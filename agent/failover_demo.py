"""Self-running failover demo (Week 5) — no LLM, no API key, no dora required.

Builds the full 3-layer stack over two *deterministic* providers — a flaky
primary that fails its first couple of calls, and a reliable backup — then runs
the `Agent` loop through it. The point is to watch the stack absorb the failures
and still deliver a final answer, then print the routing/retry metrics.

    python -m agent.failover_demo

The flaky provider stands in for a real OpenAI endpoint having a bad minute; the
backup stands in for a second model/key. In production both inner providers are
`OpenAIProvider`s — the wiring is identical.
"""

from __future__ import annotations

from .agent import Agent, AgentConfig
from .failover import AdaptiveRouter, ProviderChain, RetryProvider
from .provider import ChatConfig, ChatResponse, LlmProvider, MockProvider
from .tool import FunctionTool, ToolRegistry, ToolResult


class FlakyProvider(LlmProvider):
    """Deterministic provider that raises on its first `fail_times` calls.

    After that it delegates to a scripted `MockProvider`. No randomness, so the
    demo (and the tests) are fully reproducible.
    """

    def __init__(self, script: list[ChatResponse], fail_times: int, name: str = "flaky"):
        self._inner = MockProvider(script, name=name)
        self._fail_times = fail_times
        self._name = name
        self._calls = 0

    def name(self) -> str:
        return self._name

    def chat(self, messages, tools, config: ChatConfig) -> ChatResponse:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise RuntimeError(f"{self._name}: simulated endpoint error #{self._calls}")
        return self._inner.chat(messages, tools, config)

    def export_metrics(self) -> dict:
        return {"provider": self._name, "calls": self._calls}


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry(max_active=15)
    registry.register(FunctionTool(
        name="dora_read",
        description="Read the latest value of a dataflow input by id.",
        input_schema={
            "type": "object",
            "properties": {"input_id": {"type": "string"}},
            "required": ["input_id"],
        },
        func=lambda args: ToolResult(output='{"joint_positions": [0, 0, 0, 0, 0, 0]}'),
        tags=["transport", "read"],
    ))
    registry.set_base_tools(["dora_read"])
    return registry


def _script() -> list[ChatResponse]:
    return [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("dora_read", {"input_id": "joint_positions"})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="Arm is at the zero configuration.", finish_reason="stop"),
    ]


def main() -> None:
    # Primary fails its first two calls, then works; wrap it in retry+backoff.
    primary = RetryProvider(
        FlakyProvider(_script(), fail_times=2, name="primary"),
        max_retries=3,
        base_delay=0.0,  # no real sleeping in the demo
    )
    # A reliable backup in case the primary exhausts its retries entirely.
    backup = MockProvider(_script(), name="backup")

    # Layer 2 (chain) inside Layer 3 (adaptive routing).
    provider = AdaptiveRouter([ProviderChain([primary, backup])])

    agent = Agent(provider, _build_registry(), AgentConfig(max_iterations=10))
    print(">>> Provider stack:", provider.name())
    final = agent.process_message("What configuration is the arm in?", verbose=True)

    print(f"\nFinal response: {final}")
    print("Stack metrics:", provider.export_metrics())


if __name__ == "__main__":
    main()
