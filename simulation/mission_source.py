"""Mission source dora node (Week 6) — emits one natural-language mission.

A minimal driver for the agent-bridge demo: on the first tick it sends the
`MISSION` env string on `user_command` (once), then just logs the agent's
`agent_response`. Swap this for an interactive/dynamic source to type missions
live into the running dataflow.

Inputs:
  - tick           : timer
  - agent_response : the agent's reply (logged)

Outputs:
  - user_command   : JSON string, the natural-language mission
"""

from __future__ import annotations

import json
import os

import pyarrow as pa
from dora import Node

DEFAULT_MISSION = "Move the arm home and close the gripper."


def main() -> None:
    node = Node()
    mission = os.environ.get("MISSION", DEFAULT_MISSION)
    sent = False

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "tick" and not sent:
            node.send_output("user_command", pa.array(json.dumps(mission).encode("utf-8")))
            print(f"[mission_source] -> {mission}")
            sent = True
        elif event["id"] == "agent_response":
            try:
                reply = bytes(event["value"].to_pylist()).decode("utf-8")
                print(f"[mission_source] agent: {reply}")
            except (UnicodeDecodeError, AttributeError):
                pass


if __name__ == "__main__":
    main()
