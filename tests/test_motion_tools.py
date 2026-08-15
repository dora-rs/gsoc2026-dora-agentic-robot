"""Unit tests for the Week 7 motion/introspection tools — fake node, no dora."""

import json
from collections import deque

import pyarrow as pa

from agent.agent import Agent, AgentConfig
from agent.bridge import DoraAgentBridge
from agent.motion_tools import (
    DoraGripperTool,
    DoraListTool,
    DoraMoveTool,
    DoraPerceiveTool,
    SkillTool,
    _nearest_pose,
    build_full_registry,
)
from agent.pick_place_demo import PickPlaceNode, run_demo
from agent.provider import ChatResponse, MockProvider
from agent.skills import SkillInfo, SkillRegistry
from simulation.named_poses import NAMED_POSES

HOME = list(NAMED_POSES["home"])
ABOVE_BALL = list(NAMED_POSES["above_ball"])


def _json_value(obj):
    return pa.array(json.dumps(obj).encode("utf-8"))


class FakeNode:
    """Replies to plan_request with plan_status + execution_status, like the pipeline."""

    def __init__(self, plan_ok=True, reply=True):
        self._queue = deque()
        self.sent = []
        self._plan_ok = plan_ok
        self._reply = reply

    def push_input(self, input_id, value):
        self._queue.append({"type": "INPUT", "id": input_id, "value": value})

    def next(self, timeout=0.0):
        return self._queue.popleft() if self._queue else None

    def send_output(self, output_id, array, metadata=None):
        self.sent.append((output_id, array))
        if not self._reply:
            return
        if output_id == "plan_request":
            self.push_input("plan_status", _json_value({"success": self._plan_ok,
                                                        "message": "planned"}))
            self.push_input("execution_status", _json_value({"status": "completed"}))
        elif output_id == "gripper_command":
            self.push_input("gripper_status", _json_value({"state": "closed"}))


def _bridge(node=None, seeded=True):
    node = node or FakeNode()
    bridge = DoraAgentBridge(node, poll_secs=0.0)
    if seeded:
        bridge.ingest({"type": "INPUT", "id": "joint_positions",
                       "value": pa.array([0.0] * 7 + HOME)})
    return bridge


def _payload(result):
    return json.loads(result.output)


# --- dora_move ------------------------------------------------------------

def test_move_to_named_pose_sends_resolved_goal():
    node = FakeNode()
    tool = DoraMoveTool(_bridge(node))
    result = tool.execute({"target": "above_ball"})

    assert result.success
    request = json.loads(bytes(node.sent[0][1].to_pylist()).decode("utf-8"))
    assert node.sent[0][0] == "plan_request"
    assert request["goal"] == ABOVE_BALL
    assert request["start"] == HOME  # auto-filled from the cached joint state


def test_move_accepts_explicit_joint_vector():
    node = FakeNode()
    result = DoraMoveTool(_bridge(node)).execute({"target": [0.0] * 6})
    assert result.success
    assert _payload(result)["goal"] == [0.0] * 6


def test_move_reports_execution_status():
    result = DoraMoveTool(_bridge()).execute({"target": "home"})
    assert _payload(result)["execution_status"] == {"status": "completed"}


def test_move_rejects_unknown_pose_without_sending():
    node = FakeNode()
    result = DoraMoveTool(_bridge(node)).execute({"target": "somewhere"})
    assert not result.success
    assert "unknown pose" in _payload(result)["error"]
    assert node.sent == []  # nothing hit the wire


def test_move_rejects_wrong_joint_count():
    result = DoraMoveTool(_bridge()).execute({"target": [0.0, 1.0]})
    assert not result.success
    assert "expected 6 joint angles" in _payload(result)["error"]


def test_move_without_joint_state_is_a_clean_error():
    result = DoraMoveTool(_bridge(seeded=False)).execute({"target": "home"})
    assert not result.success
    assert "joint_positions" in _payload(result)["error"]


def test_move_surfaces_planning_failure():
    result = DoraMoveTool(_bridge(FakeNode(plan_ok=False))).execute({"target": "home"})
    assert not result.success
    assert "planning failed" in _payload(result)["error"]


def test_move_times_out_cleanly_when_pipeline_is_silent():
    tool = DoraMoveTool(_bridge(FakeNode(reply=False)))
    result = tool.execute({"target": "home", "timeout_secs": 0.05})
    assert not result.success
    assert "timed out" in _payload(result)["error"]


# --- dora_move replanning / recovery (Week 8) -----------------------------

class FlakyNode(FakeNode):
    """Rejects the first `fail_n` plan_requests, then plans successfully.

    Models a randomised planner (RRT-Connect): the same start/goal that fails on
    an unlucky sample succeeds on a later attempt.
    """

    def __init__(self, fail_n):
        super().__init__()
        self._fail_n = fail_n
        self._seen = 0

    def send_output(self, output_id, array, metadata=None):
        self.sent.append((output_id, array))
        if output_id == "plan_request":
            self._seen += 1
            ok = self._seen > self._fail_n
            self.push_input("plan_status", _json_value(
                {"success": ok, "message": "planned" if ok else "unlucky sample"}))
            if ok:
                self.push_input("execution_status", _json_value({"status": "completed"}))


def _plan_requests(node):
    return [s for s in node.sent if s[0] == "plan_request"]


def test_move_replans_a_transient_failure_and_reports_attempts():
    node = FlakyNode(fail_n=2)  # fails twice, succeeds on the third try
    result = DoraMoveTool(_bridge(node)).execute({"target": "above_ball"})
    assert result.success
    assert _payload(result)["attempts"] == 3
    assert len(_plan_requests(node)) == 3


def test_move_succeeds_first_try_reports_one_attempt():
    result = DoraMoveTool(_bridge()).execute({"target": "home"})
    assert _payload(result)["attempts"] == 1


def test_move_gives_up_after_max_attempts_on_persistent_failure():
    node = FakeNode(plan_ok=False)
    result = DoraMoveTool(_bridge(node)).execute({"target": "home", "max_attempts": 2})
    assert not result.success
    assert "gave up after 2 attempts" in _payload(result)["error"]
    assert len(_plan_requests(node)) == 2  # exactly max_attempts plans, no more


def test_move_default_retry_budget_is_three():
    node = FakeNode(plan_ok=False)
    DoraMoveTool(_bridge(node)).execute({"target": "home"})
    assert len(_plan_requests(node)) == 3  # DEFAULT_MOVE_ATTEMPTS


# --- self-healing sensor reads (Week 9) -----------------------------------

def test_move_self_heals_joint_state_from_an_undrained_queue():
    """joint_positions queued but never drained: the tool actively waits for it
    instead of failing, so a first move works without a prior perceive/gripper."""
    node = FakeNode()
    node.push_input("joint_positions", pa.array([0.0] * 7 + HOME))  # queued, not cached
    bridge = DoraAgentBridge(node, poll_secs=0.0)  # drain() is a no-op here
    result = DoraMoveTool(bridge).execute({"target": "above_ball"})
    assert result.success
    request = json.loads(bytes(node.sent[0][1].to_pylist()).decode("utf-8"))
    assert request["start"] == HOME  # recovered the start from the waiting queue


def test_perceive_self_heals_joint_state_from_an_undrained_queue():
    node = FakeNode()
    node.push_input("joint_positions", pa.array([0.0] * 7 + HOME))
    bridge = DoraAgentBridge(node, poll_secs=0.0)
    snapshot = _payload(DoraPerceiveTool(bridge).execute({}))
    assert snapshot["joint_positions"] == HOME
    assert snapshot["nearest_named_pose"] == "home"


# --- dora_gripper ---------------------------------------------------------

def test_gripper_close_sends_command_and_waits():
    node = FakeNode()
    result = DoraGripperTool(_bridge(node)).execute({"action": "close"})
    assert result.success
    assert node.sent[0][0] == "gripper_command"
    assert _payload(result)["gripper_status"] == {"state": "closed"}


def test_gripper_rejects_unknown_action():
    node = FakeNode()
    result = DoraGripperTool(_bridge(node)).execute({"action": "wiggle"})
    assert not result.success
    assert node.sent == []


def test_gripper_times_out_cleanly():
    tool = DoraGripperTool(_bridge(FakeNode(reply=False)))
    result = tool.execute({"action": "open", "timeout_secs": 0.05})
    assert not result.success
    assert "timed out" in _payload(result)["error"]


# --- dora_perceive --------------------------------------------------------

def test_perceive_fuses_arm_gripper_and_scene():
    bridge = _bridge()
    bridge.ingest({"type": "INPUT", "id": "gripper_status",
                   "value": _json_value({"state": "open"})})
    snapshot = _payload(DoraPerceiveTool(bridge).execute({}))

    assert snapshot["joint_positions"] == HOME
    assert snapshot["nearest_named_pose"] == "home"
    assert snapshot["gripper_status"] == {"state": "open"}
    assert "red_ball" in snapshot["scene_objects"]


def test_perceive_without_state_reports_the_gap():
    snapshot = _payload(DoraPerceiveTool(_bridge(seeded=False)).execute({}))
    assert "error" in snapshot


def test_nearest_pose_returns_none_when_far_from_every_pose():
    assert _nearest_pose([0.5] * 6) is None
    assert _nearest_pose(HOME) == "home"


# --- dora_list ------------------------------------------------------------

def test_list_all_covers_every_catalogue():
    catalogue = _payload(DoraListTool(_bridge()).execute({}))
    assert {"poses", "inputs", "outputs", "objects"} <= set(catalogue)
    assert "home" in catalogue["poses"]
    assert "joint_positions" in catalogue["cached_inputs"]


def test_list_single_kind_is_scoped():
    result = _payload(DoraListTool(_bridge()).execute({"kind": "poses"}))
    assert set(result) == {"poses"}


def test_list_rejects_unknown_kind():
    result = DoraListTool(_bridge()).execute({"kind": "sandwiches"})
    assert not result.success


# --- skill tool -----------------------------------------------------------

def _skill(name, always=False):
    return SkillInfo(name=name, description=f"{name} desc", version="1.0.0",
                     author="23garyd", always=always, content=f"# {name} body",
                     path=f"/tmp/{name}/SKILL.md")


def test_skill_list_shows_active_flag():
    registry = SkillRegistry([_skill("always-on", always=True), _skill("dormant")])
    catalogue = _payload(SkillTool(registry).execute({"action": "list"}))["skills"]
    by_name = {entry["name"]: entry for entry in catalogue}
    assert by_name["always-on"]["active"] is True
    assert by_name["dormant"]["active"] is False


def test_skill_activate_returns_the_body():
    registry = SkillRegistry([_skill("dormant")])
    result = _payload(SkillTool(registry).execute({"action": "activate",
                                                   "name": "dormant"}))
    assert result["content"] == "# dormant body"
    assert registry.is_active("dormant")


def test_skill_activate_unknown_lists_what_exists():
    tool = SkillTool(SkillRegistry([_skill("dormant")]))
    result = tool.execute({"action": "activate", "name": "nope"})
    assert not result.success
    assert "dormant" in _payload(result)["error"]


def test_skill_activate_without_name_is_an_error():
    result = SkillTool(SkillRegistry()).execute({"action": "activate"})
    assert not result.success


# --- registry assembly ----------------------------------------------------

def test_full_registry_registers_transport_and_semantic_tools():
    registry = build_full_registry(_bridge(), SkillRegistry())
    assert set(registry.names()) == {
        "dora_read", "dora_send", "dora_call",
        "dora_move", "dora_gripper", "dora_perceive", "dora_list", "dora_obstacle",
        "skill",
    }


def test_semantic_tools_are_pinned_as_base_tools():
    registry = build_full_registry(_bridge(), SkillRegistry())
    assert registry.base_tools() == [
        "dora_gripper", "dora_list", "dora_move", "dora_obstacle", "dora_perceive",
        "skill",
    ]


def test_skill_tool_absent_without_a_skill_registry():
    registry = build_full_registry(_bridge())
    assert "skill" not in registry.names()


# --- end-to-end through the Agent loop ------------------------------------

def test_agent_completes_pick_and_place_against_the_stateful_node():
    node = run_demo(verbose=False)
    assert node.ball_on_plate(), f"ball ended at {node.ball_position}"
    assert not node.holding
    assert not node.gripper_closed


def test_closing_the_gripper_away_from_the_ball_grasps_nothing():
    """The demo node is strict enough that a wrong sequence actually fails."""
    node = PickPlaceNode()
    bridge = DoraAgentBridge(node, poll_secs=0.0)
    registry = build_full_registry(bridge, SkillRegistry())

    script = [
        ChatResponse(tool_calls=[MockProvider.tool_call("dora_gripper",
                                                        {"action": "close"})],
                     finish_reason="tool_calls"),
        ChatResponse(tool_calls=[MockProvider.tool_call("dora_move",
                                                        {"target": "above_plate"})],
                     finish_reason="tool_calls"),
        ChatResponse(content="done", finish_reason="stop"),
    ]
    Agent(MockProvider(script), registry, AgentConfig(max_iterations=5)) \
        .process_message("grab it")

    assert not node.holding
    assert not node.ball_on_plate()  # the ball never left its start position
