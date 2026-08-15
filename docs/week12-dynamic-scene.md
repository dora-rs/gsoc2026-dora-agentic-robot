# Week 12 — A runtime scene the agent can shape

**Goal (per Week 11's "Next"):** feed the scene to the planner *dynamically*
instead of baking in a fixed table — publish obstacles as a `scene_command` the
planner consumes at runtime, so the live model can be told "avoid the box at X"
mid-mission and the plan reflects it — plus self-collision with a proper per-link
adjacency model.

## What changed

| Path | Change |
|------|--------|
| `simulation/ur5e_collision.py` | Self-collision via an adjacency-masked check (non-adjacent link segments only, so it never false-positives on a real pose), and `obstacle_from_spec()` to build an obstacle from a scene message. |
| `simulation/rrt_planner_node.py` | The planner consumes a `scene_command` input (add / remove / clear) and keeps a live obstacle list that collision checking reads directly — a plan issued after an obstacle is added routes around it. Emits a `scene_result` ack. |
| `agent/motion_tools.py` | `dora_obstacle` — a tool the LLM calls to register or clear a workspace obstacle, pinned as a base tool. |
| `agent/bridge_node.py` | The system prompt tells the model to register an obstacle it's told about before moving near it. |
| `dataflows/ur5e_mujoco_planner*.yml` | Wire `agent_bridge/scene_command → rrt_planner` and `rrt_planner/scene_result → agent_bridge`. |

### The scene loop

`dora_obstacle(add, box, [x,y,z], …)` sends a `scene_command`; the planner node
applies it to a mutable obstacle list that `is_state_valid` reads live, and acks
on `scene_result`. Because the collision-aware planner holds that list *by
reference*, no replanning-setup is needed — the very next `dora_move` plans
against the updated world. Removing (`remove` by name) and `clear` work the same
way; re-adding a name replaces it, so an add is idempotent.

### Self-collision, without false positives

The arm is a coarse sphere sweep, so adjacent links overlap by design — checking
them would reject every pose. `self_collision` only compares link segments at
least three apart in the chain (`SELF_MIN_SEPARATION`), which flags a genuine
doubling-back of the wrist onto the shoulder/upper arm while leaving every
legitimate pose clear. `config_in_collision` now includes it by default; a test
asserts all named poses pass and a folded configuration is caught.

## Result

Told about an obstacle, a live GPT-4o registers it and then plans around it. The
model calls `dora_obstacle` *first*, and the effect shows in the waypoint counts —
the moves whose path passes the box detour (23 and 34 waypoints) while the others
take the direct 12:

```
[tool] dora_obstacle({"action":"add","name":"box_obstacle","shape":"box",
                      "position":[-0.4,-0.27,0.5],"half_extents":[0.08,0.08,0.08]})
[tool] skill(activate pick-and-place) ... dora_move(above_ball) ...
>>> Agent: The red ball has been successfully picked up and placed on the green plate.
Obstacles registered: ['box_obstacle']
Plans (waypoints each): [23, 12, 12, 12, 12, 34]   # 23 & 34 route around the box
Ball position: [0.35, 0.25, 0.001]
```

The same runs over dora on the real MuJoCo sim (`ur5e_mujoco_planner_llm.yml`).

## Run it

```bash
# Deterministic (no key): the collision-checked planner on the real sim over dora
MUJOCO_HEADLESS=1 dora run dataflows/ur5e_mujoco_planner.yml --stop-after 55s

# A live GPT-4o told to avoid a box, in-process (routes around it):
OPENAI_API_KEY=sk-... python -m agent.live_planner_demo --obstacle

# The same, live over dora + real MuJoCo:
OPENAI_API_KEY=sk-... MUJOCO_HEADLESS=1 \
  dora run dataflows/ur5e_mujoco_planner_llm.yml --stop-after 150s
```

## Tests

```bash
PYTHONPATH=. pytest tests/ -q -m "not integration"   # 199 unit tests, ~5s
```

New: self-collision + `obstacle_from_spec` cases in `test_ur5e_collision.py`, and
`test_scene.py` — `scene_command` parsing (add/remove/clear/idempotent/bad), the
`dora_obstacle` tool, and the agent registering an obstacle then planning around it
(gated on dora-moveit2). Plus topology tests for the scene wiring.

## Validation status

- ✅ **Runtime obstacles**: a `scene_command` add/remove/clear updates what the
  planner avoids live; the next plan reflects it (unit-tested and shown live).
- ✅ **The agent shapes the scene**: `dora_obstacle` lets the model register an
  obstacle it's told about; a live GPT-4o did exactly this and routed around it.
- ✅ **Self-collision** with adjacency masking: all named poses pass, a folded arm
  is caught.
- ✅ **199 unit tests pass**; the mock planner dataflow (with scene wiring) and the
  live-LLM obstacle scenario both run over dora on the real sim.
- ⚠️ Obstacle geometry is still **coarse** (box/sphere/cylinder vs link spheres) —
  good for the demo obstacles and the table, not full mesh collision.
- ⚠️ The scene is **agent- or mission-driven**, not sensed — obstacles are told to
  the agent, not perceived from the simulator. Detecting them from `sensor_data`
  (e.g. a depth/point cloud) is the natural next step toward closing the loop.

## Next (Week 13)

Close the perception loop: derive obstacles from the simulator's `sensor_data`
(the scene the sim already knows) and publish them as `scene_command`
automatically, so the planner avoids what's actually there without the agent
having to be told — and let the agent query the live obstacle set via
`dora_perceive`.
