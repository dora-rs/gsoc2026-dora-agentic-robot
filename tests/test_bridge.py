"""Unit tests for the Week 6 dora bridge — fake node, no dora/MuJoCo/LLM."""

import json
from collections import deque

import pyarrow as pa

from agent.agent import Agent, AgentConfig
from agent.bridge import (
    DoraAgentBridge,
    DoraCallTool,
    DoraReadTool,
    DoraSendTool,
    build_bridge_registry,
    compose_system_prompt,
)
from agent.provider import ChatResponse, MockProvider
from agent.skills import SkillInfo

HOME = [-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]


def _json_value(obj):
    return pa.array(json.dumps(obj).encode("utf-8"))


class FakeNode:
    """Serves scripted INPUT events; auto-replies to plan_request/gripper_command."""

    def __init__(self):
        self._queue = deque()
        self.sent = []

    def push_input(self, input_id, value):
        self._queue.append({"type": "INPUT", "id": input_id, "value": value})

    def next(self, timeout=0.0):
        return self._queue.popleft() if self._queue else None

    def send_output(self, output_id, array, metadata=None):
        self.sent.append((output_id, array))
        if output_id == "plan_request":
            self.push_input("plan_status", _json_value({"success": True}))
        elif output_id == "gripper_command":
            self.push_input("gripper_status", _json_value({"state": "closed"}))


def _bridge(poll_secs=0.0):
    return DoraAgentBridge(FakeNode(), poll_secs=poll_secs)


# --- ingestion / cache ----------------------------------------------------

def test_ingest_joint_positions_slices_arm():
    b = _bridge()
    b.ingest({"type": "INPUT", "id": "joint_positions",
              "value": pa.array([0.0] * 7 + HOME)})
    data = b.read_cached("joint_positions")["data"]
    assert data == HOME  # free-joint qpos stripped, 6 arm joints remain


def test_ingest_json_input():
    b = _bridge()
    b.ingest({"type": "INPUT", "id": "plan_status", "value": _json_value({"success": True})})
    assert b.read_cached("plan_status")["data"] == {"success": True}


def test_read_cached_missing_is_error():
    assert "error" in _bridge().read_cached("nope")


def test_ingest_bad_bytes_does_not_raise():
    b = _bridge()
    b.ingest({"type": "INPUT", "id": "gripper_status", "value": pa.array([1, 2, 3])})
    # Non-UTF8/non-JSON falls back to hex, still cached, no exception.
    assert "gripper_status" in b.cache


def test_send_json_encodes_and_sends():
    node = FakeNode()
    b = DoraAgentBridge(node, poll_secs=0.0)
    b.send_json("scene_command", {"action": "add"})
    oid, arr = node.sent[0]
    assert oid == "scene_command"
    assert json.loads(bytes(arr.to_pylist()).decode("utf-8")) == {"action": "add"}


# --- tools ----------------------------------------------------------------

def test_read_tool_returns_cached():
    b = _bridge()
    b.ingest({"type": "INPUT", "id": "joint_positions", "value": pa.array([0.0] * 7 + HOME)})
    out = json.loads(DoraReadTool(b).execute({"input_id": "joint_positions"}).output)
    assert out["data"] == HOME
    assert "age_ms" in out


def test_send_tool_fire_and_forget():
    b = _bridge()
    res = DoraSendTool(b).execute({"output_id": "scene_command", "data": {"x": 1}})
    assert json.loads(res.output) == {"success": True, "sent_to": "scene_command"}


def test_send_tool_requires_output_id():
    res = DoraSendTool(_bridge()).execute({"data": {}})
    assert res.success is False
    assert "error" in json.loads(res.output)


def test_call_tool_resolves_named_pose_and_autoinjects_start():
    node = FakeNode()
    b = DoraAgentBridge(node, poll_secs=0.0)
    b.ingest({"type": "INPUT", "id": "joint_positions", "value": pa.array([0.0] * 7 + HOME)})
    res = DoraCallTool(b).execute({
        "output_id": "plan_request",
        "data": {"goal": "home"},
        "response_id": "plan_status",
    })
    # The sent plan_request has the named pose resolved and start auto-injected.
    sent_oid, sent_arr = node.sent[0]
    sent = json.loads(bytes(sent_arr.to_pylist()).decode("utf-8"))
    assert sent["goal"] == HOME
    assert sent["start"] == HOME
    assert json.loads(res.output)["response"] == {"success": True}


def test_call_tool_times_out_cleanly():
    res = DoraCallTool(_bridge()).execute({
        "output_id": "gripper_command",  # FakeNode replies on gripper_status...
        "data": {"action": "open"},
        "response_id": "never_arrives",  # ...but we wait on the wrong id
        "timeout_secs": 0.05,
    })
    assert res.success is False
    assert "timed out" in json.loads(res.output)["error"]


# --- assembly -------------------------------------------------------------

def test_build_bridge_registry_pins_base_tools():
    reg = build_bridge_registry(_bridge())
    assert reg.base_tools() == ["dora_call", "dora_read", "dora_send"]
    assert set(reg.names()) == {"dora_read", "dora_send", "dora_call"}


def test_compose_system_prompt_appends_always_skills():
    skills = [
        SkillInfo("s1", "", "1.0", "", True, "always body", "p1"),
        SkillInfo("s2", "", "1.0", "", False, "optional body", "p2"),
    ]
    prompt = compose_system_prompt("BASE", skills)
    assert "BASE" in prompt and "always body" in prompt
    assert "optional body" not in prompt  # only always-on skills injected


def test_compose_system_prompt_no_skills_is_base():
    assert compose_system_prompt("BASE", []) == "BASE"


# --- end to end through the Agent loop ------------------------------------

def test_agent_drives_bridge_end_to_end():
    node = FakeNode()
    node.push_input("joint_positions", pa.array([0.0] * 7 + HOME))
    bridge = DoraAgentBridge(node, poll_secs=0.05)
    registry = build_bridge_registry(bridge)

    script = [
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
        ChatResponse(content="done", finish_reason="stop"),
    ]
    agent = Agent(MockProvider(script), registry, AgentConfig(max_iterations=10))
    assert agent.process_message("go home") == "done"
    assert "plan_request" in [oid for oid, _ in node.sent]
