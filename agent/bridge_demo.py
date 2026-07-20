"""Self-running agent-bridge demo (Week 6) — no dora daemon, no MuJoCo, no LLM.

Drives the full Week 6 surface in-process: skills are loaded and folded into the
system prompt, and a scripted `MockProvider` runs the `Agent` through the dora
transport tools against a **fake dora node**. The fake node caches a joint state
and auto-replies to `plan_request`/`gripper_command`, so you can watch the agent
read state, plan a motion, and drive the gripper end to end.

    python -m agent.bridge_demo

The fake node stands in for the real dataflow (`dataflows/ur5e_agent_demo.yml`);
the scripted provider stands in for the real OpenAI provider from Week 5.
"""

from __future__ import annotations

import json
import os
from collections import deque

import pyarrow as pa

from .agent import Agent, AgentConfig
from .bridge import DoraAgentBridge, build_bridge_registry, compose_system_prompt
from .provider import ChatResponse, MockProvider
from .skills import load_skills

_HOME = [-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]


def _json_value(obj) -> pa.Array:
    return pa.array(json.dumps(obj).encode("utf-8"))


class FakeNode:
    """In-memory stand-in for a dora `Node`.

    Serves scripted INPUT events from a queue and records outputs. To make
    `dora_call` resolve without a real pipeline, sending `plan_request` /
    `gripper_command` auto-enqueues the matching status reply.
    """

    def __init__(self):
        self._queue: deque[dict] = deque()
        self.sent: list[tuple[str, object]] = []

    def push_input(self, input_id: str, value: pa.Array) -> None:
        self._queue.append({"type": "INPUT", "id": input_id, "value": value})

    def next(self, timeout: float = 0.0):
        return self._queue.popleft() if self._queue else None

    def send_output(self, output_id: str, array, metadata=None) -> None:
        self.sent.append((output_id, array))
        if output_id == "plan_request":
            self.push_input("plan_status", _json_value({"success": True, "message": "planned"}))
        elif output_id == "gripper_command":
            self.push_input("gripper_status", _json_value({"state": "closed"}))


def _script() -> list[ChatResponse]:
    return [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("dora_read", {"input_id": "joint_positions"})],
            finish_reason="tool_calls",
        ),
        ChatResponse(
            tool_calls=[MockProvider.tool_call("dora_call", {
                "output_id": "plan_request",
                "data": {"goal": "home"},
                "response_id": "plan_status",
            })],
            finish_reason="tool_calls",
        ),
        ChatResponse(
            tool_calls=[MockProvider.tool_call("dora_call", {
                "output_id": "gripper_command",
                "data": {"action": "close"},
                "response_id": "gripper_status",
            })],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="Read the arm state, planned a motion home, and closed the gripper.",
                     finish_reason="stop"),
    ]


def main() -> None:
    node = FakeNode()
    # Seed a full qpos: 7 free-joint values (red ball) + the 6 arm joints at home.
    node.push_input("joint_positions", pa.array([0.0] * 7 + _HOME))

    bridge = DoraAgentBridge(node, poll_secs=0.05)
    registry = build_bridge_registry(bridge)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skills = load_skills(os.path.join(repo_root, "skills"))
    for skill in skills:
        print(f"[skill] {skill.name} v{skill.version} (always={skill.always})")

    system_prompt = compose_system_prompt("You control a UR5e arm via dora.", skills)
    agent = Agent(MockProvider(_script()), registry, AgentConfig(max_iterations=10),
                  system_prompt=system_prompt)

    final = agent.process_message("Move home and close the gripper.", verbose=True)

    print(f"\nFinal response: {final}")
    print("Outputs sent to dataflow:", [oid for oid, _ in node.sent])


if __name__ == "__main__":
    main()
