# Week 9 — A live LLM drives the pick-and-place

**Goal (per Week 8's "Next"):** take the last big mock out of the loop — the
scripted provider — and have a **real LLM** complete the pick-and-place. Every
prior week ran a `MockProvider` that replayed a fixed tool sequence: the pipeline
was real, but the "agent" made no decisions. This week GPT-4o makes them.

## The experiment that set the week

The first thing I did was point real GPT-4o at the existing tools and skills. It
failed — and the *way* it failed is the whole point, because none of it was
reachable with the scripted mock:

1. **It activated the wrong skill.** The model only saw the always-on skills in
   its prompt; the dormant `pick-and-place` procedure was invisible until
   activated, so it guessed `dora-transport` and never found the recipe.
2. **`dora_move` then failed with "no joint_positions cached".** The tools cached
   sensor state only as a side effect of whatever happened to drain the event
   queue first. The mock's fixed ordering always drained it incidentally; a real
   model that perceived-then-moved did not, so the first move had no start state.
3. **It gave up** instead of recovering.

A scripted run is green on all three. A real model is the test. Fixing them is
Week 9.

## What changed

| Path | Change |
|------|--------|
| `agent/bridge.py` | `compose_system_prompt` now advertises **dormant skills** (name + description) so the model can activate the right procedure directly; new `ensure_cached` actively waits for a sensor reading instead of relying on an incidental drain. |
| `agent/motion_tools.py` | `dora_move` and `dora_perceive` use `ensure_cached`, so the first call of a mission gets joint state reliably. |
| `agent/bridge_node.py` | System prompt now points at the on-demand skill list and states the task-level recovery rule (re-perceive, re-approach on a persistent failure). |
| `agent/provider.py` | `ReactiveMockProvider` — a mock that *reacts* to tool results, for deterministic recovery tests with no API key. |
| `agent/live_pick_place_demo.py` | The live-LLM demo: real GPT-4o, judged by outcome (ball on plate). |
| `dataflows/ur5e_mujoco_agent_llm.yml` | The Week 8 real-MuJoCo dataflow with `OCTOS_PROVIDER=openai` — a live model through the real simulator. |

### Advertising dormant skills

Week 7 kept `always: false` skills dormant to save context, but a real model
can't activate what it can't see. `compose_system_prompt` now appends a catalogue
of the dormant skills — names and one-line descriptions only, not the bodies —
under "Skill procedures available on demand", with an instruction to activate the
matching one first. The bodies still load only on activation; the model just
knows they exist. After this, GPT-4o activates `pick-and-place` directly on the
first turn.

### Self-healing sensor reads

`ensure_cached(input_id)` drains, and if the value still isn't cached, blocks
briefly for the next one (the sim republishes every tick, so it arrives in tens
of milliseconds). `dora_perceive` and `dora_move` use it, so a mission's first
action no longer depends on some earlier call having drained the queue. This also
fixed a latent fragility the mock never exposed.

### Recovery, at two levels

- **Transient** (an unlucky planner sample): handled inside `dora_move` by the
  Week 8 retry loop — the model never sees it.
- **Persistent** (goal unreachable from here): the retries exhaust, the error
  goes back to the model, and the prompt + skill tell it to re-perceive and
  approach from a *different* pose. `ReactiveMockProvider` lets a test drive
  exactly this branch deterministically: a fake node rejects one approach
  persistently, and the agent recovers via an alternate pose. That is the
  judgement a live LLM applies — pinned down without an API key.

## Result

With those changes, GPT-4o completes the whole mission from the bare instruction
"Pick up the red ball and place it on the green plate": it activates the skill,
perceives, and issues each `dora_move`/`dora_gripper` itself, and the ball ends
on the plate. `agent/live_pick_place_demo.py` runs it in-process against the
Week 7 stateful node, so success is judged by **outcome**, not by a call log —
the same bar as the mock demo, now cleared by a real model.

```
[tool] skill({"action":"activate","name":"pick-and-place"})
[tool] dora_perceive({})
[tool] dora_gripper({"action":"open"})
[tool] dora_move({"target":"above_ball"})
[tool] dora_move({"target":"grasp_ball"})
[tool] dora_gripper({"action":"close"})
[tool] dora_move({"target":"lift"})     ... place_plate ... home
>>> Agent: The red ball has been picked up and placed on the green plate.
✅ Live LLM completed the pick-and-place — ball is on the plate.
```

## Run it

```bash
export OPENAI_API_KEY=sk-...

# In-process, outcome-verified (real GPT-4o, no dora/MuJoCo needed):
python -m agent.live_pick_place_demo

# The same live model through the REAL MuJoCo simulator over dora:
bash scripts/fetch_ur5e_assets.sh
dora run dataflows/ur5e_mujoco_agent_llm.yml --stop-after 120s
```

## Tests

```bash
PYTHONPATH=. pytest tests/ -q -m "not integration"   # 164 unit tests, ~3s
RUN_LIVE_LLM=1 pytest tests/test_live_llm.py -q       # opt-in, real API call
```

The live-LLM test is double-gated on `OPENAI_API_KEY` **and** `RUN_LIVE_LLM=1`,
so an ordinary run never makes a paid API call.

## Validation status

- ✅ **A real GPT-4o completes the pick-and-place**, verified by outcome (ball on
  the plate) — both as the `live_pick_place_demo` and the gated `test_live_llm`.
- ✅ **Dormant-skill catalogue**: the model activates the right procedure directly;
  unit-tested that dormant skills appear by name/description but not body.
- ✅ **Self-healing reads**: unit-tested that `dora_move`/`dora_perceive` recover
  the start state from an undrained queue.
- ✅ **Task-level recovery**: `ReactiveMockProvider` test proves the agent
  re-approaches after a persistent, unrecoverable planning failure.
- ⚠️ The **live-LLM dora dataflow** (`ur5e_mujoco_agent_llm.yml`) is
  topology-validated and runs the same verified model through the Week 8
  real-simulator path, but a live model + a paid API make it a **demo, not a CI
  test** — the automated proof of the model is the in-process outcome test above.
- ⚠️ The full **dora-moveit2 OMPL planner** stack is still not wired in
  (`sim_executor` remains the direct-control stand-in) — that is the last
  substitution left.

## Next (Week 10)

Wire the real dora-moveit2 planner into the live-LLM dataflow — OMPL path search
in place of `sim_executor` — so the live model plans collision-aware motions, and
the task-level recovery fires against *real* planner failures rather than
injected ones.
