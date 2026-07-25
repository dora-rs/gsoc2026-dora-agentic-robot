"""Sim executor dora node (Week 8) — drive the *real* MuJoCo arm from plan_requests.

`ur5e_agent_loopback.yml` (Week 7) proved the agent works across real dora
processes, but its `pipeline_stub` *teleports* the arm — no physics. The live
`ur5e_agent_demo.yml` runs the real MuJoCo sim, but through the full dora-moveit2
planner/IK/executor stack (OMPL + TracIK + toppra), which needs those extra
dependencies installed.

This node is the middle ground the agent's motion contract actually needs to
reach the real simulator without that stack: it accepts a `plan_request`, drives
the UR5e's position actuators toward the goal by streaming `control_input` to
`mujoco_node.py`, watches the returned `joint_positions` until the arm has
physically settled, and only then reports `execution_status` — so `dora_move`'s
"one call is one finished motion" contract holds against real physics.

It is direct joint control, **not** motion planning: there is no collision
checking and no path search (that is what dora-moveit2 provides). What it adds
over the stub is real: real actuators, real settling dynamics, the real qpos
layout, and the real gripper contact — so a pick-and-place here exercises the
simulator, not a teleport.

Inputs:
  - plan_request    : JSON {"start": [6], "goal": [6] | name}
  - joint_positions : float[13] full qpos from mujoco_sim (arm at [7:13])
  - tick            : timer, re-asserts the target while a motion is in flight

Outputs:
  - control_input    : float[6] arm actuator targets -> mujoco_sim/control_input
  - plan_status      : JSON {"success": bool, "message": str}
  - execution_status : JSON {"status": "completed"|"timeout", "joint_positions": [6], ...}

Environment:
  - SETTLE_TOL_RAD  : max per-joint error to call a motion settled (default 0.05)
  - SETTLE_TIMEOUT  : seconds before a motion is reported as "timeout" (default 8.0)
"""

from __future__ import annotations

import json
import os
import sys
import time

import pyarrow as pa
from dora import Node

# dora spawns this file by path, so the repo is not importable by default.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulation.named_poses import NUM_JOINTS  # noqa: E402
from simulation.pipeline_stub import FREE_JOINT_QPOS, parse_goal  # noqa: E402


def arm_slice(qpos: list[float]) -> list[float]:
    """Extract the 6 arm joints from a full qpos vector (arm sits after the ball)."""
    end = FREE_JOINT_QPOS + NUM_JOINTS
    if len(qpos) >= end:
        return list(qpos[FREE_JOINT_QPOS:end])
    return list(qpos[:NUM_JOINTS])


def settled(current: list[float], goal: list[float], tol: float) -> bool:
    """True once every joint is within `tol` radians of the goal."""
    if len(current) != len(goal):
        return False
    return max(abs(a - b) for a, b in zip(current, goal)) <= tol


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def main() -> None:
    node = Node()
    tol = _float_env("SETTLE_TOL_RAD", 0.05)
    settle_timeout = _float_env("SETTLE_TIMEOUT", 8.0)

    arm: list[float] = []          # latest measured arm configuration
    goal: list[float] | None = None  # active motion goal, or None when idle
    started_at = 0.0
    completed = 0
    print("[sim_executor] started — direct joint control to the real MuJoCo arm")

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "joint_positions":
            arm = arm_slice(event["value"].to_numpy().astype(float).tolist())
            if goal is not None and settled(arm, goal, tol):
                completed += 1
                node.send_output("execution_status", _json({
                    "status": "completed", "joint_positions": arm,
                    "motions": completed}))
                print(f"[sim_executor] motion {completed} settled -> "
                      f"{[round(v, 3) for v in arm]}")
                goal = None
            continue

        if event["id"] == "tick":
            # Keep the position target fresh while a motion is in flight, and give
            # up gracefully if the arm never converges (a real planner would too).
            if goal is not None:
                node.send_output("control_input", pa.array(goal))
                if time.monotonic() - started_at > settle_timeout:
                    node.send_output("execution_status", _json({
                        "status": "timeout", "joint_positions": arm,
                        "goal": goal}))
                    print(f"[sim_executor] motion timed out after {settle_timeout}s "
                          f"at {[round(v, 3) for v in arm]}")
                    goal = None
            continue

        if event["id"] != "plan_request":
            continue

        try:
            request = json.loads(bytes(event["value"].to_pylist()).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
            node.send_output("plan_status", _json({"success": False,
                                                   "message": f"bad request: {exc}"}))
            continue

        target = parse_goal(request if isinstance(request, dict) else {})
        if target is None:
            node.send_output("plan_status", _json({
                "success": False, "message": f"unreachable goal: {request!r}"}))
            continue

        goal = target
        started_at = time.monotonic()
        node.send_output("plan_status", _json({"success": True,
                                               "message": "commanding joint target"}))
        node.send_output("control_input", pa.array(goal))
        print(f"[sim_executor] driving arm -> {[round(v, 3) for v in goal]}")


def _json(obj) -> pa.Array:
    return pa.array(json.dumps(obj).encode("utf-8"))


if __name__ == "__main__":
    main()
