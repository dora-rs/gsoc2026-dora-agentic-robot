"""LLM provider interface + a deterministic MockProvider (Week 4).

Week 4 lands only the *interface* the `Agent` loop drives, plus a scripted
`MockProvider` so the loop is fully testable in CI without an API key or network.

The real providers — `OpenAIProvider` (GPT-4o, token metrics) and the 3-layer
failover stack (`RetryProvider` → `ProviderChain` → `AdaptiveRouter`) — land in
**Week 5**. The shapes here (`ChatConfig`, `ChatResponse`, the OpenAI-style
`tool_calls` dicts) match the OpenAI chat-completions contract so that swap is
drop-in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChatConfig:
    """Per-request generation knobs the Agent passes to the provider."""

    temperature: float = 0.1
    max_tokens: int | None = None


@dataclass
class ChatResponse:
    """A single assistant turn.

    `tool_calls` are OpenAI-style dicts:
        {"id": str, "type": "function",
         "function": {"name": str, "arguments": "<json string>"}}
    `finish_reason` is "stop" when the model is done, "tool_calls" when it wants
    tools executed and the loop to continue.
    """

    content: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"


class LlmProvider(ABC):
    """Abstract LLM provider — mirrors the Octos `LlmProvider` trait."""

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        config: ChatConfig,
    ) -> ChatResponse:
        ...

    def context_window(self) -> int:
        return 128_000

    def export_metrics(self) -> dict:
        """Provider-specific counters (token usage, calls). Default: none."""
        return {}


class MockProvider(LlmProvider):
    """Deterministic provider that replays a scripted sequence of responses.

    Each call to `chat` returns the next `ChatResponse` from `script`. This makes
    the agent loop reproducible in tests: script a tool call, then a final answer,
    and assert the loop executed the tool and returned the answer. Once the script
    is exhausted it returns a terminal "stop" response (so a runaway loop still
    terminates rather than raising).

    `tool_call(name, args, call_id=...)` is a small helper for building the
    OpenAI-style tool-call dicts the script needs.
    """

    def __init__(self, script: list[ChatResponse], name: str = "mock"):
        self._script = list(script)
        self._name = name
        self._index = 0
        self._calls = 0

    def name(self) -> str:
        return self._name

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        config: ChatConfig,
    ) -> ChatResponse:
        self._calls += 1
        if self._index < len(self._script):
            response = self._script[self._index]
            self._index += 1
            return response
        return ChatResponse(content="(mock: script exhausted)", finish_reason="stop")

    def export_metrics(self) -> dict:
        return {"provider": self._name, "calls": self._calls}

    @staticmethod
    def tool_call(name: str, args: dict | str, call_id: str = "call_0") -> dict:
        """Build an OpenAI-style tool-call dict for use in a script."""
        import json

        arguments = args if isinstance(args, str) else json.dumps(args)
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
