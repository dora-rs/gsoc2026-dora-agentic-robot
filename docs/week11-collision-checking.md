# Week 11 — Collision checking, and the pipeline finally runs live

**Goal (per Week 10's "Next"):** turn on real collision checking for the UR5e so
the planner does genuine obstacle avoidance, not just path *search* — supply UR5e
forward kinematics so a configuration can be tested against the table and
obstacles, show the planner routing around them, and the task-level recovery
firing when a grasp is genuinely unreachable.

And a bonus the environment finally allowed: **the foreign `dora` daemon that
held the coordinator port since Week 9 is gone**, so this week the whole pipeline
ran live over dora for real — including a live GPT-4o through the collision-checked
planner on the real MuJoCo simulator.

## What changed

| Path | Change |
|------|--------|
| `simulation/ur5e_kinematics.py` | Analytic UR5e forward kinematics — world-frame position of every arm link for a given configuration. Its parameters are the model's own (`body_pos`/`body_quat`/`jnt_axis`), so it reproduces MuJoCo's `mj_kinematics` **exactly** (validated to 7e-16 m), with no MuJoCo dependency at planning time. |
| `simulation/ur5e_collision.py` | A self-contained collision checker: a coarse capsule model of the arm (spheres swept along the links) tested against the table and box/sphere/cylinder obstacles. No dora-moveit2 dependency; unit-testable anywhere. |
| `simulation/rrt_planner_node.py` | `load_collision_planner()` — the real `OMPLPlanner` with a genuine `is_state_valid` (FK + collision), and start/goal-in-collision returning a clean plan failure. `COLLISION` env (default on) enables it in the node. |
| `agent/live_planner_demo.py` | The live demo now plans **collision-checked** (every plan validated against the table). |
| `dataflows/ur5e_mujoco_planner.yml` | A mock-provider, CI-runnable planner dataflow (collision on) — the deterministic counterpart of the live-LLM one. |

### Forward kinematics, pinned to the simulator

Collision checking is only as trustworthy as its idea of where the arm is.
`ur5e_kinematics.link_positions` composes the UR5e's fixed link transforms and
joint axes — read straight out of `ur5e_scene.xml` via MuJoCo — so it isn't an
approximation of the robot, it *is* the robot's kinematics. `test_ur5e_kinematics`
asserts it against the live `mj_kinematics` across fixed and random configs; the
worst-case error is 7e-16 m. Because the parameters are baked in, FK runs with no
MuJoCo at plan time — thousands of validity checks per plan cost nothing.

### Real collision checking

For a configuration, `config_in_collision` places spheres along the arm's links
(at each link's radius) via that FK and tests them against the table (a ground
plane at the object height) and any workspace obstacles. The model is deliberately
*conservative* — it over-approximates the arm — so a path it calls clear really is
clear. Every legitimate pick-and-place pose passes; a configuration that drives a
link into the table or an obstacle is rejected.

`load_collision_planner` patches this onto the real `OMPLPlanner`, so RRT-Connect's
`is_motion_valid` edge checks now reject colliding motions, and a start or goal
that is in collision fails to plan with a clear "goal configuration is in
collision" — which is exactly the signal `dora_move`'s Week 8/9 recovery reacts to.

### Recovery on a *real* failure

Week 9 proved task-level recovery with an injected failure; Week 11 makes the
failure real. With an obstacle placed on a grasp pose, that goal genuinely can't
be planned, `dora_move` exhausts its retries, and the agent re-approaches from a
clear pose. `test_collision_planner.py` drives exactly this end to end (with
`ReactiveMockProvider` for determinism, no API key).

## The pipeline ran live

With the coordinator port free, the previously-unrunnable integration suites all
pass for real this session — the loopback, recovery, and MuJoCo dataflows across
real dora processes (`test_integration_dataflow`, `_recovery`, `_mujoco`), plus a
new `test_integration_planner` that runs the collision-checked planner pipeline on
the real simulator.

And the headline: **a live GPT-4o completed the whole pick-and-place through the
collision-checked planner on the real MuJoCo sim, over dora**:

```
[rrt_planner] ready — rrt_connect, 6 joints, budget 5.0s, collision=on
[rrt_planner] planned 12 waypoints in 0.078s
[trajectory_executor] path 1 complete -> [2.156, -1.655, 2.144, -2.038, -1.571, -0.0]
... path 2 ... path 3 ... path 4 ... path 5 ...
[trajectory_executor] path 6 complete -> [-1.521, -1.559, 1.588, -1.574, -1.571, 0.0]
>>> Agent: The red ball has been successfully picked up and placed on the green plate.
   [metrics] {'provider': 'openai/gpt-4o', 'calls': 12, 'total_tokens': 39570, 'retries': 0}
```

This closes the "topology-validated only" caveat that had followed the live-LLM
dataflows since Week 9 — the same run is now captured end to end over dora.

## Run it

```bash
# Collision checker / FK, no dora needed:
python -c "from simulation.ur5e_collision import config_in_collision; ..."

# The collision-checked planner on the real sim, deterministic (no key):
bash scripts/fetch_ur5e_assets.sh
MUJOCO_HEADLESS=1 dora run dataflows/ur5e_mujoco_planner.yml --stop-after 70s

# The same, driven by a live GPT-4o:
OPENAI_API_KEY=sk-... MUJOCO_HEADLESS=1 \
  dora run dataflows/ur5e_mujoco_planner_llm.yml --stop-after 150s
```

## Tests

```bash
PYTHONPATH=. pytest tests/ -q -m "not integration"   # 193 unit tests, ~4s
PYTHONPATH=. pytest tests/ -q                         # + real dora runs (now unblocked)
```

New: `test_ur5e_kinematics.py` (FK vs MuJoCo), `test_ur5e_collision.py` (the
collision model), `test_collision_planner.py` (routes around obstacles, fails on a
blocked goal, real-collision recovery), `test_integration_planner.py` (the
collision-checked planner over dora + MuJoCo), plus topology tests. FK/collision
tests skip cleanly without MuJoCo / dora-moveit2.

## Validation status

- ✅ **UR5e FK matches MuJoCo to 7e-16 m** across fixed + random configs.
- ✅ **Real collision checking**: every pick-and-place pose is clear; a config in
  the table or an obstacle is rejected; the planner routes a collision-free path
  around a box that blocks the straight line.
- ✅ **Recovery on a real failure**: a blocked grasp fails to plan and the agent
  re-approaches from a clear pose (deterministic, no key).
- ✅ **The whole pipeline ran live over dora** — the integration suites pass for
  real, and a live GPT-4o completed the pick-and-place through the collision-checked
  planner on the real MuJoCo sim (run log captured).
- ⚠️ **Self-collision is out of scope** here — the coarse sphere model would
  false-positive between adjacent links, so only the table + workspace obstacles
  are checked. A tighter per-link model (capsules with proper adjacency masking)
  is the natural refinement.
- ⚠️ Collision geometry is **coarse** (link spheres, box/sphere/cylinder
  obstacles), sufficient for the table + demo obstacles but not a substitute for
  full mesh collision.

## Next (Week 12)

Feed the scene to the planner dynamically: publish the ball, plate, and any added
obstacles as a `scene_command` the planner consumes at runtime (rather than a
fixed table), so the live model can be told "avoid the box at X" mid-mission and
the plan reflects it — and add self-collision with a proper per-link adjacency
model.
