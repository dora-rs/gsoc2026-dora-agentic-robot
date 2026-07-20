# Week 7 — Motion tools, on-demand skills, and a real dataflow test

**Goal (per the accepted proposal):** turn the Week 6 transport bridge into
something an LLM can actually drive a task with — robot-level tools instead of
pipeline plumbing, procedural knowledge loaded on demand, and a complete
pick-and-place. Plus the thing the midterm feedback asked for: **an integration
test that runs a real dora dataflow**, so the simulation path is continuously
validated rather than only mocked.

## What's added

| Path | Purpose |
|------|---------|
| `agent/motion_tools.py` | `dora_move`, `dora_gripper`, `dora_perceive`, `dora_list`, `skill` + `build_full_registry`. Composed from the Week 6 bridge; still dora-free. |
| `agent/skills.py` | `SkillRegistry` — dormant skills, activated mid-mission. |
| `skills/pick-and-place/SKILL.md` | The grasp/place procedure, `always: false` (loaded on demand). |
| `simulation/pipeline_stub.py` | Planner + executor + sim in one node, speaking the same wire contract — no dora-moveit2, no MuJoCo. |
| `dataflows/ur5e_agent_loopback.yml` | Real dora dataflow, zero external dependencies. |
| `agent/pick_place_demo.py` | In-process demo with a **stateful** fake pipeline; asserts the ball reaches the plate. |
| `tests/test_integration_dataflow.py` | Runs `dora run` for real and asserts the mission completed. |
| `tests/test_motion_tools.py`, `tests/test_pipeline_stub.py` | 26 + 4 tests; plus 6 skill-registry and 5 topology tests. |

## From plumbing to robotics

Week 6 gave the agent three transport tools. Correct, but to move the arm the
model had to know the output id, the request schema, the matching status input,
and that a plan must be followed by execution — pipeline plumbing, and four
chances to get it wrong. Week 7 phrases the tools in the robot's terms:

```
 dora_perceive   arm state + gripper + scene objects, in one snapshot
 dora_move       named pose or joint vector -> plan, execute, wait, report
 dora_gripper    open/close, waits for gripper_status
 dora_list       what can be addressed: poses, inputs, outputs, objects
 skill           list dormant skills; pull one into the conversation
```

Each is composed from the same `DoraAgentBridge`, so nothing new touches dora.
The transport tools stay registered as the escape hatch for raw IK requests,
scene commands, and Cartesian trajectories. The five semantic tools are pinned as
**base tools**, so LRU eviction can never take them.

`dora_move` is where most of the sequencing lives: it resolves the target
(named pose or 6 floats), fills `start` from the cached joint state, sends
`plan_request`, waits for `plan_status`, and then waits for `execution_status` —
so one call is one completed motion, and the model cannot overlap two.

## On-demand skills

Week 6 injected every `always: true` skill into the system prompt. Week 7 adds
`SkillRegistry`: `always: false` skills stay **dormant**, visible to the agent as
a one-line catalogue entry until it activates one. Same idea as the
`ToolRegistry`'s LRU lifecycle, applied to knowledge instead of tools — the
50-line pick-and-place procedure only enters the context of a mission that needs
it. In the demo the agent does exactly that: `skill(list)` →
`skill(activate, "pick-and-place")` → walks the procedure.

## The integration test — and what it caught

Everything before this week was validated in-process against a fake node. That
catches logic bugs; it structurally cannot catch bugs that only exist once dora
spawns each node as its own process. `dataflows/ur5e_agent_loopback.yml` closes
that gap: the **agent side is completely real** — same `bridge_node.py`, same
skills, same tools, same wire formats — and only the motion stack is swapped for
`pipeline_stub`, which speaks the planner/executor/sim contract in ~100 lines
with no dependency beyond dora and pyarrow.

```
 mission_source ──user_command──► agent_bridge ──plan_request──► pipeline_stub
       ▲                            │    ▲                            │
       └────agent_response──────────┘    │                    joint_positions
                                         │                    plan_status
                                         │                    execution_status
                                         └──gripper_command──► gripper_controller
                                                  gripper_status  (real Week 2 node)
```

Four real node processes over real dora IPC. Writing it immediately paid for
itself — it found **two bugs invisible to every in-process test**:

1. **`agent_bridge` could never start under dora.** The node used relative
   imports (`from .agent import …`), but dora spawns a node by *path*, so it runs
   as a top-level script with no parent package. It failed at import.
   `simulation/pipeline_stub.py` had the mirror-image problem. Both now put the
   repo root on `sys.path` and import absolutely, so the same file works under
   `dora start` and `python -m`.
2. **A startup race silently swallowed the mission.** The bridge primes its
   sensor cache with a one-second `drain()` on startup, and `drain` caches *every*
   input it sees — including `user_command`. A mission published while the bridge
   was still booting went into the cache and was never run: the dataflow came up
   healthy and simply did nothing. The node now claims any pending
   `user_command` from the cache before entering its loop.

Neither bug was reachable from a fake node. This is the argument for the test.

## Scope boundary (what is *not* in Week 7)

- `pipeline_stub` is a **stand-in for the motion stack, not a simulator**:
  motions complete instantly, nothing is collision-checked. It validates the
  wiring across process boundaries, not the motion planning. The real planner
  and MuJoCo are still exercised only by `ur5e_agent_demo.yml`, which needs
  dora-moveit2 installed and meshes fetched.
- The mock provider **scripts** the tool sequence a real LLM should produce, so
  the pipeline under test is deterministic. A live GPT-4o run
  (`OCTOS_PROVIDER=openai`) uses the identical tools and skills, but is not part
  of CI — it needs an API key and is non-deterministic.

## Run it

```bash
python -m agent.pick_place_demo                          # in-process, stateful, verifies the ball lands
dora run dataflows/ur5e_agent_loopback.yml --stop-after 25s   # real dora, no external deps

# Live pipeline (needs dora-moveit2 installed + meshes fetched):
dora up
MUJOCO_HEADLESS=1 OCTOS_PROVIDER=mock dora start dataflows/ur5e_agent_demo.yml
dora stop
```

For a real LLM in the loop: `OCTOS_PROVIDER=openai` + `OPENAI_API_KEY`.

## Tests

```bash
PYTHONPATH=. pytest tests/ -q                     # 144 passed (94 prior + 50 Week 7)
PYTHONPATH=. pytest tests/ -q -m "not integration"  # skip the ~27s dora run
PYTHONPATH=. python -m simulation.dataflow_check dataflows/ur5e_agent_loopback.yml
```

## Validation status

- ✅ **A real dora dataflow runs end to end in CI** — 4 node processes, real IPC,
  8 assertions covering node startup, skill loading, mission delivery, on-demand
  activation, all 6 motions, the open/close/open gripper sequence, the final
  pose, and the reply completing the loop. Skipped when the `dora` CLI is absent.
- ✅ `dora_move`: named-pose and joint-vector targets, start auto-fill, execution
  wait, and every failure mode (unknown pose, wrong joint count, no cached state,
  planning failure, timeout) unit-tested.
- ✅ `dora_gripper` / `dora_perceive` / `dora_list` / `skill`: happy paths and
  recoverable-error paths tested.
- ✅ Pick-and-place verified by **outcome, not by call log** — the demo node
  tracks the ball, and a test confirms a wrong sequence (closing the gripper away
  from the ball) leaves the ball where it started.
- ✅ `SkillRegistry`: dormant/active split, idempotent activation, unknown-skill
  error, and the shipped skills' activation states.
- ⚠️ The **live** dora-moveit2 + MuJoCo run still needs the external dependency
  and meshes, so it remains outside CI. The loopback dataflow now covers the
  dora-integration half of that gap.

## Next (Week 8)

Close the remaining gap: a live `ur5e_agent_demo.yml` run driven by a real LLM
through the actual planner and MuJoCo, with the recovery behaviour that a real
planner's failures demand — replanning on a failed `plan_status` rather than
reporting the error and stopping.
