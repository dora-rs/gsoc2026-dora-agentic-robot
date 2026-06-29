"""3-layer LLM failover stack (Week 5) — mirrors `octos_llm`'s resilience layers.

A robot agent that drops a command because one LLM endpoint hiccuped is worse
than useless. These three wrappers each implement `LlmProvider`, so they compose
and slot straight into the `Agent` loop in place of a bare provider:

    RetryProvider   (Layer 1) — retry one provider with exponential backoff
        ↓ wraps
    ProviderChain   (Layer 2) — fall through an ordered list on hard failure
        ↓ wraps
    AdaptiveRouter  (Layer 3) — route to the best provider by live latency/error score

Typical assembly (primary + backup, each retried, adaptively routed):

    primary = RetryProvider(OpenAIProvider("gpt-4o"))
    backup  = RetryProvider(OpenAIProvider("gpt-4o-mini"))
    provider = AdaptiveRouter([primary, backup])
    agent = Agent(provider, registry)

Every layer is independently testable against a flaky `LlmProvider` double — no
network, no API key (see `tests/test_failover.py` and `agent/failover_demo.py`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .provider import ChatConfig, ChatResponse, LlmProvider


# ---------------------------------------------------------------------------
# Layer 1: RetryProvider — exponential backoff around a single provider
# ---------------------------------------------------------------------------

class RetryProvider(LlmProvider):
    """Retry a single provider with exponential backoff.

    Backoff is `base_delay * 2**attempt`. After `max_retries` extra attempts are
    exhausted the last exception is re-raised, so an outer layer (e.g. a
    `ProviderChain`) can take over.
    """

    def __init__(self, inner: LlmProvider, max_retries: int = 3, base_delay: float = 1.0):
        self._inner = inner
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._retries = 0

    def name(self) -> str:
        return f"retry({self._inner.name()})"

    def chat(self, messages: list[dict], tools: list[dict], config: ChatConfig) -> ChatResponse:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._inner.chat(messages, tools, config)
            except Exception as exc:  # transient endpoint failure — back off and retry
                last_error = exc
                self._retries += 1
                if attempt < self._max_retries:
                    delay = self._base_delay * (2 ** attempt)
                    if delay > 0:
                        time.sleep(delay)
        assert last_error is not None
        raise last_error

    def context_window(self) -> int:
        return self._inner.context_window()

    def export_metrics(self) -> dict:
        return {**self._inner.export_metrics(), "retries": self._retries}

    def report_late_failure(self, error: str) -> None:
        self._inner.report_late_failure(error)


# ---------------------------------------------------------------------------
# Layer 2: ProviderChain — ordered failover across providers
# ---------------------------------------------------------------------------

class ProviderChain(LlmProvider):
    """Try each provider in order until one succeeds.

    Tracks which provider is currently serving (`_active_index`) so late-failure
    reports and metrics attribute to the right endpoint, and counts failovers.
    """

    def __init__(self, providers: list[LlmProvider]):
        if not providers:
            raise ValueError("ProviderChain requires at least one provider")
        self._providers = providers
        self._active_index = 0
        self._failovers = 0

    def name(self) -> str:
        return f"chain({' -> '.join(p.name() for p in self._providers)})"

    def chat(self, messages: list[dict], tools: list[dict], config: ChatConfig) -> ChatResponse:
        errors: list[str] = []
        for i, provider in enumerate(self._providers):
            try:
                result = provider.chat(messages, tools, config)
                if i != self._active_index:
                    self._failovers += 1
                self._active_index = i
                return result
            except Exception as exc:
                errors.append(f"{provider.name()}: {exc}")
        raise RuntimeError("all providers in chain failed: " + "; ".join(errors))

    def context_window(self) -> int:
        return min(p.context_window() for p in self._providers)

    def export_metrics(self) -> dict:
        return {
            "active_provider": self._providers[self._active_index].name(),
            "failovers": self._failovers,
            "providers": [p.export_metrics() for p in self._providers],
        }

    def report_late_failure(self, error: str) -> None:
        self._providers[self._active_index].report_late_failure(error)


# ---------------------------------------------------------------------------
# Layer 3: AdaptiveRouter — score-based routing across providers
# ---------------------------------------------------------------------------

@dataclass
class _ProviderStats:
    """Per-provider running counters used to compute a routing score."""

    calls: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    late_failures: int = 0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.calls if self.calls else 0.0

    @property
    def error_rate(self) -> float:
        return (self.errors + self.late_failures) / self.calls if self.calls else 0.0

    def score(self) -> float:
        """Lower is better — error rate dominates, latency breaks ties."""
        return self.error_rate * 1000.0 + self.avg_latency_ms


class AdaptiveRouter(LlmProvider):
    """Route each call to the lowest-scoring (best-performing) provider.

    Scores start equal (0.0), so the first provider is preferred until live
    latency/error metrics push traffic elsewhere. A failure on the chosen
    provider falls through to the remaining providers for that call.
    """

    def __init__(self, providers: list[LlmProvider]):
        if not providers:
            raise ValueError("AdaptiveRouter requires at least one provider")
        self._providers = providers
        self._stats: dict[str, _ProviderStats] = {p.name(): _ProviderStats() for p in providers}
        self._last_used = providers[0].name()

    def name(self) -> str:
        return f"adaptive({', '.join(p.name() for p in self._providers)})"

    def _best_provider(self) -> LlmProvider:
        return min(self._providers, key=lambda p: self._stats[p.name()].score())

    def chat(self, messages: list[dict], tools: list[dict], config: ChatConfig) -> ChatResponse:
        chosen = self._best_provider()
        # Try the best provider first, then the rest in declaration order.
        ordered = [chosen] + [p for p in self._providers if p.name() != chosen.name()]
        for provider in ordered:
            stats = self._stats[provider.name()]
            stats.calls += 1
            start = time.monotonic()
            try:
                result = provider.chat(messages, tools, config)
                stats.total_latency_ms += (time.monotonic() - start) * 1000.0
                self._last_used = provider.name()
                return result
            except Exception:
                stats.errors += 1
        raise RuntimeError("all providers in adaptive router failed")

    def context_window(self) -> int:
        return min(p.context_window() for p in self._providers)

    def export_metrics(self) -> dict:
        return {
            "last_used": self._last_used,
            "provider_stats": {
                name: {
                    "calls": s.calls,
                    "errors": s.errors,
                    "avg_latency_ms": round(s.avg_latency_ms, 1),
                    "error_rate": round(s.error_rate, 3),
                    "score": round(s.score(), 1),
                }
                for name, s in self._stats.items()
            },
        }

    def report_late_failure(self, error: str) -> None:
        stats = self._stats.get(self._last_used)
        if stats:
            stats.late_failures += 1
