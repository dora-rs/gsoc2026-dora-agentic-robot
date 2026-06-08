"""Robotiq 2F-85 gripper controller dora node (Week 2).

Translates high-level gripper commands into the MuJoCo actuator command the
simulation applies to `data.ctrl[6]` (the `fingers_actuator` tendon, ctrlrange
0..255: 0 = fully open, 255 = fully closed).

Inputs:
  - gripper_command : JSON {"action": "open"|"close"|"set_position", "position"?: meters}

Outputs:
  - gripper_ctrl   : float32[1], actuator command in [0, 255] -> mujoco_sim/gripper_ctrl
  - gripper_status : JSON {"state": "open"|"closed"|"partial", "width": meters, "ctrl": float}

The mapping and command resolution live in pure module-level functions so they
can be unit-tested without MuJoCo or a running dataflow.
"""

from __future__ import annotations

import json

import pyarrow as pa
from dora import Node

# Robotiq 2F-85 stroke and actuator range.
GRIPPER_MAX_WIDTH = 0.085   # meters, fully open
GRIPPER_MIN_WIDTH = 0.0     # meters, fully closed
CTRL_OPEN = 0.0
CTRL_CLOSED = 255.0


def clamp_width(width: float) -> float:
    return max(GRIPPER_MIN_WIDTH, min(GRIPPER_MAX_WIDTH, float(width)))


def width_to_ctrl(width: float) -> float:
    """Map an opening width (meters) to the actuator command [0, 255]."""
    w = clamp_width(width)
    return CTRL_CLOSED * (1.0 - w / GRIPPER_MAX_WIDTH)


def state_for_width(width: float) -> str:
    w = clamp_width(width)
    if w <= 0.01:
        return "closed"
    if w >= 0.08:
        return "open"
    return "partial"


def resolve_command(cmd: dict) -> dict:
    """Resolve a gripper command into a target {width, ctrl, state}.

    Raises ValueError for an unknown or malformed action.
    """
    action = str(cmd.get("action", "")).lower().strip()
    if action == "open":
        width = GRIPPER_MAX_WIDTH
    elif action == "close":
        width = GRIPPER_MIN_WIDTH
    elif action == "set_position":
        if "position" not in cmd:
            raise ValueError("set_position requires a 'position' (meters)")
        width = clamp_width(cmd["position"])
    else:
        raise ValueError(f"unknown gripper action: {cmd.get('action')!r}")

    return {
        "width": round(width, 4),
        "ctrl": round(width_to_ctrl(width), 2),
        "state": state_for_width(width),
    }


def _decode_command(value) -> dict:
    """Decode a dora gripper_command event value (uint8 JSON bytes) to a dict."""
    raw = bytes(value.to_pylist())
    return json.loads(raw.decode("utf-8"))


def main() -> None:
    node = Node()
    print("[gripper] Robotiq 2F-85 controller started (0=open .. 255=closed)")

    for event in node:
        if event["type"] != "INPUT" or event["id"] != "gripper_command":
            if event["type"] == "STOP":
                break
            continue

        try:
            cmd = _decode_command(event["value"])
            result = resolve_command(cmd)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
            node.send_output(
                "gripper_status",
                pa.array(json.dumps({"state": "error", "error": str(exc)}).encode("utf-8")),
            )
            print(f"[gripper] bad command: {exc}")
            continue

        node.send_output("gripper_ctrl", pa.array([result["ctrl"]], type=pa.float32()))
        node.send_output("gripper_status", pa.array(json.dumps(result).encode("utf-8")))
        print(f"[gripper] {cmd.get('action')} -> ctrl={result['ctrl']} "
              f"width={result['width']} state={result['state']}")


if __name__ == "__main__":
    main()
