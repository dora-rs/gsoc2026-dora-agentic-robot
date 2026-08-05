# gsoc2026-dora-agentic-robot

GSOC 2026 | Agentic DORA — agent-driven framework for intelligent robot control, autonomous decision-making, and robotic task orchestration.

## Status

- **Week 1 — UR5e MuJoCo simulation environment.** Reproducible MuJoCo scene (UR5e + Robotiq 2F-85, red ball, green plate target) as a dora node. → **[docs/week1-simulation.md](docs/week1-simulation.md)**
- **Week 2 — Robotiq 2F-85 gripper controller.** `gripper_command` → `gripper_ctrl` (0=open … 255=closed) with a self-running demo dataflow. → **[docs/week2-gripper.md](docs/week2-gripper.md)**
- **Week 3 — Full motion-planning dataflow.** End-to-end wiring (sim ↔ executor ↔ planner ↔ ik ↔ scene ↔ gripper) + a static connectivity validator. → **[docs/week3-dataflow.md](docs/week3-dataflow.md)**
- **Week 4 — Agent core + ToolRegistry.** The `Agent` tool-calling loop + `AgentConfig`, and a `ToolRegistry` with LRU lifecycle and base-tool pinning — the brain that replaces `command_source`. Self-running demo, no LLM needed. → **[docs/week4-agent-core.md](docs/week4-agent-core.md)**
- **Week 5 — LLM providers + failover.** A real `OpenAIProvider` (GPT-4o, token metrics) and a 3-layer failover stack (`RetryProvider` → `ProviderChain` → `AdaptiveRouter`), each an `LlmProvider` that drops straight into the `Agent` loop. Self-running failover demo, no API key needed. → **[docs/week5-llm-providers.md](docs/week5-llm-providers.md)**
- **Week 6 — Agent bridge + skills.** The dora bridge node that replaces `command_source`: the `Agent` drives the motion pipeline through `dora_read`/`dora_send`/`dora_call` tools, with behaviour authored as `SKILL.md` files injected into the system prompt. Self-running in-process demo, no dora/MuJoCo/LLM needed. → **[docs/week6-agent-bridge.md](docs/week6-agent-bridge.md)**
- **Week 7 — Motion tools + on-demand skills + a real dataflow test.** Robot-level tools (`dora_move`, `dora_gripper`, `dora_perceive`, `dora_list`) instead of raw transport, dormant skills the agent activates mid-mission, a full pick-and-place, and an integration test that runs an actual `dora` dataflow — which caught two bugs no in-process test could reach. → **[docs/week7-motion-tools.md](docs/week7-motion-tools.md)**
- **Week 8 — Replanning recovery + the agent on the real MuJoCo sim.** `dora_move` now replans a failed plan transparently (randomised planners fail on unlucky samples), with a real dataflow that injects planner failures to prove recovery across processes. And the full pick-and-place now runs on the **real MuJoCo simulator** headless — real actuators, real settling, real gripper contact — via a `sim_executor` node, no dora-moveit2 needed. → **[docs/week8-recovery-and-real-sim.md](docs/week8-recovery-and-real-sim.md)**
- **Week 9 — A live LLM drives the task.** The scripted provider is out of the loop: real GPT-4o discovers and activates the `pick-and-place` skill, perceives, and issues each motion itself, completing the mission (verified by outcome — ball on plate). Getting there meant advertising dormant skills in the prompt, self-healing sensor reads, and task-level recovery — the failure modes only a real model exposes — plus a `ReactiveMockProvider` to test recovery deterministically. → **[docs/week9-live-llm.md](docs/week9-live-llm.md)**

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

python -m agent.pick_place_demo                                 # Week 7: pick-and-place, verified by outcome
dora run dataflows/ur5e_agent_loopback.yml --stop-after 25s     # Week 7: real dora dataflow, no external deps

dora run dataflows/ur5e_agent_recovery.yml --stop-after 25s     # Week 8: recovery — injected planner failures, agent replans
dora run dataflows/ur5e_mujoco_agent.yml --stop-after 30s       # Week 8: agent drives the REAL MuJoCo sim (needs meshes fetched)

OPENAI_API_KEY=sk-... python -m agent.live_pick_place_demo      # Week 9: a REAL LLM completes the task, verified by outcome
OPENAI_API_KEY=sk-... dora run dataflows/ur5e_mujoco_agent_llm.yml --stop-after 120s  # Week 9: live LLM through the real sim
```

Validate dataflow wiring without a simulator:

```bash
PYTHONPATH=. python -m simulation.dataflow_check dataflows/ur5e_full_pipeline.yml
```

Tests (no MuJoCo/dora/display required):

```bash
pip install -e ".[dev]"
PYTHONPATH=. pytest tests/ -q -m "not integration"  # 164 unit tests (~3s)
PYTHONPATH=. pytest tests/ -q                       # + real dora dataflow runs (~85s)
RUN_LIVE_LLM=1 OPENAI_API_KEY=sk-... pytest tests/test_live_llm.py -q  # opt-in: a real GPT-4o run
```

## Layout

```
agent/           Agent loop, ToolRegistry, LLM providers + failover, dora bridge, robot tools (with replanning), skills
simulation/      UR5e MuJoCo node, gripper + mission sources, pipeline stub, sim executor, named poses, scene model
skills/          SKILL.md domain knowledge injected into the agent's system prompt
dataflows/       dora dataflow configs
scripts/         asset fetch helper
tests/           unit tests
docs/            per-week deliverable notes
```
