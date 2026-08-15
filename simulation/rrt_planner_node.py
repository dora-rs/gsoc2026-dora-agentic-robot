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
  - plan_request  : JSON {"start": [6], "goal": [6] | name}
  - scene_command : JSON {"action":"add","object":{...}} | {"action":"remove",
                    "name":...} | {"action":"clear"} — runtime workspace obstacles
                    the collision-aware planner then routes around (Week 12).

Outputs:
  - plan_status  : JSON {"success": bool, "message": str, "num_waypoints": int, ...}
  - trajectory   : float[N*6] flattened waypoints (row-major, N waypoints x 6 joints)
  - scene_result : JSON {"result": str, "obstacles": int} — ack of a scene_command

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


def load_collision_planner(obstacles=None, ground_z=None, margin: float = 0.0,
                           num_joints: int = NUM_JOINTS):
    """Like `load_planner`, but the returned planner does real collision checking.

    dora-moveit2's own `is_state_valid` is stubbed (returns True). Here the arm is
    placed in the world with the MuJoCo-exact UR5e FK and tested against the table
    and `obstacles` (see `simulation.ur5e_collision`), so RRT-Connect searches for
    a genuinely collision-free path and a goal that is in collision fails to plan —
    the real signal `dora_move`'s task-level recovery reacts to.

    Returns `(planner, PlanRequest, PlannerType)` with the same shape as
    `load_planner`; the collision check is patched onto the instance so the base
    RRT algorithms use it via `is_motion_valid`.
    """
    import numpy as np

    from simulation.ur5e_collision import GROUND_Z, config_in_collision

    planner, PlanRequest, PlannerType = load_planner(num_joints)
    from dora_moveit.motion_planner.planner_ompl_with_collision_op import PlanResult

    # Keep the caller's list by reference so obstacles added at runtime (via a
    # scene_command) are seen live by is_state_valid — do not copy.
    obstacles = [] if obstacles is None else obstacles
    ground_z = GROUND_Z if ground_z is None else ground_z
    lo, hi = planner.joint_limits_lower, planner.joint_limits_upper

    def is_state_valid(config) -> bool:
        c = np.asarray(config, dtype=float)
        if not (np.all(c >= lo) and np.all(c <= hi)):
            return False
        return not config_in_collision(c, obstacles, ground_z, margin)

    base_plan = planner.plan

    def plan(request):
        # Reject an unreachable start/goal up front so the failure is a clear
        # "in collision", not an exhausted search.
        if not is_state_valid(request.goal_config):
            return PlanResult(success=False, message="goal configuration is in collision")
        if not is_state_valid(request.start_config):
            return PlanResult(success=False, message="start configuration is in collision")
        return base_plan(request)

    planner.is_state_valid = is_state_valid
    planner.plan = plan
    return planner, PlanRequest, PlannerType


def apply_scene_command(obstacles: list, command: dict) -> str:
    """Mutate `obstacles` in place from a scene_command; return a status message.

    Commands: `{"action":"add","object":{...}}`, `{"action":"remove","name":...}`,
    `{"action":"clear"}`. Adding a name that already exists replaces it, so a
    repeated add is idempotent. Returns a short human-readable result.
    """
    from simulation.ur5e_collision import obstacle_from_spec

    action = str((command or {}).get("action", "")).lower()
    if action == "clear":
        n = len(obstacles)
        obstacles.clear()
        return f"cleared {n} obstacle(s)"
    if action == "remove":
        name = command.get("name")
        before = len(obstacles)
        obstacles[:] = [o for o in obstacles if o.get("name") != name]
        return f"removed {before - len(obstacles)} obstacle(s) named {name!r}"
    if action == "add":
        try:
            obs = obstacle_from_spec(command.get("object", {}))
        except (ValueError, KeyError, TypeError) as exc:
            return f"rejected obstacle: {exc}"
        obstacles[:] = [o for o in obstacles if o.get("name") != obs["name"]]
        obstacles.append(obs)
        return f"added {obs['type']} {obs['name']!r} ({len(obstacles)} total)"
    return f"unknown scene action {action!r}"


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


def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    node = Node()
    max_time = _float_env("PLANNER_MAX_TIME", 5.0)
    planner_type = os.environ.get("PLANNER_TYPE", "rrt_connect")
    collision = _bool_env("COLLISION", True)

    # Obstacles are mutated in place by scene_command at runtime; the collision
    # planner reads this same list live, so a plan issued after an obstacle is
    # added routes around it.
    obstacles: list = []
    if collision:
        planner, PlanRequest, PlannerType = load_collision_planner(obstacles=obstacles)
    else:
        planner, PlanRequest, PlannerType = load_planner()
    print(f"[rrt_planner] ready — {planner_type}, {planner.num_joints} joints, "
          f"budget {max_time}s, collision={'on' if collision else 'off'}")

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        # Runtime scene updates: add/remove/clear workspace obstacles.
        if event["id"] == "scene_command":
            try:
                command = json.loads(bytes(event["value"].to_pylist()).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
                print(f"[rrt_planner] bad scene_command: {exc}")
                continue
            result = apply_scene_command(obstacles, command)
            node.send_output("scene_result", _json({"result": result,
                                                    "obstacles": len(obstacles)}))
            print(f"[rrt_planner] scene: {result}")
            continue

        if event["id"] != "plan_request":
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
