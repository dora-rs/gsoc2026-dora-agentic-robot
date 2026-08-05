"""Live-LLM pick-and-place demo (Week 9) — a REAL model drives the task.

Every earlier demo used the scripted `MockProvider`: the tool *sequence* was
fixed, so the pipeline was deterministic but the "agent" made no decisions. This
one puts a real OpenAI model (GPT-4o by default) in the loop. Given only the
mission text, the tools, and the skills, the model has to discover and activate
the `pick-and-place` procedure, perceive, and issue each `dora_move` /
`dora_gripper` itself.

It runs in-process against the Week 7 stateful `PickPlaceNode`, so — exactly like
`pick_place_demo` — success is judged by **outcome**: the node tracks the ball,
and we assert it ended on the green plate. A model that calls plausible-looking
tools in the wrong order fails the check.

    export OPENAI_API_KEY=sk-...
    python -m agent.live_pick_place_demo            # gpt-4o
    OCTOS_MODEL=gpt-4o-mini python -m agent.live_pick_place_demo

Exits non-zero if the key is missing or the ball did not reach the plate. This
is the in-process counterpart of `dataflows/ur5e_mujoco_agent_llm.yml`, which
runs the same real model through the real MuJoCo simulator over dora.
"""

from __future__ import annotations

import os
import sys

from .agent import Agent, AgentConfig
from .bridge import DoraAgentBridge, compose_system_prompt
from .bridge_node import BASE_SYSTEM_PROMPT
from .motion_tools import build_full_registry
from .provider import OpenAIProvider
from .pick_place_demo import PickPlaceNode
from .skills import SkillRegistry, load_skills

MISSION = "Pick up the red ball and place it on the green plate."


def run_live(model: str | None = None, verbose: bool = True) -> PickPlaceNode:
    """Run the mission with a real LLM and return the node for inspection."""
    node = PickPlaceNode()
    bridge = DoraAgentBridge(node, poll_secs=0.0)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skills = load_skills(os.path.join(repo_root, "skills"))
    registry = build_full_registry(bridge, SkillRegistry(skills))
    system_prompt = compose_system_prompt(BASE_SYSTEM_PROMPT, skills)

    provider = OpenAIProvider(model=model or os.environ.get("OCTOS_MODEL", "gpt-4o"))
    agent = Agent(provider, registry,
                  AgentConfig(max_iterations=30, tool_timeout_secs=30.0),
                  system_prompt=system_prompt)

    final = agent.process_message(MISSION, verbose=verbose)
    if verbose:
        print(f"\nFinal response: {final}")
        print("Pipeline log:")
        for line in node.log:
            print(f"  {line}")
        print(f"Ball position: {[round(v, 3) for v in node.ball_position]}")
        print(f"Metrics: {provider.export_metrics()}")
    return node


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set — this demo needs a real API key.",
              file=sys.stderr)
        sys.exit(2)

    node = run_live()
    if node.ball_on_plate():
        print("\n✅ Live LLM completed the pick-and-place — ball is on the plate.")
        return
    print("\n❌ Ball is NOT on the green plate — the model's plan did not work.",
          file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
