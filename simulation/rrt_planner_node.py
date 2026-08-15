"""RRT-Connect planner dora node (Week 10) — the *real* dora-moveit2 path search.

Weeks 8-9 drove the real MuJoCo arm through `sim_executor`, which is direct
joint control: it snaps the position target straight to the goal, no path search
at all. This node replaces that half of the loop with the genuine sampling-based
planner from dora-moveit2 (`OMPLPlanner`, RRT-Connect) — so the agent's
`plan_request` produces a real multi-waypoint path through joint space, which the
Week 10 trajectory executor then follows through real physics.

It imports the planner *algorithm* from the sibling dora-moveit2 checkout rather
than reimplementing it. Only the path-search half of dora-moveit2 is used here —
no OMPL/TracIK/toppra C++ deps, which is why it runs where the full example stack
does not. Its collision checker is stubbed upstream (`is_state_valid` returns
True) and its FK is GEN72-shaped, so obstacle avoidance is not yet active; the
UR5e arm is described to the planner by `simulation.ur5e_moveit_config`.

Inputs:
  - plan_request : JSON {"start": [6], "goal": [6] | name}

Outputs:
  - plan_status  : JSON {"success": bool, "message": str, "num_waypoints": int, ...}
  - trajectory   : float[N*6] flattened waypoints (row-major, N waypoints x 6 joints)

Environment:
  - DORA_MOVEIT2_PATH  : path to the dora-moveit2 `dora_moveit` package dir.
                         Defaults to the sibling checkout next to this repo.
  - ROBOT_CONFIG_MODULE : robot config for the planner. Defaults to the UR5e
                          config in this repo if unset.
  - PLANNER_MAX_TIME   : planner time budget in seconds (default 5.0).
  - PLANNER_TYPE       : rrt | rrt_connect | rrt_star | prm (default rrt_connect).
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

# The UR5e config in this repo, used unless the dataflow overrides it.
DEFAULT_ROBOT_CONFIG = "simulation.ur5e_moveit_config"

# The dora_moveit package lives in the sibling dora-moveit2 checkout.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DORA_MOVEIT2 = os.path.normpath(
    os.path.join(_REPO, "..", "dora-moveit2", "dora_moveit")
)


def dora_moveit_path() -> str:
    """Directory holding the importable `dora_moveit` package (env-overridable)."""
    return os.environ.get("DORA_MOVEIT2_PATH", DEFAULT_DORA_MOVEIT2)


def load_planner(num_joints: int = NUM_JOINTS):
    """Import the real dora-moveit2 OMPL planner and return a ready instance.

    Returns a tuple `(planner, PlanRequest, PlannerType)`. Inserts the sibling
    dora-moveit2 checkout on `sys.path` and defaults `ROBOT_CONFIG_MODULE` to the
    UR5e config so the GEN72-default planner is configured for our arm. Raises
    ImportError with an actionable message if dora-moveit2 is not present.
    """
    os.environ.setdefault("ROBOT_CONFIG_MODULE", DEFAULT_ROBOT_CONFIG)
    path = dora_moveit_path()
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        from dora_moveit.motion_planner.planner_ompl_with_collision_op import (
            OMPLPlanner,
            PlanRequest,
            PlannerType,
        )
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            f"could not import the dora-moveit2 planner from {path!r}: {exc}. "
            "Set DORA_MOVEIT2_PATH to the dora_moveit package directory."
        ) from exc
    return OMPLPlanner(num_joints=num_joints), PlanRequest, PlannerType


def resolve_config(value, num_joints: int = NUM_JOINTS) -> list[float] | None:
    """Resolve a start/goal (named pose or joint vector) to a numeric config."""
    if isinstance(value, str):
        pose = NAMED_POSES.get(value.lower().strip())
        return list(pose) if pose else None
    if isinstance(value, (list, tuple)) and len(value) == num_joints:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def flatten_trajectory(trajectory) -> list[float]:
    """Flatten a list of waypoint vectors into a single row-major float list."""
    flat: list[float] = []
    for waypoint in trajectory:
        flat.extend(float(v) for v in waypoint)
    return flat


def plan_path(planner, PlanRequest, PlannerType, start, goal, max_time=5.0,
              planner_type="rrt_connect"):
    """Run the planner from `start` to `goal`; return (trajectory, status dict)."""
    import numpy as np

    try:
        ptype = PlannerType(planner_type)
    except ValueError:
        ptype = PlannerType.RRT_CONNECT

    request = PlanRequest(
        start_config=np.array(start, dtype=float),
        goal_config=np.array(goal, dtype=float),
        planner_type=ptype,
        max_planning_time=float(max_time),
    )
    result = planner.plan(request)
    trajectory = [list(map(float, q)) for q in result.trajectory]
    status = {
        "success": bool(result.success),
        "message": result.message,
        "num_waypoints": len(trajectory),
        "planning_time": round(float(result.planning_time), 4),
        "path_length": round(float(result.path_length), 4),
    }
    return trajectory, status


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def main() -> None:
    node = Node()
    max_time = _float_env("PLANNER_MAX_TIME", 5.0)
    planner_type = os.environ.get("PLANNER_TYPE", "rrt_connect")

    planner, PlanRequest, PlannerType = load_planner()
    print(f"[rrt_planner] ready — {planner_type}, {planner.num_joints} joints, "
          f"budget {max_time}s")

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT" or event["id"] != "plan_request":
            continue

        try:
            request = json.loads(bytes(event["value"].to_pylist()).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
            node.send_output("plan_status", _json({
                "success": False, "message": f"bad request: {exc}"}))
            continue

        start = resolve_config((request or {}).get("start"))
        goal = resolve_config((request or {}).get("goal"))
        if start is None or goal is None:
            node.send_output("plan_status", _json({
                "success": False,
                "message": f"start and goal must be {NUM_JOINTS}-vectors or named "
                           f"poses; got {request!r}"}))
            continue

        trajectory, status = plan_path(
            planner, PlanRequest, PlannerType, start, goal, max_time, planner_type)
        node.send_output("plan_status", _json(status))
        if status["success"] and trajectory:
            node.send_output("trajectory", pa.array(flatten_trajectory(trajectory)))
            print(f"[rrt_planner] planned {status['num_waypoints']} waypoints in "
                  f"{status['planning_time']}s")
        else:
            print(f"[rrt_planner] FAILED: {status['message']}")


def _json(obj) -> pa.Array:
    return pa.array(json.dumps(obj).encode("utf-8"))


if __name__ == "__main__":
    main()
