"""Week 11 — the collision-aware planner routes around obstacles, fails on a
blocked goal, and drives real task-level recovery.

These need the dora-moveit2 planner importable (the RRT-Connect search), so they
skip on a bare checkout. The collision check itself is this repo's — the point is
that a real path search now respects it.
"""

import json
from collections import deque

import numpy as np
import pyarrow as pa
import pytest

from agent.agent import Agent, AgentConfig
from agent.bridge import DoraAgentBridge
from agent.motion_tools import build_full_registry
from agent.provider import ChatResponse, MockProvider, ReactiveMockProvider
from simulation.named_poses import NAMED_POSES
from simulation.rrt_planner_node import (
    load_collision_planner,
    load_planner,
    plan_path,
    resolve_config,
)
from simulation.ur5e_collision import config_in_collision, make_box
from simulation.ur5e_kinematics import link_positions


def _dora_moveit_available() -> bool:
    try:
        load_planner()
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _dora_moveit_available(),
    reason="dora-moveit2 planner not importable (set DORA_MOVEIT2_PATH)",
)

HOME = list(NAMED_POSES["home"])


def _blocking_box_between(start_name, goal_name):
    """A box on the straight-line midpoint that the endpoints stay clear of."""
    start = np.array(NAMED_POSES[start_name])
    goal = np.array(NAMED_POSES[goal_name])
    mid = start + 0.5 * (goal - start)
    return make_box("pillar", link_positions(mid)["wrist_3_link"], [0.08, 0.08, 0.4])


def _edges_collision_free(traj, obstacles, n=8):
    for a, b in zip(traj[:-1], traj[1:]):
        a, b = np.array(a), np.array(b)
        for t in np.linspace(0.0, 1.0, n):
            if config_in_collision(a + t * (b - a), obstacles):
                return False
    return True


def test_planner_routes_around_an_obstacle():
    np.random.seed(0)
    box = _blocking_box_between("home", "above_ball")
    home, goal = resolve_config("home"), resolve_config("above_ball")

    # A straight line between the poses would hit the box...
    straight_hits = any(
        config_in_collision(np.array(home) + t * (np.array(goal) - np.array(home)), [box])
        for t in np.linspace(0.0, 1.0, 21))
    assert straight_hits, "test box should block the direct path"

    planner, PR, PT = load_collision_planner(obstacles=[box])
    traj, status = plan_path(planner, PR, PT, home, goal, max_time=5.0)

    assert status["success"], "planner should find a way around the obstacle"
    assert _edges_collision_free(traj, [box]), "planned path must be collision-free"


def test_goal_in_collision_fails_to_plan():
    goal = resolve_config("above_ball")
    cage = make_box("cage", link_positions(goal)["wrist_3_link"], [0.12, 0.12, 0.12])
    planner, PR, PT = load_collision_planner(obstacles=[cage])

    traj, status = plan_path(planner, PR, PT, resolve_config("home"), goal, max_time=1.0)

    assert status["success"] is False
    assert "collision" in status["message"]
    assert traj == []


def test_collision_checking_is_what_rejects_the_blocked_goal():
    """Contrast: the stubbed planner happily 'plans' into the blocked goal; only
    the collision-aware planner rejects it. This isolates what Week 11 added."""
    goal = resolve_config("above_ball")
    cage = make_box("cage", link_positions(goal)["wrist_3_link"], [0.12, 0.12, 0.12])

    np.random.seed(0)
    plain, PR, PT = load_planner()
    _, plain_status = plan_path(plain, PR, PT, resolve_config("home"), goal, max_time=1.0)
    assert plain_status["success"] is True  # no collision checking -> accepts it

    aware, PR2, PT2 = load_collision_planner(obstacles=[cage])
    _, aware_status = plan_path(aware, PR2, PT2, resolve_config("home"), goal, max_time=1.0)
    assert aware_status["success"] is False


# --- real-collision task recovery ----------------------------------------

def _json_value(obj):
    return pa.array(json.dumps(obj).encode("utf-8"))


class CollisionRecoveryNode:
    """Routes plan_requests through the real collision-aware planner. An obstacle
    sits on the primary approach, so that goal genuinely fails to plan; the
    alternate approach is clear."""

    def __init__(self, blocked_pose, obstacle):
        self._queue = deque()
        self.sent = []
        self.plan_goals = []
        self._planner, self._PR, self._PT = load_collision_planner(obstacles=[obstacle])
        self._queue.append({"type": "INPUT", "id": "joint_positions",
                            "value": pa.array([0.0] * 7 + HOME)})

    def next(self, timeout=0.0):
        return self._queue.popleft() if self._queue else None

    def send_output(self, output_id, array, metadata=None):
        self.sent.append((output_id, array))
        if output_id != "plan_request":
            return
        req = json.loads(bytes(array.to_pylist()).decode("utf-8"))
        self.plan_goals.append(req["goal"])
        _, status = plan_path(self._planner, self._PR, self._PT,
                              req["start"], req["goal"], max_time=1.0)
        self._queue.append({"type": "INPUT", "id": "plan_status",
                            "value": _json_value(status)})
        if status["success"]:
            self._queue.append({"type": "INPUT", "id": "execution_status",
                                "value": _json_value({"status": "completed"})})


def _call(name, args):
    return ChatResponse(tool_calls=[MockProvider.tool_call(name, args)],
                        finish_reason="tool_calls")


def test_agent_recovers_when_a_grasp_is_genuinely_blocked():
    """End to end: the primary approach fails with a *real* collision error, and
    the agent recovers by approaching from a clear pose."""
    primary, alternate = "above_ball", "upright"
    obstacle = make_box("cage", link_positions(NAMED_POSES[primary])["wrist_3_link"],
                        [0.12, 0.12, 0.12])

    def _decide(messages):
        last = messages[-1]
        if last["role"] == "user":
            return _call("dora_move", {"target": primary})       # doomed first try
        if last["role"] == "tool":
            content = last["content"]
            if '"error"' in content and "collision" in content:
                return _call("dora_move", {"target": alternate})  # recover
            data = json.loads(content) if content.startswith("{") else {}
            if data.get("attempts") or data.get("status") == "completed":
                return ChatResponse(content="Reached it from another angle.",
                                    finish_reason="stop")
            return _call("dora_move", {"target": primary})
        return ChatResponse(content="done", finish_reason="stop")

    node = CollisionRecoveryNode(primary, obstacle)
    bridge = DoraAgentBridge(node, poll_secs=0.0)
    agent = Agent(ReactiveMockProvider(_decide), build_full_registry(bridge),
                  AgentConfig(max_iterations=12))

    final = agent.process_message("Pick up the ball.")

    assert final == "Reached it from another angle."
    # the blocked pose was really attempted and retried before the agent gave up...
    assert node.plan_goals.count(list(NAMED_POSES[primary])) >= 2
    # ...and the clear alternate planned successfully.
    assert list(NAMED_POSES[alternate]) in node.plan_goals
