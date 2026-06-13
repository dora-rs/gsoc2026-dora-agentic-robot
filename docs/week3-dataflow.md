# Week 3 — Full Motion-Planning Dataflow & I/O

**Goal (per the accepted proposal):** full pipeline connectivity —
`mujoco_sim ↔ trajectory_executor ↔ planner ↔ ik_solver ↔ planning_scene ↔ gripper_controller`
— with sensor outputs and actuator inputs exposed as node I/O.
**Phase I outcome:** a simulation where the arm tracks commanded joints and the gripper grasps objects.

## What's added

| Path | Purpose |
|------|---------|
| `dataflows/ur5e_full_pipeline.yml` | The complete 7-node dataflow wiring all subsystems end to end. |
| `simulation/dataflow_check.py` | Static connectivity validator — flags dangling wires before `dora start` (no MuJoCo/dora needed). CLI + library. |
| `tests/test_dataflow_topology.py` | 7 tests asserting the pipeline is fully wired (no dangling edges, sensor/actuator I/O present, closed loop). |

## Topology

```
                 ┌──────────────── command_source (placeholder; → agent in Phase II) ────────────────┐
                 │ plan_request    ik_request    scene_command    cartesian_trajectory   gripper_command
                 ▼                 ▼              ▼                 ▼                       ▼
            ┌─────────┐       ┌──────────┐  ┌───────────────┐                       ┌───────────────────┐
            │ planner │       │ik_solver │  │planning_scene │                       │ gripper_controller│
            └────┬────┘       └────┬─────┘  └──────┬────────┘                       └─────────┬─────────┘
        trajectory│ plan_status    │ik_solution    │scene_update                     gripper_ctrl│ gripper_status
                 ▼                 │               ▼                                            ▼
        ┌────────────────────┐     │        (planner/scene_update)                     ┌──────────────┐
        │ trajectory_executor│◀────┘ (status feedback to command_source)               │  mujoco_sim  │
        └─────────┬──────────┘                                                          │  (UR5e+2F85) │
         joint_commands│ execution_status                                               └──────┬───────┘
                 ▼                                                                  joint_positions│ velocities│ sensor_data
            ┌──────────┐                                                                          │
            │mujoco_sim│◀──── control_input ── joint_commands                                      │
            │          │◀──── gripper_ctrl ──── gripper_controller                                 │
            └──────────┘◀───────────── joint_positions feeds executor / scene / command_source ◀───┘
```

## Per-node I/O contract

| Node | Source | Inputs | Outputs |
|------|--------|--------|---------|
| `mujoco_sim` | this repo | tick, control_input←`trajectory_executor/joint_commands`, gripper_ctrl←`gripper_controller/gripper_ctrl` | joint_positions, joint_velocities, sensor_data |
| `planning_scene` | dora-moveit2 | robot_state←`mujoco_sim/joint_positions`, scene_command←`command_source/scene_command`, tick | scene_update, scene_state, command_result |
| `planner` | dora-moveit2 | plan_request←`command_source/plan_request`, scene_update←`planning_scene/scene_update` | trajectory, plan_status |
| `ik_solver` | dora-moveit2 | ik_request←`command_source/ik_request` | ik_solution, ik_status |
| `trajectory_executor` | dora-moveit2 | trajectory←`planner/trajectory`, cartesian_trajectory←`command_source/cartesian_trajectory`, joint_positions←`mujoco_sim/joint_positions`, tick | joint_commands, execution_status |
| `gripper_controller` | this repo (Week 2) | gripper_command←`command_source/gripper_command` | gripper_ctrl, gripper_status |
| `command_source` | placeholder (`dynamic`) | all state + status feedback | plan_request, ik_request, scene_command, cartesian_trajectory, gripper_command |

`command_source` is a `dynamic` placeholder this week — the **Octos agent bridge replaces it in Phase II (Week 5+)**. Its I/O contract is already the agent's contract, so the swap is drop-in.

## Dependency: dora-moveit2

The four motion-planning nodes come from [`dora-rs/dora-moveit2`](https://github.com/dora-rs/dora-moveit2). The dataflow references them at `../../dora-moveit2/...`, i.e. dora-moveit2 checked out **alongside** this repo:

```
<workspace>/
├── gsoc2026-dora-agentic-robot/   (this repo)
└── dora-moveit2/                  (clone of dora-rs/dora-moveit2)
```

```bash
git clone https://github.com/dora-rs/dora-moveit2 ../dora-moveit2
pip install -e ../dora-moveit2/dora_moveit/
pip install -e ../dora-moveit2/examples/move_group_demo/
```

## Validate & run

```bash
# Static wiring check — no MuJoCo/dora-moveit2 needed:
PYTHONPATH=. python -m simulation.dataflow_check dataflows/ur5e_full_pipeline.yml
PYTHONPATH=. pytest tests/ -q                    # 27 passed (11+9+7)

# End-to-end (needs dora-moveit2 + meshes):
bash scripts/fetch_ur5e_assets.sh
dora up
MUJOCO_HEADLESS=1 dora start dataflows/ur5e_full_pipeline.yml
dora stop
```

## Validation status

- ✅ Full-pipeline topology validated: 7 nodes, **all input edges resolve**, sensor/actuator I/O present, closed loop (`tests/test_dataflow_topology.py`).
- ✅ The connectivity checker also passes on the Week 1 and Week 2 dataflows.
- ⚠️ End-to-end execution (OMPL planning → execution → grasp) was **not** run in the authoring environment (no MuJoCo / dora-moveit2 install there). The wiring mirrors the proven `move_group_demo` and `octos_agent_demo` dataflows; needs a reviewer run.

## Next (Week 4)

Agent core: the `Agent` loop + `ToolRegistry` (LRU + base-tool pinning) — the
brain that will replace `command_source`.
