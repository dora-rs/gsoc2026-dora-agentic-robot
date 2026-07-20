"""Pipeline stub dora node (Week 7) — planner + executor + sim, in one small node.

The full pipeline (`ur5e_agent_demo.yml`) needs dora-moveit2, MuJoCo, and fetched
meshes. That is the right target for a live demo, but it makes the *dataflow
itself* untestable in CI, so up to Week 6 the agent was only ever exercised
in-process against a fake node.

This node closes that gap. It speaks exactly the wire contract the agent bridge
expects from `planner` + `trajectory_executor` + `mujoco_sim` — accept a
`plan_request`, report `plan_status`, teleport the arm to the goal, publish the
new `joint_positions`, then report `execution_status` — with no dependency
beyond dora and pyarrow. `dataflows/ur5e_agent_loopback.yml` wires it up so a
**real dora dataflow** can run in a test (see `tests/test_integration_dataflow`).

It is a stand-in for the motion stack, not a simulator: motions complete
instantly and nothing is checked for collisions. What it validates is the part
in-process tests cannot — that the agent, the bridge, and the wire formats work
across real dora node boundaries.

Inputs:
  - tick         : timer, republishes the current state
  - plan_request : JSON {"start": [6], "goal": [6]}

Outputs:
  - joint_positions  : float64[13], full qpos (7 free-joint + 6 arm), like mujoco_sim
  - plan_status      : JSON {"success": bool, "message": str}
  - execution_status : JSON {"status": "completed", "joint_positions": [6]}
"""

from __future__ import annotations

import json
import os
import sys

import pyarrow as pa
from dora import Node

# dora spawns this file by path, so the repo is not importable by default.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulation.named_poses import NAMED_POSES, NUM_JOINTS  # noqa: E402

# Matches ur5e_scene.xml: the red ball's free joint occupies the first 7 qpos.
FREE_JOINT_QPOS = 7


def full_qpos(arm: list[float]) -> list[float]:
    """Embed a 6-joint arm configuration in a full-scene qpos vector."""
    return [0.0] * FREE_JOINT_QPOS + list(arm)


def parse_goal(request: dict) -> list[float] | None:
    """Extract the goal joint vector from a plan_request, or None if invalid."""
    goal = request.get("goal")
    if isinstance(goal, str):
        pose = NAMED_POSES.get(goal.lower().strip())
        return list(pose) if pose else None
    if isinstance(goal, (list, tuple)) and len(goal) == NUM_JOINTS:
        try:
            return [float(v) for v in goal]
        except (TypeError, ValueError):
            return None
    return None


def main() -> None:
    node = Node()
    start_pose = os.environ.get("START_POSE", "home")
    arm = list(NAMED_POSES.get(start_pose, NAMED_POSES["home"]))
    completed = 0

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "tick":
            node.send_output("joint_positions", pa.array(full_qpos(arm)))
            continue

        if event["id"] != "plan_request":
            continue

        try:
            request = json.loads(bytes(event["value"].to_pylist()).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
            node.send_output("plan_status", _json({"success": False,
                                                   "message": f"bad request: {exc}"}))
            continue

        goal = parse_goal(request if isinstance(request, dict) else {})
        if goal is None:
            node.send_output("plan_status", _json({
                "success": False, "message": f"unplannable goal: {request!r}"}))
            continue

        # Plan accepted -> execute instantly -> publish the resulting state.
        arm = goal
        completed += 1
        node.send_output("plan_status", _json({"success": True,
                                               "message": "trajectory planned"}))
        node.send_output("joint_positions", pa.array(full_qpos(arm)))
        node.send_output("execution_status", _json({
            "status": "completed", "joint_positions": arm, "motions": completed}))
        print(f"[pipeline_stub] motion {completed} -> {[round(v, 3) for v in arm]}")


def _json(obj) -> pa.Array:
    return pa.array(json.dumps(obj).encode("utf-8"))


if __name__ == "__main__":
    main()
