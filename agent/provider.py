"""LLM provider interface, a deterministic MockProvider, and a real OpenAIProvider.

Week 4 landed the *interface* the `Agent` loop drives, plus a scripted
`MockProvider` so the loop is fully testable in CI without an API key or network.

**Week 5** adds the real `OpenAIProvider` (GPT-4o, token metrics) here, and the
3-layer failover stack (`RetryProvider` → `ProviderChain` → `AdaptiveRouter`) in
the sibling `agent/failover.py`. Every failover wrapper is itself an
`LlmProvider`, so the stack composes and drops straight into the `Agent` loop.
The shapes here (`ChatConfig`, `ChatResponse`, the OpenAI-style `tool_calls`
dicts) match the OpenAI chat-completions contract so the swap is drop-in.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator


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

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        config: ChatConfig,
    ) -> Iterator[ChatResponse]:
        """Streaming chat — yields partial responses. Default: a single yield."""
        yield self.chat(messages, tools, config)

    def context_window(self) -> int:
        return 128_000

    def export_metrics(self) -> dict:
        """Provider-specific counters (token usage, calls). Default: none."""
        return {}

    def report_late_failure(self, error: str) -> None:
        """Report a failure discovered after `chat` returned (e.g. invalid JSON).

        The failover stack uses this to penalise a provider that returned a
        syntactically-valid but unusable response. Default: no-op.
        """


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


class OpenAIProvider(LlmProvider):
    """Real OpenAI chat-completions provider (GPT-4o by default).

    The `openai` SDK is imported lazily so importing this module — and running
    the whole test suite — never requires the dependency or an API key. Install
    the extra with ``pip install -e ".[llm]"`` to use it for real.

    A pre-built `client` may be injected (used by the unit tests to exercise the
    response translation and token accounting without any network call).
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        client: object | None = None,
    ):
        self._model = model
        self._total_tokens = 0
        self._calls = 0
        self._late_failures = 0
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI  # lazy: optional dependency

            key = api_key or os.environ.get("OPENAI_API_KEY", "")
            self._client = OpenAI(api_key=key)

    def name(self) -> str:
        return f"openai/{self._model}"

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        config: ChatConfig,
    ) -> ChatResponse:
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": config.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if config.max_tokens:
            kwargs["max_tokens"] = config.max_tokens

        self._calls += 1
        response = self._client.chat.completions.create(**kwargs)
        if getattr(response, "usage", None):
            self._total_tokens += response.usage.total_tokens or 0

        choice = response.choices[0]
        msg = choice.message

        tool_calls: list[dict] = []
        for tc in (msg.tool_calls or []):
            tool_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })

        return ChatResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
        )

    def export_metrics(self) -> dict:
        return {
            "provider": self.name(),
            "model": self._model,
            "calls": self._calls,
            "total_tokens": self._total_tokens,
            "late_failures": self._late_failures,
        }

    def report_late_failure(self, error: str) -> None:
        self._late_failures += 1
