"""Demo command source — cycles the gripper open/close on a timer (Week 2).

A minimal stand-in for the agent/planner added in later weeks: every tick it
toggles between an open and a close command so the gripper demo dataflow is
self-running. Logs gripper_status feedback.

Inputs:
  - tick           : timer
  - gripper_status : JSON feedback from the gripper_controller (logged)

Outputs:
  - gripper_command : JSON {"action": "open"|"close"}
"""

from __future__ import annotations

import json

import pyarrow as pa
from dora import Node


def main() -> None:
    node = Node()
    actions = ["open", "close"]
    i = 0

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "tick":
            action = actions[i % len(actions)]
            i += 1
            cmd = {"action": action}
            node.send_output(
                "gripper_command",
                pa.array(json.dumps(cmd).encode("utf-8")),
            )
            print(f"[command_source] -> {action}")
        elif event["id"] == "gripper_status":
            try:
                status = json.loads(bytes(event["value"].to_pylist()).decode("utf-8"))
                print(f"[command_source] status: {status}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass


if __name__ == "__main__":
    main()
