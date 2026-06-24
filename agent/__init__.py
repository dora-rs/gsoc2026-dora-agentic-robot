"""Agent core (Week 4) — the Octos-pattern agent that replaces `command_source`.

This package is the "brain" of the agentic dataflow: an `Agent` loop driving an
`LlmProvider` against a `ToolRegistry`. Week 4 lands the framework only — the
loop, config, registry, the tool abstraction, and a deterministic `MockProvider`
so everything is testable without an LLM or a running dataflow.

Later phases extend this package (all interfaces here are forward-compatible):
  - Week 5 — real `LlmProvider`s (OpenAI) + 3-layer failover
  - Week 6 — SKILL.md loader + the dora bridge node
  - Week 7-8 — the dora transport / motion / introspection tools
"""

from __future__ import annotations

from .tool import Tool, ToolResult, ToolRegistry, FunctionTool
from .provider import LlmProvider, ChatConfig, ChatResponse, MockProvider
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
]
