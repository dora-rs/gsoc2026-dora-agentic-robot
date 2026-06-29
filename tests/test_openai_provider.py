"""Unit tests for the Week 5 OpenAIProvider — response translation + token metrics.

A fake client is injected, so these exercise the OpenAI chat-completions
translation (content, tool_calls, finish_reason, token accounting) with no
network call and without the `openai` package installed.
"""

from types import SimpleNamespace

from agent.provider import ChatConfig, OpenAIProvider


class _FakeCompletions:
    def __init__(self, response, recorder):
        self._response = response
        self._recorder = recorder

    def create(self, **kwargs):
        self._recorder.append(kwargs)
        return self._response


class _FakeClient:
    """Mimics the shape OpenAIProvider uses: client.chat.completions.create(...)."""

    def __init__(self, response):
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=_FakeCompletions(response, self.calls))


def _response(content=None, tool_calls=None, finish_reason="stop", total_tokens=0):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(total_tokens=total_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_translates_plain_text_response():
    client = _FakeClient(_response(content="hello", total_tokens=12))
    provider = OpenAIProvider(client=client)
    resp = provider.chat([{"role": "user", "content": "hi"}], [], ChatConfig())
    assert resp.content == "hello"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


def test_translates_tool_calls():
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="dora_read", arguments='{"input_id": "x"}'),
    )
    client = _FakeClient(_response(tool_calls=[tc], finish_reason="tool_calls"))
    provider = OpenAIProvider(client=client)
    resp = provider.chat([], [], ChatConfig())
    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls == [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "dora_read", "arguments": '{"input_id": "x"}'},
    }]


def test_accumulates_token_usage_across_calls():
    client = _FakeClient(_response(content="x", total_tokens=10))
    provider = OpenAIProvider(client=client)
    provider.chat([], [], ChatConfig())
    provider.chat([], [], ChatConfig())
    metrics = provider.export_metrics()
    assert metrics["total_tokens"] == 20
    assert metrics["calls"] == 2
    assert metrics["model"] == "gpt-4o"


def test_passes_tools_and_temperature_through():
    client = _FakeClient(_response(content="x"))
    provider = OpenAIProvider(model="gpt-4o-mini", client=client)
    tools = [{"type": "function", "function": {"name": "t"}}]
    provider.chat([], tools, ChatConfig(temperature=0.7, max_tokens=256))
    sent = client.calls[0]
    assert sent["model"] == "gpt-4o-mini"
    assert sent["temperature"] == 0.7
    assert sent["max_tokens"] == 256
    assert sent["tools"] == tools
    assert sent["tool_choice"] == "auto"


def test_omits_tools_key_when_no_tools():
    client = _FakeClient(_response(content="x"))
    provider = OpenAIProvider(client=client)
    provider.chat([], [], ChatConfig())
    assert "tools" not in client.calls[0]


def test_name_includes_model():
    provider = OpenAIProvider(model="gpt-4o", client=_FakeClient(_response()))
    assert provider.name() == "openai/gpt-4o"


def test_report_late_failure_counted():
    provider = OpenAIProvider(client=_FakeClient(_response()))
    provider.report_late_failure("bad json")
    assert provider.export_metrics()["late_failures"] == 1
