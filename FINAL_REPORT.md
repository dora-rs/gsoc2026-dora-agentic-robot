# Final Report — Agentic Dora: Agent-Driven Robot Control

**GSoC 2026 · dora-rs · Project #10**
Contributor: [23garyd](https://github.com/23garyd) · Mentor: dorarobotics
Repository: [dora-rs/gsoc2026-dora-agentic-robot](https://github.com/dora-rs/gsoc2026-dora-agentic-robot)

![Pick-and-place in MuJoCo](docs/assets/pick_and_place.gif)

## Summary

Agentic Dora connects an LLM agent to the [dora-rs](https://dora-rs.ai) dataflow
runtime so a **natural-language instruction** — *"pick up the red ball and place
it on the green plate"* — is carried out by a simulated robot through **real
motion planning and physics**. A live GPT-4o discovers a skill, perceives the
scene, plans collision-free paths, and drives a UR5e arm to completion — captured
running end to end over dora on the MuJoCo simulator. Everything runs in
simulation, needs no hardware, and works with a deterministic mock agent for
testing without any API key.

The guiding principle throughout was **swappability**: the LLM, the motion stack,
the robot, and the agent's tools and skills are each an isolated, testable
interface. Adding a capability is a small unit — a tool (a verb), a `SKILL.md` (a
procedure), a `RobotConfig` + scene (a body), or a provider (a brain) — never a
rewrite.

## What was built

The project was built in weekly increments, each a self-contained step with its
own validation. Grouped by capability:

### Simulation & motion (Weeks 1–3)
- A reproducible **MuJoCo UR5e + Robotiq 2F-85 scene** exposed as a dora node
  (`simulation/mujoco_node.py`) — real actuators, contact, `nq = 21`, red ball and
  green plate.
- A **Robotiq 2F-85 gripper controller** (`gripper_command` → `gripper_ctrl`,
  0 = open … 255 = closed).
- The **full motion-planning dataflow** wiring sim ↔ planner ↔ executor ↔ scene ↔
  gripper, plus a static connectivity validator (`simulation/dataflow_check.py`).

### The agent (Weeks 4–6)
- An **Agent tool-calling loop** and a **ToolRegistry** with LRU lifecycle and
  base-tool pinning — the "brain" that replaces a pipeline's hand-written
  `command_source`.
- A real **`OpenAIProvider`** (GPT-4o) and a **3-layer failover stack**
  (`RetryProvider` → `ProviderChain` → `AdaptiveRouter`), each an `LlmProvider`
  that drops into the loop.
- The **agent bridge node** that drives the pipeline from a natural-language
  mission, with behaviour authored as **`SKILL.md`** files injected into the
  system prompt.

### Robotics semantics & robustness (Weeks 7–9)
- **Semantic tools** phrased in the robot's own terms — `dora_move`,
  `dora_gripper`, `dora_perceive`, `dora_list` — over the raw transport layer, plus
  **on-demand skills** the agent activates mid-mission.
- **Replanning recovery**: `dora_move` retries a randomised planner's transient
  failures transparently, and hands only persistent failures back for task-level
  recovery (re-perceive, re-approach). The agent drives the **real MuJoCo sim**.
- **A live LLM completes the task** — GPT-4o makes every decision, verified by
  outcome (ball on plate). Getting there required advertising dormant skills in
  the prompt and self-healing sensor reads — failure modes only a real model
  exposes.

### Real planning, collision, and a live scene (Weeks 10–12)
- The genuine **dora-moveit2 RRT-Connect planner** in the loop: `dora_move` triggers
  real sampling-based path search producing multi-waypoint trajectories, followed
  through real physics by a trajectory executor.
- **Collision checking**: analytic **UR5e forward kinematics validated against
  MuJoCo to 7e-16 m** place the arm in the world so configurations can be tested
  against the table and obstacles. Paths route around a box that blocks the
  straight line; a blocked grasp fails to plan and triggers recovery. Self-collision
  with adjacency masking.
- **A runtime scene the agent can shape**: obstacles are published as a
  `scene_command` the planner consumes live, and a `dora_obstacle` tool lets the
  model register an obstacle it's told about — the planner then routes every
  motion around it. Captured live over dora: a GPT-4o told to avoid a box registers
  it and the plans past it detour.

### Documentation & polish (Week 13)
- A full [documentation set](docs/README.md): architecture (with a dataflow
  topology diagram), a sub-5-minute setup guide, a tool API reference, and skill /
  pipeline / extension authoring guides — plus this report and a rendered demo GIF.

## Architecture

A dora dataflow is a graph of nodes exchanging messages. The motion pipeline
(simulator → planner → executor → gripper) is ordinary dora; the novelty is one
node — the **agent bridge** — that replaces the fixed `command_source` a normal
pipeline would use. An LLM decides what to do and drives the pipeline through
tools that read and write the dataflow's wires. Everything downstream is unchanged:
the agent is just a smarter source.

```
mission_source → agent_bridge (LLM + tools) → rrt_planner → trajectory_executor → mujoco_sim
                       ↑  gripper_controller ─┘        sensor / status feedback ┘
```

The full topology diagram and the agent-bridge internals are in
[docs/architecture.md](docs/architecture.md).

## Design decisions

- **The agent is a drop-in `command_source`.** Rather than invent an agent
  framework bolted onto dora, the agent emits exactly the wires a scripted source
  would (`plan_request`, `gripper_command`, …). This kept the entire motion stack
  reusable and let the same agent drive a teleport stub, a direct-control executor,
  and the real planner without change.
- **Semantic tools over raw transport.** Early tools were `dora_read`/`dora_send`/
  `dora_call` — correct but low-level, and every plumbing detail was a chance for
  the model to err. `dora_move("above_ball")` hides the plan/execute handshake, the
  wire format, and the start-state lookup. The transport tools remain as an escape
  hatch.
- **Dormant skills.** `SKILL.md` procedures are advertised by name + description in
  the prompt but their bodies load only when the model calls `skill(activate, …)`.
  This keeps the prompt lean while letting a real model discover and pull in the
  right procedure — a fix driven directly by watching GPT-4o activate the wrong
  skill when only always-on skills were visible.
- **Mock-first, outcome-verified testing.** A deterministic `MockProvider` /
  `ReactiveMockProvider` makes the whole pipeline testable with no API key, and
  demos are judged by **outcome** (the ball is on the plate) rather than by a
  tool-call log — so a plausible-but-wrong sequence fails the check. Live-LLM tests
  are double-gated (`OPENAI_API_KEY` + `RUN_LIVE_LLM=1`) so an ordinary run never
  makes a paid call.
- **A repo-local planner node importing the dora-moveit2 *algorithm*.** The planner
  node lives in this repo and imports only the RRT-Connect implementation from the
  sibling dora-moveit2 checkout (via `DORA_MOVEIT2_PATH`), rather than pointing the
  dataflow at a second package's example nodes. The dataflow stays self-contained
  and unit-testable; the discovery that the planner is pure-NumPy (no OMPL/TracIK/
  toppra C++) is what made it runnable at all.
- **Forward kinematics pinned to the simulator.** Collision checking is only as
  trustworthy as its idea of where the arm is, so the analytic FK is built from the
  model's own body transforms and joint axes and asserted against MuJoCo's
  `mj_kinematics` (worst case 7e-16 m) — correct by construction, and MuJoCo-free at
  plan time.
- **A conservative, self-contained collision model.** The arm is a coarse sphere
  sweep that *over*-approximates its volume, so a path the checker calls clear
  really is clear; self-collision uses adjacency masking to avoid false positives
  on legitimate poses. It has no dora-moveit2 dependency and is unit-testable
  anywhere.
- **A runtime scene held by reference.** Obstacles live in a list the collision
  planner reads live, so a `scene_command` add/remove immediately changes what the
  next plan avoids — no replanning setup, and the agent and perception can both
  feed it.

## Results & validation

- **~200 unit tests** pass with no simulator, dora, or API key.
- **Integration tests** run real dora dataflows on the real MuJoCo simulator
  (loopback, injected-failure recovery, direct-control, and the collision-checked
  planner) — all green this session.
- **A live GPT-4o was captured completing the pick-and-place end to end over dora**,
  through the collision-checked planner on the real simulator, including an
  obstacle-avoidance run where it registered a box and the plans past it detoured.
- Every shipped dataflow is topology-validated in CI.
- The [demo GIF](docs/assets/pick_and_place.gif) is a real MuJoCo render of the
  pick-and-place under physics (grasp verified, ball on plate).

A deliverables-to-code map with exact run commands is in
[docs/final-submission.md](docs/final-submission.md).

## Future work

- **Close the perception loop.** Derive obstacles from what the simulator actually
  contains (its `sensor_data` / scene geometry) and auto-publish them as
  `scene_command`, so the planner avoids what's physically there without the agent
  being told. A prototype exists and is the most natural next step.
- **Tighter collision geometry.** Move from link spheres + primitive obstacles
  toward capsule/mesh collision, and enable dora-moveit2's own collision checker
  now that UR5e FK exists.
- **Full trajectory parameterisation and IK.** Wire toppra time-parameterisation
  and TracIK so motions are time-optimal and Cartesian goals are reachable — the
  parts of dora-moveit2 not yet in the loop.
- **A second robot.** Bring in the `hunter_with_arm` mobile base so the agent drives
  *and* manipulates, exercising a second `RobotConfig` end to end and proving the
  robot-agnostic claim.
- **Richer tasks and interaction.** Multi-object missions, an interactive mission
  source to type instructions live, and skills for more procedures.

## Links

- **Documentation:** [docs/README.md](docs/README.md) — start at
  [docs/final-submission.md](docs/final-submission.md).
- **Project board:** https://github.com/orgs/dora-rs/projects/8
- **Key pull requests** (all merged):
  [Agent core #27](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/27) ·
  [LLM providers #29](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/29) ·
  [Agent bridge + skills #31](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/31) ·
  [Motion tools #33](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/33) ·
  [Live LLM #37](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/37) ·
  [Real planner #39](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/39) ·
  [Collision checking #41](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/41) ·
  [Runtime scene #43](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/43) ·
  [Documentation #45](https://github.com/dora-rs/gsoc2026-dora-agentic-robot/pull/45)
- **Weekly build log:** the `weekN-*.md` notes in [docs/](docs/).

## Acknowledgements

Thanks to the dora-rs maintainers and the mentor (dorarobotics) for guidance
throughout, and to the [dora-moveit2](https://github.com/dora-rs/dora-moveit2)
project whose RRT-Connect planner this work builds on.
