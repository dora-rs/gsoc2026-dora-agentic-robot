# Week 5 — LLM Providers + 3-Layer Failover

**Goal (per the accepted proposal):** give the Week 4 `Agent` loop a real brain
and make it resilient. A concrete `OpenAIProvider` (GPT-4o, token metrics)
behind the existing `LlmProvider` interface, plus a 3-layer failover stack —
`RetryProvider` → `ProviderChain` → `AdaptiveRouter` — so one flaky endpoint
never drops a robot command. Every layer is itself an `LlmProvider`, so the
whole stack composes and slots into the loop in place of a bare provider.

## What's added

| Path | Purpose |
|------|---------|
| `agent/provider.py` | New `OpenAIProvider` (lazy `openai` import, GPT-4o default, token/late-failure metrics). `LlmProvider` ABC gains `chat_stream` + `report_late_failure` defaults. |
| `agent/failover.py` | The 3-layer stack: `RetryProvider` (Layer 1), `ProviderChain` (Layer 2), `AdaptiveRouter` (Layer 3) — each an `LlmProvider`. |
| `agent/failover_demo.py` | Self-running demo (`python -m agent.failover_demo`) — a flaky deterministic provider absorbed by the stack. No API key. |
| `tests/test_failover.py` | 13 tests: retry/backoff, chain fall-through, adaptive routing + scoring, late-failure penalty, full stack through the `Agent` loop. |
| `tests/test_openai_provider.py` | 7 tests: response/tool-call translation + token accounting via an injected fake client (no network, no `openai` dep). |

## The 3-layer stack

```
                      ┌──────────────────────────────────────────────┐
 Agent.process_message│  AdaptiveRouter   (Layer 3)                   │
        │             │    route to lowest-scoring provider          │
        ▼             │      │                                       │
 provider.chat(...) ──┼──────┤  ProviderChain  (Layer 2)             │
                      │      │    try each in order until one works  │
                      │      │      │                                │
                      │      │      ▼  RetryProvider  (Layer 1)       │
                      │      │         retry one provider w/ backoff │
                      │      │            │                          │
                      │      │            ▼  OpenAIProvider / Mock    │
                      └──────────────────────────────────────────────┘
```

- **Layer 1 — `RetryProvider`**: wraps a *single* provider, retrying transient
  failures with exponential backoff (`base_delay * 2**attempt`, `max_retries=3`).
  Re-raises the last error once attempts are exhausted, handing control upward.
- **Layer 2 — `ProviderChain`**: an ordered list of providers; tries each until
  one succeeds, counts failovers, and attributes late failures / metrics to the
  provider currently serving. Raises only if *all* fail.
- **Layer 3 — `AdaptiveRouter`**: scores each provider by live error-rate (×1000)
  + average latency and routes each call to the lowest score, falling through to
  the rest on failure. Scores start equal, so it prefers the first provider until
  the metrics say otherwise.

The layers are independent — use any subset. `RetryProvider(OpenAIProvider(...))`
alone is a valid provider; so is `AdaptiveRouter([primary, backup])`.

## Resilience contract

| Failure mode | Handled by | Behaviour |
|--------------|-----------|-----------|
| Transient endpoint error (timeout, 5xx) | `RetryProvider` | Backoff + retry, transparent to caller. |
| One provider hard-down | `ProviderChain` | Fall through to the next provider. |
| One provider slow / erroring over time | `AdaptiveRouter` | Score rises, traffic shifts to a healthier provider. |
| Valid-looking but unusable response | `report_late_failure` | Penalises the serving provider's score for future routing. |

## OpenAIProvider

Mirrors the OpenAI chat-completions contract the loop already speaks. The
`openai` SDK is imported **lazily** inside `__init__`, so importing the package
and running the entire test suite needs neither the dependency nor a key. A
client can be injected for testing.

```python
from agent import Agent, OpenAIProvider, RetryProvider, AdaptiveRouter, ToolRegistry

primary = RetryProvider(OpenAIProvider("gpt-4o"))
backup  = RetryProvider(OpenAIProvider("gpt-4o-mini"))
agent   = Agent(AdaptiveRouter([primary, backup]), ToolRegistry())
agent.process_message("Move the arm home.")     # OPENAI_API_KEY in env
```

Install the optional extra: `pip install -e ".[llm]"`.

## Scope boundary (what is *not* in Week 5)

- No dora bridge node yet — the agent still runs in-process against the registry.
  The bridge that replaces `command_source` lands in **Week 6** alongside the
  `SKILL.md` system-prompt loader.
- Streaming is stubbed (`chat_stream` yields once); true token streaming is a
  later optimisation.
- The real dora transport / motion / introspection tools arrive in **Week 7-8**,
  registered into the same `ToolRegistry`.

## Run it

```bash
python -m agent.failover_demo        # flaky provider absorbed by the stack; no API key
```

## Tests

```bash
PYTHONPATH=. pytest tests/ -q        # 67 passed (47 prior + 20 Week 5)
```

## Validation status

- ✅ `RetryProvider` retry-then-succeed, retry-count metric, and re-raise after
  exhaustion unit-tested (`base_delay=0`, no real sleeping).
- ✅ `ProviderChain` fall-through, failover counting, all-fail error, and the
  empty-list guard unit-tested.
- ✅ `AdaptiveRouter` preference order, route-around-failure, score-based
  re-routing, late-failure penalty, and all-fail error unit-tested.
- ✅ Full stack (`RetryProvider` → `ProviderChain` → `AdaptiveRouter`) driven end
  to end through the `Agent` loop.
- ✅ `OpenAIProvider` response/tool-call translation and token accounting tested
  with an injected fake client — no network, no `openai` dependency.
- ⚠️ A live OpenAI call is not exercised in CI (needs a key); the translation is
  validated against the documented chat-completions response shape.

## Next (Week 6)

The dora **bridge node** that replaces the Week 3 `command_source` placeholder —
wiring this `Agent` (now with a real provider stack) onto dora inputs/outputs —
plus a `SKILL.md` loader for system-prompt injection.
