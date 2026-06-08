# Week 2 — Robotiq 2F-85 Gripper Controller & Grasping

**Goal (per the accepted proposal):** a tendon-coupled 4-bar-linkage Robotiq 2F-85
gripper with friction/force/contact tuned for reliable physics grasping (no weld
constraints), plus a `gripper_controller` node mapping `gripper_command` →
`gripper_ctrl` (0=open … 255=closed) → `data.ctrl[6]`.

## What's added

| Path | Purpose |
|------|---------|
| `simulation/gripper_controller.py` | dora node: `gripper_command` (JSON) → `gripper_ctrl` (float32 0–255) + `gripper_status`. Pure, testable mapping core. |
| `simulation/gripper_command_source.py` | demo driver — toggles open/close every 2 s so the dataflow self-runs. |
| `dataflows/ur5e_gripper_demo.yml` | command_source → gripper_controller → `mujoco_sim/gripper_ctrl`. |
| `tests/test_gripper_controller.py` | 9 unit tests for the width↔ctrl mapping and command resolution. |

## Gripper model (carried in `ur5e_scene.xml` from Week 1)

The grasping physics already live in the vendored model — these are the proven
parameters from the pick-and-place demo, **using friction, not weld constraints**:

- **Single actuator** `fingers_actuator` (`<general>` on the `split` tendon),
  `ctrlrange 0 255`, `forcerange -20 20` — controls both fingers together.
- **Tendon coupling** synchronizes the left/right driver joints (4-bar linkage).
- **Silicone pad contacts** (`pad_box1`/`pad_box2`) carry the grip friction; the
  red ball uses `friction="2.0 1.0 0.01"`, `condim="6"`, `priority="1"` for a
  stable friction grasp.
- **Contact excludes** between gripper internal links prevent the linkage from
  colliding with itself.

> This week does **not** alter those proven friction values — changing them
> without a physics run risks regressing the grasp. Week 2's contribution is the
> control path (command → actuator) and its tests; tuning refinements, if needed,
> will follow once the grasp is exercised end-to-end with the planner (Week 9).

## Control mapping

`gripper_controller` converts a command to the actuator value the sim applies:

| Command | width (m) | `gripper_ctrl` | state |
|---------|-----------|----------------|-------|
| `{"action":"open"}`  | 0.085 | 0.0   | open |
| `{"action":"close"}` | 0.0   | 255.0 | closed |
| `{"action":"set_position","position":w}` | clamp(w, 0..0.085) | `255·(1 − w/0.085)` | partial |

## Run it

```bash
pip install -e .
bash scripts/fetch_ur5e_assets.sh        # one-time
dora up
MUJOCO_HEADLESS=1 dora start dataflows/ur5e_gripper_demo.yml   # drop HEADLESS for the viewer
dora stop
```

## Tests

```bash
PYTHONPATH=. pytest tests/ -q             # 20 passed (11 Week 1 + 9 Week 2)
```

## Validation status

- ✅ Gripper control mapping + command resolution unit-tested (no simulator).
- ⚠️ Physics grasp not executed in the authoring environment (MuJoCo not installed there); needs a reviewer run after `fetch_ur5e_assets.sh`. The control path mirrors the working `dora-octos-bridge` gripper node.

## Next (Week 3)

Full dataflow wiring + sensor/actuator I/O across mujoco_sim ↔ trajectory_executor
↔ planner ↔ ik_solver ↔ planning_scene ↔ gripper_controller.
