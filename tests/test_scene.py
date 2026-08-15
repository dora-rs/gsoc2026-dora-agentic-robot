"""Week 12 — runtime scene: scene_command parsing, the dora_obstacle tool, and
the agent routing a plan around an obstacle it registered.

The pure parts (scene command handling, the tool wiring) run anywhere; the
end-to-end "plan avoids the obstacle" test needs the dora-moveit2 planner and
skips without it.
"""

import json
from collections import deque

import numpy as np
import pyarrow as pa
import pytest

from agent.bridge import DoraAgentBridge
from agent.motion_tools import DoraObstacleTool, build_full_registry
from simulation.rrt_planner_node import apply_scene_command


# --- scene_command parsing (pure) ----------------------------------------

def test_apply_scene_command_add_remove_clear():
    obstacles = []
    msg = apply_scene_command(obstacles, {"action": "add", "object": {
        "name": "box1", "type": "box", "position": [0.3, 0, 0.3],
        "half_extents": [0.05, 0.05, 0.05]}})
    assert "added" in msg and len(obstacles) == 1

    # adding the same name replaces rather than duplicates (idempotent)
    apply_scene_command(obstacles, {"action": "add", "object": {
        "name": "box1", "type": "sphere", "position": [0.4, 0, 0.3], "radius": 0.05}})
    assert len(obstacles) == 1 and obstacles[0]["type"] == "sphere"

    apply_scene_command(obstacles, {"action": "remove", "name": "box1"})
    assert obstacles == []

    obstacles.append({"name": "x"})
    assert "cleared 1" in apply_scene_command(obstacles, {"action": "clear"})
    assert obstacles == []


def test_apply_scene_command_rejects_bad_obstacle():
    obstacles = []
    msg = apply_scene_command(obstacles, {"action": "add",
                                          "object": {"name": "x", "type": "blob",
                                                     "position": [0, 0, 0]}})
    assert "rejected" in msg and obstacles == []


# --- dora_obstacle tool ---------------------------------------------------

class _SceneNode:
    """Applies scene_command to a live obstacle list and acks scene_result."""

    def __init__(self):
        self._queue = deque()
        self.sent = []
        self.obstacles = []

    def next(self, timeout=0.0):
        return self._queue.popleft() if self._queue else None

    def send_output(self, output_id, array, metadata=None):
        self.sent.append((output_id, array))
        if output_id != "scene_command":
            return
        cmd = json.loads(bytes(array.to_pylist()).decode("utf-8"))
        result = apply_scene_command(self.obstacles, cmd)
        self._queue.append({"type": "INPUT", "id": "scene_result",
                            "value": pa.array(json.dumps(
                                {"result": result,
                                 "obstacles": len(self.obstacles)}).encode())})


def test_dora_obstacle_tool_registers_an_obstacle():
    node = _SceneNode()
    tool = DoraObstacleTool(DoraAgentBridge(node, poll_secs=0.0))

    out = json.loads(tool.execute({"action": "add", "name": "wall", "shape": "box",
                                   "position": [0.3, 0.0, 0.3],
                                   "half_extents": [0.05, 0.05, 0.2]}).output)
    assert out["scene_result"]["obstacles"] == 1
    assert node.obstacles[0]["name"] == "wall"


def test_dora_obstacle_tool_validates_arguments():
    tool = DoraObstacleTool(DoraAgentBridge(_SceneNode(), poll_secs=0.0))
    assert "error" in json.loads(tool.execute({"action": "spin"}).output)
    assert "error" in json.loads(
        tool.execute({"action": "add", "shape": "pyramid",
                      "position": [0, 0, 0]}).output)


# --- end to end: the agent avoids an obstacle it registered ---------------

def _dora_moveit_available() -> bool:
    from simulation.rrt_planner_node import load_planner
    try:
        load_planner()
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _dora_moveit_available(),
                    reason="dora-moveit2 planner not importable")
def test_agent_registers_an_obstacle_and_plans_around_it():
    from agent.live_planner_demo import PlannerBackedNode
    from simulation.named_poses import NAMED_POSES
    from simulation.ur5e_collision import config_in_collision
    from simulation.ur5e_kinematics import link_positions

    np.random.seed(0)
    node = PlannerBackedNode()
    registry = build_full_registry(DoraAgentBridge(node, poll_secs=0.0))

    # a box on the straight-line midpoint between home and above_ball
    mid = np.array(NAMED_POSES["home"]) + 0.5 * (
        np.array(NAMED_POSES["above_ball"]) - np.array(NAMED_POSES["home"]))
    pos = list(link_positions(mid)["wrist_3_link"])
    registry.get("dora_obstacle").execute({
        "action": "add", "name": "pillar", "shape": "box", "position": pos,
        "half_extents": [0.08, 0.08, 0.4]})
    assert node.obstacles and node.obstacles[0]["name"] == "pillar"

    out = json.loads(registry.get("dora_move").execute({"target": "above_ball"}).output)
    assert out["plan_status"]["success"], "planner should route around the obstacle"

    # the executed path (as the node applied it) ended at the goal, and the plan
    # was multi-waypoint — the detour around the pillar.
    assert out["plan_status"]["num_waypoints"] >= 2
    # and the goal pose itself is clear of the obstacle (grasp still reachable)
    assert not config_in_collision(NAMED_POSES["above_ball"], node.obstacles)
