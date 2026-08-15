# Setup guide — running in under 5 minutes

From a fresh clone to a robot completing a pick-and-place. Two paths: a
**no-dependencies demo** (runs in seconds, no MuJoCo, no dora, no API key) and the
**full simulator pipeline** over dora.

## 0. Prerequisites

- Python 3.10+
- For the full pipeline: the [`dora` CLI](https://dora-rs.ai/docs/guides/Installation/installing)
  (`cargo install dora-cli` or a release binary) — check with `dora --version`.
- A live LLM run also needs an `OPENAI_API_KEY`. Everything else runs without one.

## 1. Install (about 1 minute)

```bash
git clone https://github.com/dora-rs/gsoc2026-dora-agentic-robot
cd gsoc2026-dora-agentic-robot
pip install -e ".[dev]"          # library + MuJoCo + test deps
```

## 2. The instant demo — no dora, no MuJoCo, no key

The pick-and-place through the stateful in-memory node — proves the agent, tools,
and skills end to end, judged by outcome (ball on plate):

```bash
python -m agent.pick_place_demo
```

You should see the tool sequence (`skill activate` → `dora_perceive` →
`dora_move` … `dora_gripper` …) and `✅ Ball is on the green plate`.

Other zero-dependency demos:

```bash
python -m agent.agent_demo        # the agent loop + ToolRegistry (no LLM)
python -m agent.failover_demo     # the 3-layer LLM failover stack (no key)
python -m agent.bridge_demo       # the agent driving the bridge (no dora/MuJoCo)
```

## 3. The full simulator pipeline (about 2 minutes)

Fetch the UR5e meshes once, then run a real dora dataflow:

```bash
bash scripts/fetch_ur5e_assets.sh                      # one-time mesh download

# The agent driving the real MuJoCo sim through direct control (self-contained —
# no motion-planning deps beyond this repo), scripted mock provider, no API key:
MUJOCO_HEADLESS=1 dora run dataflows/ur5e_mujoco_agent.yml --stop-after 30s

# Or through the real RRT-Connect planner with collision checking:
MUJOCO_HEADLESS=1 dora run dataflows/ur5e_mujoco_planner.yml --stop-after 70s
```

> The `*_planner*.yml` dataflows import the RRT-Connect algorithm from the
> sibling [dora-moveit2](https://github.com/dora-rs/dora-moveit2) checkout
> (expected next to this repo, or point `DORA_MOVEIT2_PATH` at its `dora_moveit`
> package dir). The `ur5e_mujoco_agent.yml` and `ur5e_agent_*` dataflows need only
> this repo.

`dora run` starts an embedded coordinator, runs the graph, and stops after the
window. You'll see the planner plan each motion and the arm settle on the real
physics, ending with `Pick-and-place complete`.

Prefer the long-running form? `dora up` once, then
`dora start <dataflow>` / `dora stop`.

## 4. A live LLM (optional — needs a key)

```bash
export OPENAI_API_KEY=sk-...

# In-process, outcome-verified (real GPT-4o, no dora/MuJoCo needed):
python -m agent.live_planner_demo

# The same live model through the real simulator over dora:
MUJOCO_HEADLESS=1 dora run dataflows/ur5e_mujoco_planner_llm.yml --stop-after 150s
```

The live tests never fire on an ordinary run — they are double-gated on
`OPENAI_API_KEY` **and** `RUN_LIVE_LLM=1`, so `pytest` makes no paid call by
accident.

## 5. Tests

```bash
PYTHONPATH=. pytest tests/ -q -m "not integration"   # ~200 unit tests, seconds
PYTHONPATH=. pytest tests/ -q                         # + real dora dataflow runs
RUN_LIVE_LLM=1 OPENAI_API_KEY=sk-... pytest tests/test_live_llm.py -q  # opt-in live
```

## 6. Validate a dataflow without running it

```bash
PYTHONPATH=. python -m simulation.dataflow_check dataflows/ur5e_mujoco_planner.yml
```

Static wiring check — flags any input wired to a node/output that doesn't exist.

## Troubleshooting

- **`dora: command not found`** — install the dora CLI (step 0). The in-process
  demos (steps 2, 4-in-process) don't need it.
- **`MuJoCo model not found … fetch the meshes`** — run
  `bash scripts/fetch_ur5e_assets.sh`.
- **`Address already in use` on `dora run`** — another dora coordinator is
  holding the port; stop it (`dora destroy`) or wait for it to exit.
- **Live run exits 1 with "OPENAI_API_KEY is unset"** — export the key, or use a
  mock dataflow / the `--mock` demos.
- **No viewer window on macOS** — the interactive MuJoCo viewer needs `mjpython`;
  use `MUJOCO_HEADLESS=1` (as above) to run without it.

Next: [architecture.md](architecture.md) for how it fits together, or
[tool-reference.md](tool-reference.md) for what the agent can do.
