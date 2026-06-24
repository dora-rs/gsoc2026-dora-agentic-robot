"""Self-running agent-core demo (Week 4) — no LLM, no dora required.

Wires a `MockProvider` (scripted to read a joint state, then answer) against a
`ToolRegistry` holding two example tools, and runs the `Agent` loop end to end.
This exercises the whole Week 4 surface — registry → schema export → loop → tool
execution → result feedback — purely in-process.

    python -m agent.agent_demo

The scripted provider stands in for the real LLM that arrives in Week 5; the two
example tools stand in for the dora transport tools that arrive in Week 7.
"""

from __future__ import annotations

import json

from .agent import Agent, AgentConfig
from .provider import ChatResponse, MockProvider
from .tool import FunctionTool, ToolRegistry, ToolResult

# A tiny in-memory "sensor cache" the example tools read/write.
_FAKE_STATE = {
    "joint_positions": [0.0, -1.57, 1.57, -1.57, -1.57, 0.0],
}


def _read(args: dict) -> ToolResult:
    key = args.get("input_id", "")
    if key not in _FAKE_STATE:
        return ToolResult(output=f"no such input: {key}", success=False)
    return ToolResult(output=json.dumps({key: _FAKE_STATE[key]}))


def _send(args: dict) -> ToolResult:
    return ToolResult(output=json.dumps({"sent": args.get("output_id", ""), "ok": True}))


def build_registry() -> ToolRegistry:
    registry = ToolRegistry(max_active=15)
    registry.register(FunctionTool(
        name="dora_read",
        description="Read the latest value of a dataflow input by id.",
        input_schema={
            "type": "object",
            "properties": {"input_id": {"type": "string"}},
            "required": ["input_id"],
        },
        func=_read,
        tags=["transport", "read"],
    ))
    registry.register(FunctionTool(
        name="dora_send",
        description="Send a fire-and-forget command on a dataflow output.",
        input_schema={
            "type": "object",
            "properties": {"output_id": {"type": "string"}, "data": {"type": "object"}},
            "required": ["output_id"],
        },
        func=_send,
        tags=["transport", "write"],
    ))
    registry.set_base_tools(["dora_read", "dora_send"])
    return registry


def main() -> None:
    registry = build_registry()

    # Scripted "LLM": call dora_read, then report the result and stop.
    script = [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("dora_read", {"input_id": "joint_positions"})],
            finish_reason="tool_calls",
        ),
        ChatResponse(
            content="Read joint_positions; the arm is at its home configuration.",
            finish_reason="stop",
        ),
    ]
    agent = Agent(MockProvider(script), registry, AgentConfig(max_iterations=10))
    final = agent.process_message("What configuration is the arm in?", verbose=True)
    print(f"\nFinal response: {final}")


if __name__ == "__main__":
    main()
