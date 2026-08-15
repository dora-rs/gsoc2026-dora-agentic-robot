# Documentation

**Agentic Dora** — an LLM agent driving a simulated robot through real motion
planning on the [dora-rs](https://dora-rs.ai) dataflow runtime. A natural-language
instruction becomes a planned, collision-checked, physically-executed
pick-and-place.

![Pick-and-place in MuJoCo: the UR5e grasps the red ball and places it on the green plate](assets/pick_and_place.gif)

*The pick-and-place under real physics — the motions the agent issues, rendered
from MuJoCo (`python scripts/render_demo.py`).*

## Guides

| Guide | What it covers |
|-------|----------------|
| **[final-submission.md](final-submission.md)** | The deliverables checklist (requirement → where it lives → how to run it), the 3-command tour, and verification status. Start here for an overview. |
| **[architecture.md](architecture.md)** | How it fits together — the agent-as-source idea, the dataflow topology diagram, and inside the agent bridge. |
| **[setup.md](setup.md)** | From clone to a running pick-and-place in under 5 minutes. Zero-dependency demos and the full simulator pipeline. |
| **[tool-reference.md](tool-reference.md)** | Every agent tool: JSON schemas, examples, return shapes, and error handling. |
| **[skill-authoring.md](skill-authoring.md)** | Writing `SKILL.md` — procedural knowledge for the agent, for new tasks and robots. |
| **[pipeline-authoring.md](pipeline-authoring.md)** | The dora dataflow YAML format, timers, wire encodings, and the `dataflow_check` validator. |
| **[extending.md](extending.md)** | Adding a tool, an LLM provider, or a whole new robot. |

## Try it now

```bash
pip install -e ".[dev]"
python -m agent.pick_place_demo          # pick-and-place, no dora/MuJoCo/key needed
```

Then the full simulator pipeline — see [setup.md](setup.md).

## Build log

The project was built in weekly increments; each note is a self-contained story of
one step, with its own validation status.

| Week | Deliverable |
|------|-------------|
| [1](week1-simulation.md) | UR5e MuJoCo simulation as a dora node |
| [2](week2-gripper.md) | Robotiq 2F-85 gripper controller |
| [3](week3-dataflow.md) | Full motion-planning dataflow + connectivity validator |
| [4](week4-agent-core.md) | Agent loop + ToolRegistry |
| [5](week5-llm-providers.md) | LLM providers + 3-layer failover |
| [6](week6-agent-bridge.md) | Agent bridge node + SKILL.md skills |
| [7](week7-motion-tools.md) | Motion tools + on-demand skills + real dataflow test |
| [8](week8-recovery-and-real-sim.md) | Replanning recovery + the agent on real MuJoCo |
| [9](week9-live-llm.md) | A live LLM completes the task |
| [10](week10-real-planner.md) | The real dora-moveit2 RRT-Connect planner in the loop |
| [11](week11-collision-checking.md) | Collision checking; the pipeline runs live over dora |
| [12](week12-dynamic-scene.md) | A runtime scene the agent can shape |
| 13 | Documentation & demo polish *(this set of guides)* |
