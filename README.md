# gsoc2026-dora-agentic-robot

GSOC 2026 | Agentic DORA — agent-driven framework for intelligent robot control, autonomous decision-making, and robotic task orchestration.

## Status

- **Week 1 — UR5e MuJoCo simulation environment.** Reproducible MuJoCo scene (UR5e + Robotiq 2F-85, red ball, green plate target) as a dora node. → **[docs/week1-simulation.md](docs/week1-simulation.md)**
- **Week 2 — Robotiq 2F-85 gripper controller.** `gripper_command` → `gripper_ctrl` (0=open … 255=closed) with a self-running demo dataflow. → **[docs/week2-gripper.md](docs/week2-gripper.md)**
- **Week 3 — Full motion-planning dataflow.** End-to-end wiring (sim ↔ executor ↔ planner ↔ ik ↔ scene ↔ gripper) + a static connectivity validator. → **[docs/week3-dataflow.md](docs/week3-dataflow.md)**

```bash
pip install -e .
bash scripts/fetch_ur5e_assets.sh                       # one-time: fetch meshes
dora up
MUJOCO_HEADLESS=1 dora start dataflows/ur5e_sim.yml             # Week 1: sim only
MUJOCO_HEADLESS=1 dora start dataflows/ur5e_gripper_demo.yml    # Week 2: gripper demo
MUJOCO_HEADLESS=1 dora start dataflows/ur5e_full_pipeline.yml   # Week 3: full pipeline (needs dora-moveit2)
dora stop
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
simulation/      UR5e MuJoCo node, named poses, scene model
dataflows/       dora dataflow configs
scripts/         asset fetch helper
tests/           unit tests
docs/            per-week deliverable notes
```
