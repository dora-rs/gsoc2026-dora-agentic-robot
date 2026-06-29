"""Unit tests for the Week 5 3-layer failover stack.

All deterministic — a flaky `LlmProvider` double that fails a fixed number of
calls then succeeds. No network, no API key, no sleeping (base_delay=0).
"""

import pytest

from agent.agent import Agent, AgentConfig
from agent.failover import AdaptiveRouter, ProviderChain, RetryProvider
from agent.provider import ChatConfig, ChatResponse, LlmProvider, MockProvider
from agent.tool import FunctionTool, ToolRegistry, ToolResult


class _Flaky(LlmProvider):
    """Raises on the first `fail_times` calls, then returns `response`."""

    def __init__(self, fail_times, response=None, name="flaky"):
        self._fail_times = fail_times
        self._response = response or ChatResponse(content="ok", finish_reason="stop")
        self._name = name
        self.calls = 0

    def name(self):
        return self._name

    def chat(self, messages, tools, config):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError(f"{self._name} boom #{self.calls}")
        return self._response


_CFG = ChatConfig()


# --- RetryProvider --------------------------------------------------------

def test_retry_succeeds_after_transient_failures():
    inner = _Flaky(fail_times=2, response=ChatResponse(content="recovered"))
    provider = RetryProvider(inner, max_retries=3, base_delay=0.0)
    resp = provider.chat([], [], _CFG)
    assert resp.content == "recovered"
    assert inner.calls == 3  # 2 failures + 1 success
    assert provider.export_metrics()["retries"] == 2


def test_retry_reraises_after_exhausting_attempts():
    inner = _Flaky(fail_times=99)
    provider = RetryProvider(inner, max_retries=2, base_delay=0.0)
    with pytest.raises(RuntimeError):
        provider.chat([], [], _CFG)
    assert inner.calls == 3  # initial + 2 retries


def test_retry_name_wraps_inner():
    provider = RetryProvider(MockProvider([], name="m"), base_delay=0.0)
    assert provider.name() == "retry(m)"


# --- ProviderChain --------------------------------------------------------

def test_chain_falls_through_to_next_provider():
    bad = _Flaky(fail_times=99, name="bad")
    good = _Flaky(fail_times=0, response=ChatResponse(content="from-good"), name="good")
    chain = ProviderChain([bad, good])
    resp = chain.chat([], [], _CFG)
    assert resp.content == "from-good"
    assert chain.export_metrics()["failovers"] == 1
    assert chain.export_metrics()["active_provider"] == "good"


def test_chain_raises_when_all_fail():
    chain = ProviderChain([_Flaky(99, name="a"), _Flaky(99, name="b")])
    with pytest.raises(RuntimeError, match="all providers in chain failed"):
        chain.chat([], [], _CFG)


def test_chain_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        ProviderChain([])


def test_chain_no_failover_counted_when_first_works():
    good = _Flaky(0, response=ChatResponse(content="ok"), name="good")
    chain = ProviderChain([good])
    chain.chat([], [], _CFG)
    assert chain.export_metrics()["failovers"] == 0


# --- AdaptiveRouter -------------------------------------------------------

def test_router_prefers_first_provider_when_scores_tied():
    a = _Flaky(0, response=ChatResponse(content="a"), name="a")
    b = _Flaky(0, response=ChatResponse(content="b"), name="b")
    router = AdaptiveRouter([a, b])
    assert router.chat([], [], _CFG).content == "a"
    assert a.calls == 1 and b.calls == 0


def test_router_routes_away_from_failing_provider():
    bad = _Flaky(fail_times=99, name="bad")
    good = _Flaky(fail_times=0, response=ChatResponse(content="good"), name="good")
    router = AdaptiveRouter([bad, good])
    # First call: 'bad' chosen (tie -> first), fails, falls through to 'good'.
    assert router.chat([], [], _CFG).content == "good"
    # Second call: 'bad' now has a worse score, so 'good' is chosen directly.
    router.chat([], [], _CFG)
    assert good.calls == 2
    assert router.export_metrics()["last_used"] == "good"


def test_router_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        AdaptiveRouter([])


def test_router_raises_when_all_fail():
    router = AdaptiveRouter([_Flaky(99, name="a"), _Flaky(99, name="b")])
    with pytest.raises(RuntimeError):
        router.chat([], [], _CFG)


def test_router_late_failure_penalises_last_used():
    a = _Flaky(0, response=ChatResponse(content="a"), name="a")
    router = AdaptiveRouter([a])
    router.chat([], [], _CFG)
    router.report_late_failure("bad json")
    assert router.export_metrics()["provider_stats"]["a"]["error_rate"] == 1.0


# --- Full stack through the Agent loop ------------------------------------

def test_full_stack_drives_agent_loop():
    """RetryProvider -> ProviderChain -> AdaptiveRouter, end to end via Agent."""
    script = [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("echo", {})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="done", finish_reason="stop"),
    ]

    class _FlakyScripted(LlmProvider):
        def __init__(self):
            self._inner = MockProvider(script, name="primary")
            self.calls = 0

        def name(self):
            return "primary"

        def chat(self, messages, tools, config):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("primary cold start")
            return self._inner.chat(messages, tools, config)

    primary = RetryProvider(_FlakyScripted(), max_retries=3, base_delay=0.0)
    provider = AdaptiveRouter([ProviderChain([primary])])

    reg = ToolRegistry()
    reg.register(FunctionTool(
        name="echo",
        description="echo",
        input_schema={"type": "object", "properties": {}},
        func=lambda args: ToolResult(output="echoed"),
    ))
    agent = Agent(provider, reg, AgentConfig(max_iterations=5))
    assert agent.process_message("go") == "done"
    # The router's chosen branch is the single chain wrapping the retried primary.
    assert provider.export_metrics()["last_used"] == "chain(retry(primary))"
