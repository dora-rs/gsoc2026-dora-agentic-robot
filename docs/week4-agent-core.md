# Week 4 — Agent Core + ToolRegistry

**Goal (per the accepted proposal):** the agent core — an `Agent` loop with
`AgentConfig` (max_iterations, max_timeout, tool_timeout, temperature) and a
`ToolRegistry` (registration, discovery, OpenAI-schema export, LRU lifecycle,
base-tool pinning). This is the **brain** that replaces the Week 3
`command_source` placeholder once the LLM provider and dora tools land.

## What's added

| Path | Purpose |
|------|---------|
| `agent/tool.py` | `Tool` ABC + `ToolResult`, a `FunctionTool` adapter, and the `ToolRegistry` (LRU lifecycle, base-tool pinning, tag discovery, OpenAI-schema export). |
| `agent/provider.py` | Minimal `LlmProvider` interface (`ChatConfig`/`ChatResponse`) + a deterministic, scripted `MockProvider` so the loop is testable without an LLM. |
| `agent/agent.py` | The `Agent` tool-calling loop + `AgentConfig`. Self-contained tool execution through the registry, bounded by `tool_timeout_secs`. |
| `agent/agent_demo.py` | Self-running demo (`python -m agent.agent_demo`) — no LLM, no dora, no MuJoCo. |
| `tests/test_tool_registry.py` | 11 tests: registration/discovery, LRU eviction order, base-tool pinning, schema export. |
| `tests/test_agent_loop.py` | 9 tests: tool execution + result feedback, error/timeout handling, `max_iterations` backstop. |

## The agent loop

```
 user_message
      │
      ▼
 ┌────────────────────────────────────────────────────────────┐
 │ for iteration in 1..max_iterations  (and within max_timeout)│
 │                                                             │
 │   tools_schema = registry.to_openai_schema()  ← LRU-bounded │
 │   response     = provider.chat(messages, tools_schema)      │
 │                                                             │
 │   finish_reason == "stop"  ──────────►  return response.text│
 │   else: for each tool_call:                                 │
 │           result = registry.get(name).execute(args)         │
 │                    └─ bounded by tool_timeout_secs          │
 │           append {role: tool, content: result}              │
 │           (errors → {"error": …} so the model can recover)  │
 └────────────────────────────────────────────────────────────┘
```

In Week 4 each tool is a self-contained `Tool` object in the registry, so the
agent executes it directly — no external bridge yet. Every failure mode (unknown
tool, malformed JSON arguments, tool exception, tool timeout) is converted into
an `{"error": …}` payload fed back to the model instead of crashing the loop.

## ToolRegistry — LRU lifecycle + base-tool pinning

The registry bounds how many tool definitions reach the model's context:

- **`max_active`** caps the exported schema. An LLM does not need 50 tool
  definitions in context — only the base tools plus whatever was used recently.
- **Base tools** (`set_base_tools`) are pinned: always present in the schema,
  never evicted, even beyond `max_active`. The agent never loses its essentials.
- **LRU** fills the remaining slots with the most-recently-used non-base tools;
  `get(name)` marks a tool most-recently-used.

```python
reg = ToolRegistry(max_active=2)
for n in ("base", "x", "y", "z"):
    reg.register(make_tool(n))
reg.set_base_tools(["base"])
reg.get("x"); reg.get("y"); reg.get("z")
reg.active_tool_names()   # -> ["base", "z"]  (base pinned, z most-recent)
```

## AgentConfig

| Field | Default | Meaning |
|-------|---------|---------|
| `max_iterations` | 50 | Hard cap on LLM turns per message (runaway-loop backstop). |
| `max_timeout_secs` | 600 | Wall-clock budget for the whole message. |
| `tool_timeout_secs` | 600 | Per-tool execution budget; an over-budget tool is abandoned and reported as an error. |
| `temperature` | 0.1 | Sampling temperature passed through to the provider. |

## Scope boundary (what is *not* in Week 4)

This week is the framework only. To keep the loop testable now, `provider.py`
ships the `LlmProvider` *interface* plus a deterministic `MockProvider`. The
shapes (`ChatConfig`, `ChatResponse`, OpenAI-style `tool_calls`) match the OpenAI
chat-completions contract so the following swaps are drop-in:

- **Week 5** — real `OpenAIProvider` (GPT-4o, token metrics) + 3-layer failover
  (`RetryProvider` → `ProviderChain` → `AdaptiveRouter`).
- **Week 6** — `SKILL.md` loader (system-prompt injection) + the dora bridge node
  that replaces `command_source`.
- **Week 7-8** — the real dora tools (`dora_read`/`dora_send`/`dora_call`,
  motion + introspection) registered into this same `ToolRegistry`.

## Run it

```bash
python -m agent.agent_demo            # self-running; no LLM / dora / MuJoCo
```

## Tests

```bash
PYTHONPATH=. pytest tests/ -q         # 47 passed (27 prior + 20 Week 4)
```

## Validation status

- ✅ `ToolRegistry` LRU eviction order and base-tool pinning unit-tested.
- ✅ `Agent` loop unit-tested against a deterministic `MockProvider`: tool
  execution + result feedback, unknown-tool / bad-args / exception / timeout
  handling, and the `max_iterations` backstop on a runaway loop.
- ✅ End-to-end self-run via `agent.agent_demo` (registry → schema → loop → tool
  → result feedback), all in-process.
- ⚠️ No real LLM is exercised yet — that arrives in Week 5. The loop is validated
  with a scripted provider; the provider interface mirrors the proven
  `dora-octos-bridge` agent so the Week 5 swap is drop-in.

## Next (Week 5)

LLM provider abstraction + failover: `OpenAIProvider` (GPT-4o, token metrics)
and a deterministic `MockProvider`, behind a 3-layer failover stack
(`RetryProvider` → `ProviderChain` → `AdaptiveRouter`).
