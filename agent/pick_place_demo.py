"""Self-running pick-and-place demo (Week 7) — no dora daemon, no MuJoCo, no LLM.

Week 6's demo showed the agent *reaching* the pipeline. This one shows it
completing a task: the agent discovers the dormant `pick-and-place` skill,
activates it, and runs the grasp/place procedure through the Week 7 robot tools.

The difference from Week 6's fake node is that `PickPlaceNode` keeps **state** —
where the arm is, whether the gripper is closed, and where the ball is. The ball
follows the gripper while it is closed, so at the end we can assert something
real: the ball is on the green plate. A demo that only checks "the tools were
called" cannot tell a correct sequence from a plausible-looking wrong one.

    python -m agent.pick_place_demo

Exits non-zero if the ball did not end up on the plate.
"""

from __future__ import annotations

import json
import os
import sys
from collections import deque

import pyarrow as pa

from simulation.named_poses import NAMED_POSES, SCENE_OBJECTS
from .agent import Agent, AgentConfig
from .bridge import DoraAgentBridge, compose_system_prompt
from .motion_tools import build_full_registry
from .provider import ChatResponse, MockProvider
from .skills import SkillRegistry, load_skills

FREE_JOINT_QPOS = 7

# Poses at which the gripper is low enough to hold/release the ball, and the
# world position the ball ends up at for each. Enough fidelity to catch an
# out-of-order sequence without pulling in a physics engine.
GRASP_POSES = {"grasp_ball", "place_plate"}
POSE_POSITIONS = {
    "grasp_ball": SCENE_OBJECTS["red_ball"]["position"],
    "place_plate": SCENE_OBJECTS["green_plate"]["position"],
}


def _json_value(obj) -> pa.Array:
    return pa.array(json.dumps(obj).encode("utf-8"))


def _pose_name(joints: list[float], tolerance: float = 1e-3) -> str | None:
    for name, pose in NAMED_POSES.items():
        if max(abs(a - b) for a, b in zip(pose, joints)) <= tolerance:
            return name
    return None


class PickPlaceNode:
    """Stateful in-memory dora node: arm pose, gripper state, and ball position."""

    def __init__(self, start_pose: str = "home"):
        self._queue: deque[dict] = deque()
        self.sent: list[tuple[str, object]] = []
        self.arm = list(NAMED_POSES[start_pose])
        self.gripper_closed = False
        self.holding = False
        self.ball_position = list(SCENE_OBJECTS["red_ball"]["position"])
        self.log: list[str] = []
        self._publish_state()

    # --- dora Node API ----------------------------------------------------

    def next(self, timeout: float = 0.0):
        return self._queue.popleft() if self._queue else None

    def send_output(self, output_id: str, array, metadata=None) -> None:
        self.sent.append((output_id, array))
        if output_id == "plan_request":
            self._handle_plan(array)
        elif output_id == "gripper_command":
            self._handle_gripper(array)

    # --- simulated pipeline ----------------------------------------------

    def _push(self, input_id: str, value) -> None:
        self._queue.append({"type": "INPUT", "id": input_id, "value": value})

    def _publish_state(self) -> None:
        self._push("joint_positions", pa.array([0.0] * FREE_JOINT_QPOS + self.arm))

    def _decode(self, array) -> dict:
        try:
            return json.loads(bytes(array.to_pylist()).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return {}

    def _handle_plan(self, array) -> None:
        goal = self._decode(array).get("goal")
        if not isinstance(goal, list) or len(goal) != len(self.arm):
            self._push("plan_status", _json_value({"success": False,
                                                   "message": "unplannable goal"}))
            return

        self.arm = [float(v) for v in goal]
        name = _pose_name(self.arm)
        self.log.append(f"move -> {name or 'custom pose'}")

        # While the gripper holds the ball, the ball travels with the arm.
        if self.holding and name in POSE_POSITIONS:
            self.ball_position = list(POSE_POSITIONS[name])

        self._push("plan_status", _json_value({"success": True, "message": "planned"}))
        self._publish_state()
        self._push("execution_status", _json_value({"status": "completed",
                                                    "pose": name}))

    def _handle_gripper(self, array) -> None:
        action = str(self._decode(array).get("action", "")).lower()
        at = _pose_name(self.arm)
        self.gripper_closed = action == "close"

        if action == "close":
            # Only grasps something if the arm is actually down at the ball.
            self.holding = at == "grasp_ball" and self.ball_position[:2] == \
                SCENE_OBJECTS["red_ball"]["position"][:2]
            self.log.append(f"close at {at} -> holding={self.holding}")
        elif action == "open":
            if self.holding and at in POSE_POSITIONS:
                self.ball_position = list(POSE_POSITIONS[at])
            self.holding = False
            self.log.append(f"open at {at}")

        state = "closed" if self.gripper_closed else "open"
        self._push("gripper_status", _json_value({"state": state,
                                                  "holding": self.holding}))

    # --- verification -----------------------------------------------------

    def ball_on_plate(self, tolerance: float = 0.02) -> bool:
        plate = SCENE_OBJECTS["green_plate"]["position"]
        return all(abs(a - b) <= tolerance for a, b in zip(self.ball_position[:2],
                                                           plate[:2]))


def _script() -> list[ChatResponse]:
    """The tool sequence a real LLM should produce from the pick-and-place skill."""
    def call(name: str, args: dict) -> ChatResponse:
        return ChatResponse(tool_calls=[MockProvider.tool_call(name, args)],
                            finish_reason="tool_calls")

    return [
        call("skill", {"action": "list"}),
        call("skill", {"action": "activate", "name": "pick-and-place"}),
        call("dora_perceive", {}),
        call("dora_gripper", {"action": "open"}),
        call("dora_move", {"target": "above_ball"}),
        call("dora_move", {"target": "grasp_ball"}),
        call("dora_gripper", {"action": "close"}),
        call("dora_move", {"target": "lift"}),
        call("dora_move", {"target": "above_plate"}),
        call("dora_move", {"target": "place_plate"}),
        call("dora_gripper", {"action": "open"}),
        call("dora_move", {"target": "home"}),
        ChatResponse(content="The red ball is on the green plate and the arm is home.",
                     finish_reason="stop"),
    ]


def run_demo(verbose: bool = True) -> PickPlaceNode:
    """Run the mission end to end and return the node for inspection."""
    node = PickPlaceNode()
    bridge = DoraAgentBridge(node, poll_secs=0.0)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skills = load_skills(os.path.join(repo_root, "skills"))
    skill_registry = SkillRegistry(skills)
    registry = build_full_registry(bridge, skill_registry)

    if verbose:
        for skill in skills:
            state = "always-on" if skill.always else "dormant"
            print(f"[skill] {skill.name} v{skill.version} ({state})")
        print(f"[tools] {registry.active_tool_names()}")

    system_prompt = compose_system_prompt("You control a UR5e arm via dora.", skills)
    agent = Agent(MockProvider(_script()), registry,
                  AgentConfig(max_iterations=20, tool_timeout_secs=10.0),
                  system_prompt=system_prompt)

    final = agent.process_message(
        "Pick up the red ball and place it on the green plate.", verbose=verbose)

    if verbose:
        print(f"\nFinal response: {final}")
        print("Pipeline log:")
        for line in node.log:
            print(f"  {line}")
        print(f"\nActivated skills: {[s.name for s in skill_registry.active()]}")
        print(f"Ball position: {[round(v, 3) for v in node.ball_position]}")
    return node


def main() -> None:
    node = run_demo()
    if node.ball_on_plate():
        print("\n✅ Ball is on the green plate — pick-and-place succeeded.")
        return
    print("\n❌ Ball is NOT on the green plate — pick-and-place failed.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
