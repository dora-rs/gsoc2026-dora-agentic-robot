"""Agent bridge dora node (Week 6, extended Week 7) — the pipeline driver.

Runs the Week 4 `Agent` (with the Week 5 provider stack and Week 6 skills) as a
live dora node. It reads natural-language missions on `user_command`, drives the
motion pipeline through the robot tools, and emits the agent's text on
`agent_response`; all sensor/status inputs are cached for the tools to read.

Week 7 swaps the transport-only registry for `build_full_registry` — the
`dora_move`/`dora_gripper`/`dora_perceive`/`dora_list` semantic tools plus the
`skill` tool backed by a `SkillRegistry`, so dormant skills load on demand.

    dora start dataflows/ur5e_agent_demo.yml     # live pipeline
    dora run dataflows/ur5e_agent_loopback.yml   # no external deps

Env vars:
  OCTOS_PROVIDER   provider — "mock" (default, no API key) or "openai"
  OPENAI_API_KEY   required when OCTOS_PROVIDER=openai
  OCTOS_MODEL      OpenAI model (default gpt-4o)
  OCTOS_SKILLS_DIR skills directory (default: <repo>/skills)
  USER_COMMAND     if set, run that one mission then stop (one-shot); else
                   run persistently, waiting for commands on `user_command`

This module is only imported by the dora runtime, so `from dora import Node`
lives here, not in the reusable `agent/bridge.py` library.
"""

from __future__ import annotations

import json
import os
import sys

import pyarrow as pa
from dora import Node

# dora spawns a node by *path*, so this file runs as a top-level script with no
# parent package — relative imports would fail. Put the repo root on sys.path and
# import absolutely, so the same file works under `dora start` and `python -m`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import Agent, AgentConfig  # noqa: E402
from agent.bridge import DoraAgentBridge, compose_system_prompt  # noqa: E402
from agent.motion_tools import build_full_registry  # noqa: E402
from agent.provider import (  # noqa: E402
    ChatResponse, LlmProvider, MockProvider, OpenAIProvider,
)
from agent.failover import RetryProvider  # noqa: E402
from agent.skills import SkillRegistry, load_skills  # noqa: E402

BASE_SYSTEM_PROMPT = (
    "You are a robot control agent operating a UR5e 6-DOF arm with a Robotiq "
    "2F-85 gripper in MuJoCo simulation, driving a dora motion-planning pipeline. "
    "Call dora_perceive before planning, move the arm with dora_move (one motion "
    "at a time — it returns when the motion is done), and drive the gripper with "
    "dora_gripper. Before a multi-step task, activate the matching procedure from "
    "the 'Skill procedures available on demand' list below with skill(activate, "
    "<name>) and follow it step by step. If a dora_move returns an error it could "
    "not recover from, call dora_perceive to see where the arm is, then approach "
    "from a different pose rather than repeating the same failing call. If you are "
    "told about an obstacle to avoid, register it with dora_obstacle(add, ...) "
    "before moving near it — the planner then routes every motion around it. When "
    "the task is done, reply with a short confirmation and stop."
)


def _default_skills_dir() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("OCTOS_SKILLS_DIR") or os.path.join(repo_root, "skills")


def build_provider() -> LlmProvider:
    """Construct the LLM provider from the environment."""
    provider_name = os.environ.get("OCTOS_PROVIDER", "mock").lower()
    if provider_name == "mock":
        return MockProvider(_mock_script())
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[bridge] ERROR: OCTOS_PROVIDER=openai but OPENAI_API_KEY is unset",
              file=sys.stderr)
        sys.exit(1)
    model = os.environ.get("OCTOS_MODEL", "gpt-4o")
    return RetryProvider(OpenAIProvider(model=model, api_key=api_key))


def _mock_script() -> list[ChatResponse]:
    """The pick-and-place mission, scripted, so OCTOS_PROVIDER=mock needs no API key.

    This is the exact tool sequence a real LLM is expected to produce from the
    `pick-and-place` skill: discover the skill, activate it, perceive, then walk
    the grasp/place procedure. Scripting it keeps the *pipeline* under test
    deterministic — the dataflow, bridge, and tools are all real.
    """
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
        ChatResponse(
            content="Pick-and-place complete: grasped the red ball, placed it on "
                    "the green plate, and returned the arm home.",
            finish_reason="stop"),
    ]


def _decode_command(value) -> str | None:
    try:
        raw = bytes(value.to_pylist()).decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return None
    try:  # a JSON string or a bare string are both accepted
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, str) else raw
    except json.JSONDecodeError:
        return raw


def _run_mission(agent: Agent, bridge: DoraAgentBridge, node, command: str) -> None:
    print(f"\n[bridge] >>> mission: {command}")
    response = agent.process_message(command, verbose=True)
    node.send_output("agent_response", pa.array(response.encode("utf-8")))
    print(f"[bridge] <<< {response}")


def main() -> None:
    node = Node()
    bridge = DoraAgentBridge(node)
    bridge.drain(1.0)  # prime the sensor cache

    provider = build_provider()
    skills = load_skills(_default_skills_dir())
    for skill in skills:
        print(f"[bridge] loaded skill '{skill.name}' v{skill.version} (always={skill.always})")

    # `always: true` skills go into the prompt; the rest stay dormant until the
    # agent activates them through the `skill` tool.
    skill_registry = SkillRegistry(skills)
    registry = build_full_registry(bridge, skill_registry)
    system_prompt = compose_system_prompt(BASE_SYSTEM_PROMPT, skills)
    agent = Agent(provider, registry, AgentConfig(), system_prompt=system_prompt)

    one_shot = os.environ.get("USER_COMMAND", "")
    if one_shot:
        _run_mission(agent, bridge, node, one_shot)
        import subprocess
        subprocess.Popen(["dora", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    # The prime drain above caches *every* input it sees, `user_command` included.
    # A mission published while this node was still starting up would otherwise
    # sit in the cache forever and never run, so claim it before looping.
    pending = bridge.cache.pop("user_command", None)
    if pending is not None and isinstance(pending[0], str):
        _run_mission(agent, bridge, node, pending[0])

    print("[bridge] persistent mode — waiting for commands on 'user_command'")
    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue
        if event["id"] == "user_command":
            command = _decode_command(event["value"])
            if command:
                _run_mission(agent, bridge, node, command)
        else:
            bridge.ingest(event)


if __name__ == "__main__":
    main()
