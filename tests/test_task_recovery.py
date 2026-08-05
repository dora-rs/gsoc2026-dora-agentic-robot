"""Task-level recovery (Week 9) — the agent re-approaches when a move can't plan.

Week 8 gave `dora_move` transparent replanning for *transient* failures. When a
goal is genuinely unreachable from where the arm is, the retries are exhausted
and the error is handed back to the agent — and the prompt/skill now tell it to
recover by approaching from a different pose. This test proves that loop
deterministically, with no API key, using `ReactiveMockProvider` to stand in for
the LLM's judgement: it reacts to the returned error by choosing a new pose.
"""

import json
from collections import deque

import pyarrow as pa

from agent.agent import Agent, AgentConfig
from agent.bridge import DoraAgentBridge
from agent.motion_tools import build_full_registry
from agent.provider import ChatResponse, MockProvider, ReactiveMockProvider
from simulation.named_poses import NAMED_POSES

HOME = list(NAMED_POSES["home"])
UNREACHABLE = "above_ball"   # the primary approach that will never plan
ALTERNATE = "upright"        # the recovery pose that does plan


def _json_value(obj):
    return pa.array(json.dumps(obj).encode("utf-8"))


class RecoveryNode:
    """Plans every goal except `bad_goal`, which it rejects persistently."""

    def __init__(self, bad_goal):
        self._queue = deque()
        self.sent = []
        self.plan_goals = []
        self._bad = list(NAMED_POSES[bad_goal])
        self._queue.append({"type": "INPUT", "id": "joint_positions",
                            "value": pa.array([0.0] * 7 + HOME)})

    def next(self, timeout=0.0):
        return self._queue.popleft() if self._queue else None

    def send_output(self, output_id, array, metadata=None):
        self.sent.append((output_id, array))
        if output_id == "plan_request":
            goal = json.loads(bytes(array.to_pylist()).decode("utf-8"))["goal"]
            self.plan_goals.append(goal)
            ok = goal != self._bad
            self._queue.append({"type": "INPUT", "id": "plan_status",
                                "value": _json_value(
                                    {"success": ok,
                                     "message": "planned" if ok else "unreachable"})})
            if ok:
                self._queue.append({"type": "INPUT", "id": "execution_status",
                                    "value": _json_value({"status": "completed"})})


def _call(name, args):
    return ChatResponse(tool_calls=[MockProvider.tool_call(name, args)],
                        finish_reason="tool_calls")


def _decide(messages):
    """Emulate an LLM: perceive, try the primary approach, and on a returned
    error, recover by approaching from a different pose."""
    last = messages[-1]
    if last["role"] == "user":
        return _call("dora_perceive", {})
    if last["role"] == "tool":
        content = last["content"]
        if '"error"' in content and "unreachable" in content:
            return _call("dora_move", {"target": ALTERNATE})   # recover
        data = json.loads(content) if content.startswith("{") else {}
        if data.get("attempts") or data.get("status") == "completed":
            return ChatResponse(content="Recovered and reached the target.",
                                finish_reason="stop")
        return _call("dora_move", {"target": UNREACHABLE})     # doomed first try
    return ChatResponse(content="done", finish_reason="stop")


def test_agent_recovers_by_approaching_from_a_different_pose():
    node = RecoveryNode(bad_goal=UNREACHABLE)
    bridge = DoraAgentBridge(node, poll_secs=0.0)
    registry = build_full_registry(bridge)
    provider = ReactiveMockProvider(_decide)
    agent = Agent(provider, registry, AgentConfig(max_iterations=12))

    final = agent.process_message("Reach the target.")

    # The task completed only because the agent changed approach after the error.
    assert final == "Recovered and reached the target."
    # The unreachable goal was really attempted (and retried) before giving up...
    assert node.plan_goals.count(list(NAMED_POSES[UNREACHABLE])) == 3  # retry budget
    # ...then the alternate pose was planned successfully.
    assert list(NAMED_POSES[ALTERNATE]) in node.plan_goals


def test_reactive_mock_reports_call_count():
    provider = ReactiveMockProvider(lambda m: ChatResponse(content="hi",
                                                           finish_reason="stop"))
    Agent(provider, build_full_registry(DoraAgentBridge(RecoveryNode(UNREACHABLE),
                                                        poll_secs=0.0))) \
        .process_message("hello")
    assert provider.export_metrics()["calls"] == 1
