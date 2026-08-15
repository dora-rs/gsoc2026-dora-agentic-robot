"""Trajectory executor dora node (Week 10) — follow a planned path on real physics.

`sim_executor` (Week 8) drove the real MuJoCo arm straight to a single goal — it
had no concept of a path, because nothing upstream produced one. Week 10 puts the
real dora-moveit2 RRT-Connect planner in the loop (`rrt_planner_node.py`), which
emits a *multi-waypoint* trajectory. This node consumes that trajectory and walks
the arm through it waypoint by waypoint: it streams each waypoint to
`mujoco_node.py` as a position target, waits until the arm has physically settled
there, then advances — and only reports `execution_status` once the final
waypoint is reached, so `dora_move`'s "one call is one finished motion" contract
still holds across a whole planned path.

It is waypoint-following through real dynamics, not time-optimal trajectory
parameterisation (that is toppra's job in the full dora-moveit2 stack). What it
adds over `sim_executor` is real: the arm now traverses the planner's path, not a
straight snap to the goal.

Inputs:
  - trajectory      : float[N*6] flattened waypoints from the planner
  - joint_positions : float[13+] full qpos from mujoco_sim (arm at [7:13])
  - tick            : timer, re-asserts the active waypoint while in flight

Outputs:
  - control_input    : float[6] arm actuator targets -> mujoco_sim/control_input
  - execution_status : JSON {"status": "completed"|"timeout", "joint_positions": [6], ...}

Environment:
  - SETTLE_TOL_RAD  : max per-joint error to call a waypoint reached (default 0.05)
  - SETTLE_TIMEOUT  : seconds before the whole trajectory is reported timed out
                      (default 12.0 — a multi-waypoint path needs longer than a
                      single settle).
"""

from __future__ import annotations

import json
import os
import sys

import pyarrow as pa
from dora import Node

# dora spawns this file by path, so the repo is not importable by default.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulation.named_poses import NUM_JOINTS  # noqa: E402
from simulation.sim_executor import _float_env, arm_slice, settled  # noqa: E402


def reshape_trajectory(flat: list[float], num_joints: int = NUM_JOINTS) -> list[list[float]]:
    """Reshape a row-major flattened path into a list of `num_joints`-vectors.

    A trailing partial waypoint (length not a multiple of `num_joints`) is
    dropped rather than trusted — a truncated target is worse than none.
    """
    n = len(flat) // num_joints
    return [list(flat[i * num_joints:(i + 1) * num_joints]) for i in range(n)]


def main() -> None:
    node = Node()
    tol = _float_env("SETTLE_TOL_RAD", 0.05)
    settle_timeout = _float_env("SETTLE_TIMEOUT", 12.0)

    arm: list[float] = []            # latest measured arm configuration
    waypoints: list[list[float]] = []  # active path, empty when idle
    idx = 0                          # index of the waypoint we're driving toward
    started_at = 0.0
    completed = 0                    # full trajectories finished
    import time

    print("[trajectory_executor] started — following planned paths on real physics")

    def advance_to(i: int) -> None:
        nonlocal idx
        idx = i
        node.send_output("control_input", pa.array(waypoints[idx]))

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "trajectory":
            flat = event["value"].to_numpy().astype(float).tolist()
            waypoints = reshape_trajectory(flat)
            if not waypoints:
                continue
            started_at = time.monotonic()
            advance_to(0)
            print(f"[trajectory_executor] following {len(waypoints)} waypoints")
            continue

        if event["id"] == "joint_positions":
            arm = arm_slice(event["value"].to_numpy().astype(float).tolist())
            if not waypoints:
                continue
            if settled(arm, waypoints[idx], tol):
                if idx + 1 < len(waypoints):
                    advance_to(idx + 1)
                else:
                    completed += 1
                    node.send_output("execution_status", _json({
                        "status": "completed", "joint_positions": arm,
                        "waypoints": len(waypoints), "motions": completed}))
                    print(f"[trajectory_executor] path {completed} complete -> "
                          f"{[round(v, 3) for v in arm]}")
                    waypoints = []
            continue

        if event["id"] == "tick":
            if waypoints:
                node.send_output("control_input", pa.array(waypoints[idx]))
                if time.monotonic() - started_at > settle_timeout:
                    node.send_output("execution_status", _json({
                        "status": "timeout", "joint_positions": arm,
                        "waypoint": idx, "waypoints": len(waypoints)}))
                    print(f"[trajectory_executor] path timed out at waypoint "
                          f"{idx}/{len(waypoints)}")
                    waypoints = []
            continue


def _json(obj) -> pa.Array:
    return pa.array(json.dumps(obj).encode("utf-8"))


if __name__ == "__main__":
    main()
