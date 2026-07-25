# Week 8 — Replanning recovery, and the agent on the real MuJoCo simulator

**Goal (per Week 7's "Next"):** close the two things the loopback left open.
First, the recovery behaviour a real planner demands — replanning on a failed
`plan_status` instead of reporting the error and stopping. Second, get the
full pick-and-place actually running on the **real MuJoCo simulator**, not a
teleporting stub — the mentor's top second-half priority.

Both landed, and both are verified by a real `dora run` in CI.

## What's added

| Path | Purpose |
|------|---------|
| `agent/motion_tools.py` | `dora_move` now **replans** a failed plan transparently (bounded retry), and reports the attempt count. |
| `simulation/pipeline_stub.py` | `FAIL_FIRST_N` — inject planner failures, so recovery can be tested across real process boundaries. |
| `simulation/sim_executor.py` | New node: turns `plan_request` into a streamed joint target for the **real** sim and reports completion once the arm physically settles. |
| `simulation/mujoco_node.py` | `SIM_SUBSTEPS` (default 1, unchanged) so a headless run advances physics faster than the tick rate and settles in bounded time. |
| `dataflows/ur5e_agent_recovery.yml` | The loopback + injected failures — the recovery regression test. |
| `dataflows/ur5e_mujoco_agent.yml` | The agent driving the real MuJoCo node headless, no dora-moveit2. |
| `tests/test_integration_recovery.py`, `tests/test_integration_mujoco.py` | Two more **real** `dora run` tests: recovery, and real-physics execution. |

## Recovery: replan, don't give up

Week 7's `dora_move` reported a failed `plan_status` as an error and stopped.
That is wrong for a real planner. RRT-Connect (the OMPL planner in
dora-moveit2) is **randomised**: a failed plan is usually an unlucky set of
samples, not an unreachable goal, and replanning from the same start often
succeeds on the next try. So `dora_move` now retries:

```
for attempt in 1..max_attempts:      # default 3
    re-read the current joint state   # the arm may have settled/moved
    send plan_request
    wait for plan_status
    if it failed -> remember why, try again
    else -> wait for execution_status, return {..., "attempts": attempt}
return the last error, "gave up after N attempts"
```

Two layers of recovery, deliberately split:

- **Transient failures** (an unlucky sample) are handled *inside the tool* — the
  agent never sees them, it just gets a slightly slower `dora_move`. Bounded, so
  a truly unreachable goal can't loop forever.
- **Persistent failures** (the retry budget is exhausted) are returned to the
  agent, and the `pick-and-place` skill now tells it what to do: re-`perceive`
  and approach from a *different* pose, rather than repeat the identical call.

### Proving it across real processes

A retry loop is trivial to pass in a unit test — the hard question is whether it
works when the planner is a *separate dora node*. `FAIL_FIRST_N=2` makes
`pipeline_stub` reject the first two `plan_request`s without moving the arm.
`ur5e_agent_recovery.yml` wires that up, and the integration test asserts:

- exactly two injected rejections actually happened (so there was something to
  recover from), and
- all six motions still completed and the mission still finished.

A build with the retry loop deleted stalls at the first motion. That is the
regression this test guards.

## The agent on the real simulator

The bigger gap Week 7 named: the loopback's `pipeline_stub` *teleports* the arm,
so nothing about real physics was exercised. `ur5e_agent_demo.yml` runs the real
sim, but only through the full dora-moveit2 OMPL/TracIK/toppra stack, which needs
those dependencies installed.

`ur5e_mujoco_agent.yml` is the missing middle: the **same real agent, same real
Week 1 MuJoCo node, same real Week 2 gripper**, with a small `sim_executor` in
place of the OMPL stack.

```
 mission_source ──user_command──► agent_bridge ──plan_request──► sim_executor
       ▲                            │    ▲                            │
       └────agent_response──────────┘    │                      control_input
                                         │                            ▼
                          joint_positions │◄─────────────────────  mujoco_sim  (real physics)
                          plan_status     │       joint_positions      ▲
                          execution_status│                            │
                                         └──gripper_command──► gripper_controller ──gripper_ctrl──┘
```

`sim_executor` accepts a `plan_request`, streams the goal to the UR5e's position
actuators as `control_input`, watches the returned `joint_positions`, and only
reports `execution_status` once every joint is within tolerance of the goal — so
`dora_move`'s "one call is one finished motion" contract holds against real
settling dynamics, not an instant teleport.

**What this is:** real actuators, real settling, the real 21-dof qpos layout
(free-joint ball + 6 arm + 8 gripper), real gripper contact. The agent completes
the whole pick-and-place on real physics, headless, in about five seconds of
wall-clock, and the integration test asserts all six motions physically settled
(none timed out) and the gripper actuated open→close→open through the sim.

**What this is not:** motion *planning*. `sim_executor` does direct joint
control — no collision checking, no path search. That is exactly what
dora-moveit2 provides, and wiring the real OMPL planner in its place (the goal
of `ur5e_agent_demo.yml`) is the remaining step. The named poses were chosen so
the straight-line joint interpolation between them stays clear of the table.

## Scope boundary

- The **full dora-moveit2 stack** (OMPL planning, TracIK, toppra time-scaling)
  still needs those packages installed and is not in CI. `ur5e_mujoco_agent.yml`
  covers the real-simulator half of that gap; `ur5e_agent_demo.yml` remains the
  target for the real-planner half.
- A **live LLM** (`OCTOS_PROVIDER=openai`) drives the identical tools and skills,
  but is non-deterministic and needs an API key, so CI uses the scripted mock —
  the *pipeline* under test is real, only the token stream is fixed.
- The real-physics run asserts motion execution and settling, **not** a verified
  physical grasp force — reliable grasping under contact is a grasp-planning
  problem the real planner is there to solve.

## Run it

```bash
# Recovery — real dora, injected planner failures, no external deps:
dora run dataflows/ur5e_agent_recovery.yml --stop-after 25s

# The agent on the REAL MuJoCo simulator (one-time mesh fetch first):
bash scripts/fetch_ur5e_assets.sh
dora run dataflows/ur5e_mujoco_agent.yml --stop-after 30s

# Live pipeline with the real planner (needs dora-moveit2 installed):
dora up
MUJOCO_HEADLESS=1 OCTOS_PROVIDER=mock dora start dataflows/ur5e_agent_demo.yml
dora stop
```

## Tests

```bash
PYTHONPATH=. pytest tests/ -q                       # 172 passed (155 unit + 17 integration)
PYTHONPATH=. pytest tests/ -q -m "not integration"  # 155, ~1.5s, skip the dora runs
```

The three integration suites — `test_integration_dataflow` (Week 7 happy path),
`test_integration_recovery`, and `test_integration_mujoco` — are each a real
`dora run`. The MuJoCo one skips automatically unless `mujoco` and the fetched
meshes are both present.

## Validation status

- ✅ **Recovery runs end to end in a real dataflow** — two injected planner
  failures, all six motions still complete, mission still finishes. Unit tests
  cover replan-then-succeed, give-up-after-N, and the exact retry budget.
- ✅ **The agent drives the real MuJoCo simulator** — real scene loaded (nq=21),
  all six motions physically settled within tolerance, none timed out, gripper
  open→close→open through the sim, mission complete. ~5s wall-clock.
- ✅ `SIM_SUBSTEPS` defaults to 1, so the Week 1 node's behaviour is unchanged
  for every existing dataflow and the viewer path.
- ⚠️ The full dora-moveit2 planner stack is still outside CI; the real-simulator
  run now covers everything below the agent except the OMPL path search itself.

## Next (Week 9)

Swap `sim_executor` for the real dora-moveit2 planner in `ur5e_agent_demo.yml`
and get a **live GPT-4o** run completing the pick-and-place through real OMPL
planning + real MuJoCo — the last mock removed from the loop — with the
task-level recovery (re-perceive, re-approach) exercised against real
plan failures.
