"""Pick-and-place through the REAL planner (Week 10) — outcome-verified.

Week 9 proved a live LLM can complete the pick-and-place, but every `dora_move`
went through `sim_executor`-style direct control: the goal was applied straight,
no path search. This demo puts the genuine dora-moveit2 RRT-Connect planner in
the loop. `PlannerBackedNode` subclasses the Week 7 stateful `PickPlaceNode` and
overrides one thing — a `plan_request` is now run through the real `OMPLPlanner`
(configured for the UR5e), producing an actual multi-waypoint path, before the
arm arrives at the goal. Everything else — the ball tracking, the gripper, the
outcome check — is unchanged, so success is still judged by **outcome**: the ball
must end on the green plate, and every motion must have been a real plan.

Two entry points, mirroring the split the rest of the repo uses:

    # deterministic, no API key (the CI-able proof the planner is in the loop):
    python -m agent.live_planner_demo --mock

    # a real GPT-4o driving the same real planner:
    export OPENAI_API_KEY=sk-...
    python -m agent.live_planner_demo

Both need the dora-moveit2 checkout importable (see rrt_planner_node). This is the
in-process counterpart of `dataflows/ur5e_mujoco_planner_llm.yml`, which runs the
same real model + real planner through the real MuJoCo simulator over dora.
"""

from __future__ import annotations

import os
import sys

from simulation.rrt_planner_node import load_planner, plan_path
from .agent import Agent, AgentConfig
from .bridge import DoraAgentBridge, compose_system_prompt
from .bridge_node import BASE_SYSTEM_PROMPT
from .motion_tools import build_full_registry
from .pick_place_demo import (
    POSE_POSITIONS,
    PickPlaceNode,
    _json_value,
    _pose_name,
    _script,
)
from .provider import MockProvider, OpenAIProvider
from .skills import SkillRegistry, load_skills

MISSION = "Pick up the red ball and place it on the green plate."


class PlannerBackedNode(PickPlaceNode):
    """PickPlaceNode whose `dora_move` runs the real RRT-Connect planner.

    `self.plans` records the waypoint count of every accepted plan, so a test can
    assert the motions were genuinely planned (>= 2 waypoints), not teleported.
    """

    def __init__(self, start_pose: str = "home"):
        self._planner, self._PlanRequest, self._PlannerType = load_planner()
        self.plans: list[int] = []
        super().__init__(start_pose)

    def _handle_plan(self, array) -> None:
        goal = self._decode(array).get("goal")
        if not isinstance(goal, list) or len(goal) != len(self.arm):
            self._push("plan_status", _json_value({"success": False,
                                                   "message": "unplannable goal"}))
            return

        goal = [float(v) for v in goal]
        # The agent sends the exact named-pose vector, so identify the pose from
        # the requested goal (the planner's final waypoint only lands within
        # tolerance of it). The real planner still searches start -> goal.
        name = _pose_name(goal)
        trajectory, status = plan_path(
            self._planner, self._PlanRequest, self._PlannerType,
            list(self.arm), goal)

        if not status["success"] or not trajectory:
            self._push("plan_status", _json_value({
                "success": False,
                "message": status.get("message", "planning failed")}))
            return

        self.plans.append(status["num_waypoints"])
        self.arm = goal  # arrive at the goal (executor settles here on real sim)
        self.log.append(f"planned {status['num_waypoints']} waypoints -> "
                        f"{name or 'custom pose'}")

        # While the gripper holds the ball, the ball travels with the arm.
        if self.holding and name in POSE_POSITIONS:
            self.ball_position = list(POSE_POSITIONS[name])

        self._push("plan_status", _json_value({
            "success": True, "message": f"planned {status['num_waypoints']} waypoints",
            "num_waypoints": status["num_waypoints"]}))
        self._publish_state()
        self._push("execution_status", _json_value({
            "status": "completed", "pose": name,
            "waypoints": status["num_waypoints"]}))


def _run(provider, verbose: bool) -> PlannerBackedNode:
    node = PlannerBackedNode()
    bridge = DoraAgentBridge(node, poll_secs=0.0)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skills = load_skills(os.path.join(repo_root, "skills"))
    registry = build_full_registry(bridge, SkillRegistry(skills))
    system_prompt = compose_system_prompt(BASE_SYSTEM_PROMPT, skills)

    agent = Agent(provider, registry,
                  AgentConfig(max_iterations=30, tool_timeout_secs=30.0),
                  system_prompt=system_prompt)
    final = agent.process_message(MISSION, verbose=verbose)

    if verbose:
        print(f"\nFinal response: {final}")
        print("Pipeline log:")
        for line in node.log:
            print(f"  {line}")
        print(f"Plans (waypoints each): {node.plans}")
        print(f"Ball position: {[round(v, 3) for v in node.ball_position]}")
    return node


def run_scripted(verbose: bool = True) -> PlannerBackedNode:
    """Deterministic run (no API key) proving the real planner is in the loop."""
    return _run(MockProvider(_script()), verbose)


def run_live(model: str | None = None, verbose: bool = True) -> PlannerBackedNode:
    """A real LLM driving the mission through the real planner."""
    provider = OpenAIProvider(model=model or os.environ.get("OCTOS_MODEL", "gpt-4o"))
    return _run(provider, verbose)


def main() -> None:
    use_mock = "--mock" in sys.argv
    if not use_mock and not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set — run with --mock for the scripted proof.",
              file=sys.stderr)
        sys.exit(2)

    node = run_scripted() if use_mock else run_live()
    planned = node.plans and all(n >= 2 for n in node.plans)
    if node.ball_on_plate() and planned:
        print(f"\n✅ Pick-and-place completed through the real planner "
              f"({len(node.plans)} plans, {sum(node.plans)} waypoints total).")
        return
    if not node.ball_on_plate():
        print("\n❌ Ball is NOT on the green plate.", file=sys.stderr)
    else:
        print("\n❌ Motions were not real multi-waypoint plans.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
