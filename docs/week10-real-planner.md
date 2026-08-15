# Week 10 — The real planner in the loop

**Goal (per Week 9's "Next"):** put the genuine dora-moveit2 motion planner into
the live-LLM loop — real sampling-based path search in place of `sim_executor`'s
direct joint control — so the model's `dora_move` triggers real planning and a
real multi-waypoint path is executed, not a snap to the goal.

## The one thing that unblocked this

Week 9 kept `sim_executor` because "the full dora-moveit2 stack needs OMPL +
TracIK + toppra installed," and those aren't available here. But the planner half
of dora-moveit2 (`planner_ompl_with_collision_op.py`) turns out to be **pure
NumPy RRT-Connect** — "OMPL" is the algorithm family, not the C++ library. The
missing deps were only ever for IK (TracIK) and time-parameterisation (toppra).
So the real path search *does* run here — which is the whole week.

## What changed

| Path | Change |
|------|--------|
| `simulation/ur5e_moveit_config.py` | A `RobotConfig` for the 6-DOF UR5e (real joint limits from `ur5e_scene.xml`, named poses, coarse collision model) so the GEN72-default planner plans for our arm. |
| `simulation/rrt_planner_node.py` | A repo-local dora node that imports the real `OMPLPlanner` from the sibling dora-moveit2 checkout and speaks the repo's wire contract: `plan_request` → `plan_status` + a flattened `trajectory`. |
| `simulation/trajectory_executor_node.py` | Follows the planner's multi-waypoint path on the real sim: stream a waypoint, wait until the arm settles, advance, and report `execution_status` only at the end. |
| `dataflows/ur5e_mujoco_planner_llm.yml` | The Week 9 real-sim + live-LLM dataflow with `sim_executor` split into `rrt_planner` → `trajectory_executor`. |
| `agent/live_planner_demo.py` | The pick-and-place completed through the real planner, outcome-verified — deterministically (`--mock`) and with a live GPT-4o. |

### Why a repo-local planner node, not the dora-moveit2 example node

Week 6's `ur5e_agent_demo.yml` points straight at dora-moveit2's `move_group_demo`
nodes (`../../dora-moveit2/examples/...`). That couples the dataflow to a
second package being installed and to meshes/toppra for its executor — none of
which run in CI. Instead, `rrt_planner_node.py` lives in *this* repo and imports
only the planner **algorithm** (via `DORA_MOVEIT2_PATH`, defaulting to the
sibling checkout). The dataflow stays repo-local and unit-testable; the
dora-moveit2 dependency is an import inside one node, configured by env.

### The agent contract already fit

No agent-side change was needed. `dora_move` already resolves a named pose to a
numeric goal and sends `{"start": [...], "goal": [...]}` — exactly what the real
planner consumes — then waits for `plan_status` and (optionally)
`execution_status`. Splitting one node into planner + executor is transparent to
the model: the planner emits `plan_status`, the executor emits `execution_status`.

## Result

A real GPT-4o completes the whole pick-and-place with every `dora_move` going
through genuine RRT-Connect path search — 6 plans, 12 waypoints each, and the
ball ends on the plate:

```
[tool] skill({"action":"activate","name":"pick-and-place"})
[tool] dora_perceive({}) ... dora_move(above_ball) ... dora_move(place_plate) ...
>>> Agent: The red ball has been successfully picked up and placed on the green plate.
Pipeline log:
  planned 12 waypoints -> above_ball
  planned 12 waypoints -> grasp_ball
  ... planned 12 waypoints -> place_plate ...
Plans (waypoints each): [12, 12, 12, 12, 12, 12]
Ball position: [0.35, 0.25, 0.001]
✅ Pick-and-place completed through the real planner (6 plans, 72 waypoints total).
```

`agent/live_planner_demo.py` runs this in-process against a `PlannerBackedNode`
(the Week 7 stateful node with its `dora_move` rerouted through the real
`OMPLPlanner`), so success is judged by **outcome** — ball on plate — *and* by
the plans being real multi-waypoint paths, not teleports.

## Run it

```bash
# Deterministic proof the real planner is in the loop (no API key):
python -m agent.live_planner_demo --mock

# The same, driven by a real GPT-4o:
export OPENAI_API_KEY=sk-...
python -m agent.live_planner_demo

# The same real model + real planner through the real MuJoCo sim over dora:
bash scripts/fetch_ur5e_assets.sh
dora run dataflows/ur5e_mujoco_planner_llm.yml --stop-after 150s
```

## Tests

```bash
PYTHONPATH=. pytest tests/ -q -m "not integration"    # 179 unit tests, ~3s
RUN_LIVE_LLM=1 pytest tests/test_live_llm.py -q         # opt-in, real API calls
```

New this week: `test_rrt_planner.py` (the real RRT-Connect produces a valid UR5e
path; failure plumbing), `test_trajectory_executor.py` (the path-following state
machine, driven by a closed-loop fake node), `test_planner_demo.py` (the whole
pick-and-place through the real planner, deterministic), a live-planner case in
`test_live_llm.py`, and topology tests for the new dataflow. Tests that need the
planner skip cleanly on a checkout without dora-moveit2.

## Validation status

- ✅ **A real GPT-4o completes the pick-and-place through the real planner**,
  verified by outcome (ball on plate) and by every motion being a genuine
  multi-waypoint plan — as `live_planner_demo` and the gated `test_live_llm`.
- ✅ **The real RRT-Connect planner runs in-process** for the UR5e (valid path,
  within joint limits) — unit-tested, no OMPL/TracIK/toppra needed.
- ✅ **The trajectory executor** walks the arm through a whole planned path and
  reports completion only at the end — unit-tested via a closed-loop fake node.
- ✅ **Deterministic CI proof** (`test_planner_demo.py`): the full pick-and-place
  completes through the real planner with no API key.
- ⚠️ The **live dora dataflow** (`ur5e_mujoco_planner_llm.yml`) is
  topology-validated and runs the same verified model + planner through the Week
  8 real-simulator path, but — as in Week 9 — a foreign `dora` coordinator daemon
  owned by another user still holds the fixed coordinator port on this machine,
  so no `dora run` could be executed this session. The dataflow's behaviour is
  proven by its parts (planner in-process, executor state machine, agent contract
  unchanged); the automated proof of the model + planner is the in-process
  outcome test.
- ⚠️ **Collision avoidance is not yet active.** dora-moveit2's collision checker
  is stubbed upstream (`is_state_valid` returns `True`) and its FK is
  GEN72-shaped, so the planner does real path *search* but not obstacle
  avoidance. Task-level recovery still triggers on `plan_status: success=false`
  (unit-tested), but the planner seldom emits that until collision checking is
  live — that is Week 11.

## Next (Week 11)

Turn on real collision checking for the UR5e: supply UR5e forward kinematics so
dora-moveit2's `is_state_valid` can reject configurations that hit the table or
the arm's own body, add the ball and plate as scene obstacles, and show the live
model's `dora_move` producing a collision-free path around them — and the
task-level recovery firing when a grasp pose is genuinely unreachable.
