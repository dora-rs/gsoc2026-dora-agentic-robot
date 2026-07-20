# Week 6 — Agent Bridge + SKILL.md Skills

**Goal (per the accepted proposal):** connect the agent to the robot. Replace the
Week 3 `command_source` placeholder with a **dora bridge node** so the Week 4
`Agent` (now carrying the Week 5 provider stack) *is* the pipeline driver — it
reads a natural-language mission and drives motion through dora tools. Plus a
`SKILL.md` loader so the agent's domain knowledge is authored in Markdown and
injected into the system prompt, not hard-coded in Python.

## What's added

| Path | Purpose |
|------|---------|
| `agent/skills.py` | `SKILL.md` loader — frontmatter parse, `load_skills`, `skills_to_prompt`. Pure, no YAML dep. |
| `agent/bridge.py` | `DoraAgentBridge` (input cache + JSON/float wire I/O) and the `dora_read`/`dora_send`/`dora_call` tools; `build_bridge_registry` + `compose_system_prompt`. dora-free (injected node). |
| `agent/bridge_node.py` | The dora node entrypoint — builds provider/skills/registry, runs the `Agent` on `user_command`, emits `agent_response`. |
| `agent/bridge_demo.py` | Self-running demo (`python -m agent.bridge_demo`) against a fake node — no dora, no MuJoCo, no LLM. |
| `simulation/mission_source.py` | Tiny node that emits one mission so the dataflow is self-running. |
| `skills/ur5e-arm/SKILL.md`, `skills/dora-transport/SKILL.md` | Always-on skills: named poses + motion rules, and how to use the transport tools. |
| `dataflows/ur5e_agent_demo.yml` | Full pipeline with `agent_bridge` in place of `command_source`. |
| `tests/test_skills.py`, `tests/test_bridge.py` | 9 + 14 tests; plus 4 topology tests for the new dataflow. |

## The bridge

```
 mission_source ── user_command ──►┌───────────────────────────────────┐
                                   │           agent_bridge            │
 mujoco_sim ── joint_positions ───►│                                   │
 planner   ── plan_status ────────►│   Agent.process_message(mission)  │
 gripper   ── gripper_status ─────►│     │                             │
 scene     ── scene_state ────────►│     ▼ tool calls                  │
                                   │   dora_read  → read cached input  │
                                   │   dora_send  → fire-and-forget    │───► scene_command
                                   │   dora_call  → send + wait status │───► plan_request
                                   │                                   │───► gripper_command
                                   │   final text ─────────────────────│───► agent_response
                                   └───────────────────────────────────┘
```

The agent never imports dora — it only calls tools. `DoraAgentBridge` translates
those calls into dora I/O against an injected `node`, so the whole library is
unit-testable against a fake node. The bridge caches every input event; the tools
read from that cache (`dora_read`) and send commands (`dora_send`/`dora_call`),
with `dora_call` blocking on the matching status input (e.g. `plan_request` →
`plan_status`). `dora_call` also normalizes `plan_request`: named-pose goals
(`"home"`, `"above_ball"`, …) resolve via `simulation.named_poses`, and `start`
auto-fills from the cached `joint_positions`.

### Wire format

Matches this repo's other nodes: JSON as `pa.array(json.dumps(x).encode())`, and
`joint_positions` as a raw float array whose arm slice starts at qpos index **7**
(after the red-ball free joint's 7 qpos).

## Skills — behaviour authored in Markdown

A skill is a `SKILL.md`: YAML-ish frontmatter (`name`, `version`, `always`, …)
plus a Markdown body. `load_skills(dir)` reads every `<subdir>/SKILL.md`;
`compose_system_prompt` folds the `always: true` skills into the system prompt.
Two always-on skills ship this week — `ur5e-arm` (named poses, grasp/place
sequences) and `dora-transport` (how to use the three tools). New robot knowledge
is a new Markdown file, no code change.

## Replacing command_source

The Week 3 `command_source` (a `dynamic` placeholder) is gone; `agent_bridge`
now feeds `plan_request`, `scene_command`, `gripper_command`, `ik_request`, and
`cartesian_trajectory`, and consumes the same sensor/status feedback. A
`mission_source` emits one natural-language command so the demo is self-running.
Topology is verified by `simulation.dataflow_check` and the new topology tests.

## Scope boundary (what is *not* in Week 6)

- The bridge exposes the three **transport** tools (`dora_read`/`dora_send`/
  `dora_call`). The richer **motion/introspection** tools (`dora_move`,
  `dora_list`, `dora_perceive`, …) and on-demand (non-`always`) skill activation
  arrive in **Week 7-8**.
- A live end-to-end run drives the real dora-moveit2 pipeline (planner/IK/
  executor + MuJoCo); in CI the bridge is validated in-process against a fake
  node and a scripted provider, matching the repo's simulator-free test policy.

## Run it

```bash
python -m agent.bridge_demo          # in-process; fake node, scripted provider, no deps
# Live pipeline (needs dora-moveit2 installed + meshes fetched):
dora up
MUJOCO_HEADLESS=1 OCTOS_PROVIDER=mock dora start dataflows/ur5e_agent_demo.yml
dora stop
```

For a real LLM in the loop: `OCTOS_PROVIDER=openai` + `OPENAI_API_KEY`.

## Tests

```bash
PYTHONPATH=. pytest tests/ -q                                   # 94 passed (67 prior + 27 Week 6)
PYTHONPATH=. python -m simulation.dataflow_check dataflows/ur5e_agent_demo.yml
```

## Validation status

- ✅ `SKILL.md` loader: frontmatter parsing, subdir discovery, always-only prompt
  filtering, and the two shipped skills unit-tested.
- ✅ `DoraAgentBridge`: joint-slice + JSON ingest, cache reads, JSON sends, and
  malformed-event tolerance tested against a fake node.
- ✅ Tools: `dora_read` cache read, `dora_send` fire-and-forget, `dora_call`
  named-pose resolution + start auto-inject + clean timeout tested.
- ✅ `agent_bridge` dataflow: topology validated, `command_source` confirmed
  replaced, agent drives the bridge end to end through the `Agent` loop.
- ⚠️ A live dora-moveit2 + MuJoCo run needs the external dependency + meshes and
  is not exercised in CI; the bridge is validated in-process.

## Next (Week 7-8)

The real motion/introspection tools (`dora_move`, `dora_list`, `dora_perceive`)
registered into this same `ToolRegistry`, on-demand skill activation, and a full
LLM-driven pick-and-place run through the live pipeline.
