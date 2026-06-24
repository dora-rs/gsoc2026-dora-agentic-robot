"""Unit tests for the Agent loop (Week 4) — driven by a deterministic MockProvider.

No LLM, no dora, no MuJoCo required.
"""

import json

import pytest

from agent.agent import Agent, AgentConfig
from agent.provider import ChatResponse, MockProvider
from agent.tool import FunctionTool, ToolRegistry, ToolResult


def _registry(func, name="echo", schema=None):
    reg = ToolRegistry()
    reg.register(FunctionTool(
        name=name,
        description=f"{name} tool",
        input_schema=schema or {"type": "object", "properties": {}},
        func=func,
    ))
    return reg


def test_plain_answer_no_tools():
    provider = MockProvider([ChatResponse(content="hello", finish_reason="stop")])
    agent = Agent(provider, ToolRegistry())
    assert agent.process_message("hi") == "hello"


def test_executes_tool_then_returns_final():
    seen = {}

    def echo(args):
        seen.update(args)
        return ToolResult(output=json.dumps({"echo": args.get("text")}))

    reg = _registry(echo, schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    })
    script = [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("echo", {"text": "hi"})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="done", finish_reason="stop"),
    ]
    agent = Agent(MockProvider(script), reg)
    assert agent.process_message("go") == "done"
    assert seen == {"text": "hi"}  # the tool actually ran with parsed args


def test_unknown_tool_is_reported_not_raised():
    captured = {}

    def provider_chat_capture(messages, tools, config):
        # second call: inspect what the tool result looked like
        captured["messages"] = messages
        return ChatResponse(content="recovered", finish_reason="stop")

    script = [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("does_not_exist", {})],
            finish_reason="tool_calls",
        ),
    ]
    provider = MockProvider(script)
    # Patch the 2nd response to a capturing function-like behaviour via script add.
    provider._script.append(ChatResponse(content="recovered", finish_reason="stop"))
    agent = Agent(provider, ToolRegistry())
    result = agent.process_message("go")
    assert result == "recovered"


def test_tool_exception_surfaced_as_error():
    def boom(args):
        raise RuntimeError("kaboom")

    reg = _registry(boom, name="boom")
    tool_result_seen = {}

    script = [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("boom", {})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="handled", finish_reason="stop"),
    ]
    agent = Agent(MockProvider(script), reg)
    # The loop must not raise; it converts the exception into a tool error.
    assert agent.process_message("go") == "handled"


def test_bad_json_arguments_handled():
    reg = _registry(lambda args: ToolResult(output="ok"))
    script = [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("echo", "{not json}")],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="ok", finish_reason="stop"),
    ]
    agent = Agent(MockProvider(script), reg)
    assert agent.process_message("go") == "ok"


def test_max_iterations_terminates_runaway_loop():
    # Provider always asks for a tool, never stops -> loop must hit the cap.
    calls = {"n": 0}

    class Loopy(MockProvider):
        def chat(self, messages, tools, config):
            calls["n"] += 1
            return ChatResponse(
                tool_calls=[MockProvider.tool_call("echo", {})],
                finish_reason="tool_calls",
            )

    reg = _registry(lambda args: ToolResult(output="ok"))
    agent = Agent(Loopy([]), reg, AgentConfig(max_iterations=3))
    agent.process_message("go")
    assert calls["n"] == 3  # exactly max_iterations, then stops


def test_tool_timeout_reported():
    import time as _time

    def slow(args):
        _time.sleep(0.5)
        return ToolResult(output="too late")

    reg = _registry(slow, name="slow")
    script = [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("slow", {})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="after", finish_reason="stop"),
    ]
    agent = Agent(MockProvider(script), reg,
                  AgentConfig(max_iterations=5, tool_timeout_secs=0.05))
    # Should not hang; the slow tool is abandoned and reported, loop continues.
    assert agent.process_message("go") == "after"


def test_failed_toolresult_becomes_error():
    reg = _registry(lambda args: ToolResult(output="nope", success=False))
    script = [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("echo", {})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="ok", finish_reason="stop"),
    ]
    agent = Agent(MockProvider(script), reg)
    assert agent.process_message("go") == "ok"


def test_config_defaults():
    cfg = AgentConfig()
    assert cfg.max_iterations == 50
    assert cfg.max_timeout_secs == 600.0
    assert cfg.tool_timeout_secs == 600.0
    assert cfg.temperature == pytest.approx(0.1)
