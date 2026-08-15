# Final submission — Agentic Dora

Agent-driven control of a simulated robot: a natural-language instruction becomes
a planned, collision-checked, physically-executed pick-and-place on the
[dora-rs](https://dora-rs.ai) dataflow runtime. This page maps the project's
required deliverables to where they live, and gives the definitive commands to
run and verify everything.

## Deliverables checklist

| Requirement | Where it is | Run it |
|-------------|-------------|--------|
| **Runs fully in simulation** (no hardware) | MuJoCo UR5e + Robotiq scene as a dora node — `simulation/mujoco_node.py`, `simulation/models/ur5e_scene.xml` | `dora run dataflows/ur5e_mujoco_agent.yml --stop-after 30s` |
| **Mock agent for testing without external APIs** | `MockProvider` / `ReactiveMockProvider` (`agent/provider.py`); every dataflow has a mock variant (`OCTOS_PROVIDER=mock`) | `python -m agent.pick_place_demo` |
| **Simulation environment** | UR5e + Robotiq 2F-85, red ball + green plate; real actuators, contact, `nq=21` | `dora run dataflows/ur5e_sim.yml --stop-after 5s` |
| **Agent bridge node** | The LLM-driven node that replaces `command_source` — `agent/bridge_node.py`, `agent/bridge.py`, `agent/agent.py` | (used by every `*agent*` / `*planner*` dataflow) |
| **Agent-callable robot tools** | `dora_move`, `dora_gripper`, `dora_perceive`, `dora_list`, `dora_obstacle`, `skill`, transport tools — `agent/motion_tools.py` | see [tool-reference.md](tool-reference.md) |
| **End-to-end demo** | Natural language → pick-and-place, outcome-verified (ball on plate) | `python -m agent.pick_place_demo` (mock) · `python -m agent.live_planner_demo` (GPT-4o) |
| **Example dataflow config** | 10 dataflows in `dataflows/`, validated in CI | [pipeline-authoring.md](pipeline-authoring.md) |
| **Documentation** | This `docs/` set — architecture, setup, tool reference, authoring + extension guides | [docs/README.md](README.md) |

## Beyond the baseline

- **A real LLM drives the task.** GPT-4o discovers and activates the skill,
  perceives, and issues every motion itself — verified by outcome (Week 9+).
- **Real motion planning.** The genuine dora-moveit2 RRT-Connect planner produces
  multi-waypoint paths; a trajectory executor follows them on real physics
  (Week 10).
- **Collision checking.** MuJoCo-exact UR5e forward kinematics drive real
  collision checking; paths route around obstacles and a blocked grasp triggers
  task-level recovery (Week 11).
- **A runtime scene the agent can shape.** Obstacles are added/removed at runtime;
  the model can register one it's told about and the planner routes around it
  (Week 12).
- **Captured live over dora.** A live GPT-4o completing the pick-and-place through
  the collision-checked planner on the real MuJoCo simulator, over dora, end to
  end (Weeks 11–12).

## The 3-command tour

```bash
pip install -e ".[dev]"

# 1. Instant, no dependencies — the agent completes the task (ball on plate):
python -m agent.pick_place_demo

# 2. On the real simulator over dora (fetch meshes once):
bash scripts/fetch_ur5e_assets.sh
MUJOCO_HEADLESS=1 dora run dataflows/ur5e_mujoco_agent.yml --stop-after 30s

# 3. A live GPT-4o through the collision-checked planner (needs a key):
OPENAI_API_KEY=sk-... MUJOCO_HEADLESS=1 \
  dora run dataflows/ur5e_mujoco_planner_llm.yml --stop-after 150s
```

See [setup.md](setup.md) for the full guide and troubleshooting.

## Verification status

- **199 unit tests** pass with no simulator, dora, or API key
  (`pytest tests/ -q -m "not integration"`).
- **Integration tests** run real dora dataflows on the real MuJoCo simulator
  (`pytest tests/ -q` — loopback, recovery, MuJoCo, and the collision-checked
  planner over dora).
- **Live-LLM tests** are opt-in, double-gated on `OPENAI_API_KEY` **and**
  `RUN_LIVE_LLM=1`, so an ordinary run makes no paid call.
- Every shipped dataflow is topology-validated (`tests/test_dataflow_topology.py`).

## Repository map

```
agent/           Agent loop, ToolRegistry, LLM providers + failover, dora bridge,
                 robot tools (planning + collision), skills
simulation/      UR5e MuJoCo node, gripper + mission sources, sim executor,
                 RRT-Connect planner + trajectory executor, UR5e config,
                 forward kinematics + collision, named poses, scene
skills/          SKILL.md domain knowledge injected into the agent's prompt
dataflows/       dora dataflow configs (10)
scripts/         asset fetch + demo GIF render
docs/            these guides + the per-week build log
tests/           ~200 unit tests + real-dataflow integration tests
```

## Build log

The project was built in weekly increments — the `weekN-*.md` notes in this folder
each tell the story of one step with its own validation status, from the Week 1
simulation to the live-LLM collision-checked pipeline.
