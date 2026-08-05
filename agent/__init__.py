"""Agent core (Week 4) — the Octos-pattern agent that replaces `command_source`.

This package is the "brain" of the agentic dataflow: an `Agent` loop driving an
`LlmProvider` against a `ToolRegistry`. Week 4 lands the framework only — the
loop, config, registry, the tool abstraction, and a deterministic `MockProvider`
so everything is testable without an LLM or a running dataflow.

Later phases extend this package (all interfaces here are forward-compatible):
  - Week 5 — real `OpenAIProvider` + 3-layer failover
  - Week 6 — SKILL.md loader + the dora bridge node
  - Week 7 — motion/introspection tools + on-demand skill activation
  - Week 8 — dora_move replanning/recovery + the agent driving the real MuJoCo
    simulator (see `agent.motion_tools`, `simulation.sim_executor`)
  - Week 9 — a live LLM completes the task: dormant-skill catalogue in the
    prompt, self-healing sensor reads, and `ReactiveMockProvider` for
    deterministic recovery tests (see `agent.live_pick_place_demo`)

The `SKILL.md` loader and `SkillRegistry` are exported here (pure Python). The
dora bridge and its tools live in the `agent.bridge` / `agent.motion_tools` /
`agent.bridge_node` submodules, imported explicitly so the core package stays
free of the `pyarrow`/`dora` dependencies.
"""

from __future__ import annotations

from .tool import Tool, ToolResult, ToolRegistry, FunctionTool
from .provider import (
    LlmProvider,
    ChatConfig,
    ChatResponse,
    MockProvider,
    ReactiveMockProvider,
    OpenAIProvider,
)
from .failover import RetryProvider, ProviderChain, AdaptiveRouter
from .skills import (
    SkillInfo,
    SkillRegistry,
    load_skill,
    load_skills,
    skills_to_prompt,
)
from .agent import Agent, AgentConfig

__all__ = [
    "Agent",
    "AgentConfig",
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "FunctionTool",
    "LlmProvider",
    "ChatConfig",
    "ChatResponse",
    "MockProvider",
    "ReactiveMockProvider",
    "OpenAIProvider",
    "RetryProvider",
    "ProviderChain",
    "AdaptiveRouter",
    "SkillInfo",
    "SkillRegistry",
    "load_skill",
    "load_skills",
    "skills_to_prompt",
]
