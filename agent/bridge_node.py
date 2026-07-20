"""Agent bridge dora node (Week 6) — the pipeline driver that replaces command_source.

Runs the Week 4 `Agent` (with the Week 5 provider stack and Week 6 skills) as a
live dora node. It reads natural-language missions on `user_command`, drives the
motion pipeline through the dora transport tools, and emits the agent's text on
`agent_response`; all sensor/status inputs are cached for the tools to read.

    dora start dataflows/ur5e_agent_demo.yml

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

from .agent import Agent, AgentConfig
from .bridge import DoraAgentBridge, build_bridge_registry, compose_system_prompt
from .provider import ChatResponse, LlmProvider, MockProvider, OpenAIProvider
from .failover import RetryProvider
from .skills import load_skills

BASE_SYSTEM_PROMPT = (
    "You are a robot control agent operating a UR5e 6-DOF arm with a Robotiq "
    "2F-85 gripper in MuJoCo simulation, driving a dora motion-planning pipeline. "
    "Read joint_positions before planning, plan motions with dora_call on "
    "plan_request, and wait for each motion to finish before the next command."
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
    """A tiny scripted mission so OCTOS_PROVIDER=mock drives the pipeline visibly."""
    return [
        ChatResponse(
            tool_calls=[MockProvider.tool_call("dora_read", {"input_id": "joint_positions"})],
            finish_reason="tool_calls",
        ),
        ChatResponse(
            tool_calls=[MockProvider.tool_call("dora_call", {
                "output_id": "plan_request",
                "data": {"goal": "home"},
                "response_id": "plan_status",
            })],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="Read the arm state and planned a motion home.",
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
    registry = build_bridge_registry(bridge)
    skills = load_skills(_default_skills_dir())
    for skill in skills:
        print(f"[bridge] loaded skill '{skill.name}' v{skill.version} (always={skill.always})")

    system_prompt = compose_system_prompt(BASE_SYSTEM_PROMPT, skills)
    agent = Agent(provider, registry, AgentConfig(), system_prompt=system_prompt)

    one_shot = os.environ.get("USER_COMMAND", "")
    if one_shot:
        _run_mission(agent, bridge, node, one_shot)
        import subprocess
        subprocess.Popen(["dora", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

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
