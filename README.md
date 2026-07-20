# gsoc2026-dora-agentic-robot

GSOC 2026 | Agentic DORA — agent-driven framework for intelligent robot control, autonomous decision-making, and robotic task orchestration.

## Status

- **Week 1 — UR5e MuJoCo simulation environment.** Reproducible MuJoCo scene (UR5e + Robotiq 2F-85, red ball, green plate target) as a dora node. → **[docs/week1-simulation.md](docs/week1-simulation.md)**
- **Week 2 — Robotiq 2F-85 gripper controller.** `gripper_command` → `gripper_ctrl` (0=open … 255=closed) with a self-running demo dataflow. → **[docs/week2-gripper.md](docs/week2-gripper.md)**
- **Week 3 — Full motion-planning dataflow.** End-to-end wiring (sim ↔ executor ↔ planner ↔ ik ↔ scene ↔ gripper) + a static connectivity validator. → **[docs/week3-dataflow.md](docs/week3-dataflow.md)**
- **Week 4 — Agent core + ToolRegistry.** The `Agent` tool-calling loop + `AgentConfig`, and a `ToolRegistry` with LRU lifecycle and base-tool pinning — the brain that replaces `command_source`. Self-running demo, no LLM needed. → **[docs/week4-agent-core.md](docs/week4-agent-core.md)**
- **Week 5 — LLM providers + failover.** A real `OpenAIProvider` (GPT-4o, token metrics) and a 3-layer failover stack (`RetryProvider` → `ProviderChain` → `AdaptiveRouter`), each an `LlmProvider` that drops straight into the `Agent` loop. Self-running failover demo, no API key needed. → **[docs/week5-llm-providers.md](docs/week5-llm-providers.md)**
- **Week 6 — Agent bridge + skills.** The dora bridge node that replaces `command_source`: the `Agent` drives the motion pipeline through `dora_read`/`dora_send`/`dora_call` tools, with behaviour authored as `SKILL.md` files injected into the system prompt. Self-running in-process demo, no dora/MuJoCo/LLM needed. → **[docs/week6-agent-bridge.md](docs/week6-agent-bridge.md)**

```bash
pip install -e .
bash scripts/fetch_ur5e_assets.sh                       # one-time: fetch meshes
dora up
MUJOCO_HEADLESS=1 dora start dataflows/ur5e_sim.yml             # Week 1: sim only
MUJOCO_HEADLESS=1 dora start dataflows/ur5e_gripper_demo.yml    # Week 2: gripper demo
MUJOCO_HEADLESS=1 dora start dataflows/ur5e_full_pipeline.yml   # Week 3: full pipeline (needs dora-moveit2)
dora stop

python -m agent.agent_demo                                      # Week 4: agent core demo (no LLM/dora/MuJoCo)
python -m agent.failover_demo                                   # Week 5: 3-layer failover demo (no API key)
python -m agent.bridge_demo                                     # Week 6: agent-bridge demo (no dora/MuJoCo/LLM)
MUJOCO_HEADLESS=1 OCTOS_PROVIDER=mock dora start dataflows/ur5e_agent_demo.yml   # Week 6: agent drives the live pipeline (needs dora-moveit2)
```

Validate dataflow wiring without a simulator:

```bash
PYTHONPATH=. python -m simulation.dataflow_check dataflows/ur5e_full_pipeline.yml
```

Tests (no MuJoCo/dora/display required):

```bash
pip install -e ".[dev]"
PYTHONPATH=. pytest tests/ -q
```

## Layout

```
agent/           Agent loop, ToolRegistry, LLM providers + failover, dora bridge, SKILL.md loader
simulation/      UR5e MuJoCo node, gripper + mission sources, named poses, scene model
skills/          SKILL.md domain knowledge injected into the agent's system prompt
dataflows/       dora dataflow configs
scripts/         asset fetch helper
tests/           unit tests
docs/            per-week deliverable notes
```
