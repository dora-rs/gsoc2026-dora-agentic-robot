# Tool API reference

The tools the agent calls to drive the pipeline. Each is a `Tool` (see
[extending.md](extending.md)) exposed to the model as an OpenAI-style function:
`name`, `description`, and a JSON-Schema `parameters` (the `input_schema` below).

Two layers:

- **Semantic tools** — phrased in the robot's own terms (`dora_move`,
  `dora_gripper`, `dora_perceive`, `dora_list`, `dora_obstacle`) plus `skill`.
  These are **base tools**: pinned in the registry so LRU eviction never drops
  them.
- **Transport tools** — the low-level escape hatch (`dora_read`, `dora_send`,
  `dora_call`) for any wire a semantic tool doesn't cover.

`build_full_registry(bridge, skills)` registers all of them.

## Return & error convention

Every tool returns a `ToolResult` whose `output` is a JSON string.

- **Success** — a tool-specific JSON object, `success=True`.
- **Failure** — `{"error": "<message>"}` with `success=False` (the `_err` helper).
  Errors are returned to the model, not raised, so it can read the message and
  recover (re-perceive, try another pose, fix an argument).

---

## dora_move

Move the arm to a target configuration and wait for the motion to complete.
Tags: `["motion", "write"]`.

```json
{
  "type": "object",
  "properties": {
    "target": {
      "description": "Named pose, or a list of 6 joint angles (radians).",
      "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "number"}}]
    },
    "timeout_secs": {"type": "number"},
    "max_attempts": {"type": "integer",
      "description": "How many times to replan a failed plan before giving up (default 3)."}
  },
  "required": ["target"]
}
```

Named poses: `home`, `upright`, `zero`, `above_ball`, `grasp_ball`, `lift`,
`above_plate`, `place_plate`.

**Example** — `{"target": "above_ball"}` or `{"target": [2.2, -1.66, 2.14, -2.05, -1.57, 0.0]}`

**Success**
```json
{"goal": [6 floats], "attempts": 1,
 "plan_status": {"success": true, "num_waypoints": 12, "message": "..."},
 "execution_status": {"status": "completed", "joint_positions": [6 floats]}}
```

**Behaviour & errors**
- Reads the start configuration from the current `joint_positions` each attempt
  (waits up to `STATE_WAIT_TIMEOUT = 1.0s` for a reading).
- A **transient** plan failure (the randomised planner drew an unlucky sample) is
  retried automatically up to `max_attempts` (default `DEFAULT_MOVE_ATTEMPTS = 3`).
  Only a **persistent** failure is returned — recover from it by approaching from
  a different pose.
- Errors: `{"error": "unknown pose '...'. Known poses: [...]"}`,
  `{"error": "expected 6 joint angles, got N"}`,
  `{"error": "no joint_positions available — is the simulator running?"}`,
  `{"error": "planning failed: <msg> (gave up after 3 attempts)"}`.
- Default `timeout_secs` = `DEFAULT_MOVE_TIMEOUT = 60.0`.

---

## dora_gripper

Open or close the Robotiq 2F-85 and wait for its status. Tags:
`["motion", "gripper", "write"]`.

```json
{
  "type": "object",
  "properties": {
    "action": {"type": "string", "enum": ["open", "close"]},
    "timeout_secs": {"type": "number"}
  },
  "required": ["action"]
}
```

**Example** — `{"action": "close"}`

**Success** — `{"action": "close", "gripper_status": {"state": "closed", "holding": true}}`

**Errors** — `{"error": "action must be one of ['open', 'close']"}`,
`{"error": "timed out after 15.0s waiting for gripper_status"}`.
Default `timeout_secs` = `DEFAULT_GRIPPER_TIMEOUT = 15.0`.

---

## dora_perceive

One fused snapshot of the world: arm state, gripper, and scene objects. Call it
before planning. Tags: `["perception", "read"]`. No arguments
(`{"type": "object", "properties": {}}`).

**Success**
```json
{
  "joint_positions": [6 floats],
  "joint_positions_age_ms": 12,
  "gripper_status": {"state": "open", "holding": false},
  "scene_state": null,
  "scene_objects": {"red_ball": {...}, "green_plate": {...}},
  "nearest_named_pose": "home"
}
```

If no joint reading is cached yet it returns the same object with
`"error": "no joint_positions cached yet"` instead of `nearest_named_pose`.

---

## dora_list

Catalogue what the agent can address. Tags: `["introspection", "read"]`.

```json
{"type": "object",
 "properties": {"kind": {"type": "string",
   "enum": ["poses", "inputs", "outputs", "objects", "all"]}}}
```

**Example** — `{"kind": "poses"}` or `{}` (defaults to a full catalogue).

**Success** — a catalogue of `poses` (named poses), `inputs` / `outputs`
(dataflow wires the agent can read/write), and `objects` (scene objects); for
`inputs`/`all` it adds `cached_inputs` (what's currently cached).

---

## dora_obstacle

Register or clear a workspace obstacle the arm must avoid; the planner routes
every subsequent motion around known obstacles. Tags: `["scene", "write"]`.

```json
{
  "type": "object",
  "properties": {
    "action": {"type": "string", "enum": ["add", "remove", "clear"]},
    "name": {"type": "string"},
    "shape": {"type": "string", "enum": ["box", "sphere", "cylinder"]},
    "position": {"type": "array", "items": {"type": "number"}},
    "half_extents": {"type": "array", "items": {"type": "number"}},
    "radius": {"type": "number"},
    "height": {"type": "number"},
    "timeout_secs": {"type": "number"}
  },
  "required": ["action"]
}
```

**Example** — add a box, then move (the plan routes around it):
`{"action": "add", "name": "pillar", "shape": "box", "position": [-0.4, -0.27, 0.5], "half_extents": [0.08, 0.08, 0.08]}`

**Success** — `{"action": "add", "scene_result": {"result": "added box 'pillar' (1 total)", "obstacles": 1}}`
(`scene_result` is `null` if the planner has no ack wire — the command still applied).

**Errors** — `{"error": "action must be one of ['add', 'remove', 'clear']"}`,
`{"error": "shape must be one of ['box', 'sphere', 'cylinder']"}`.

---

## skill

List dormant skills and pull one into the conversation on demand. Tags:
`["skills", "read"]`. Activate the matching procedure before a multi-step task
(see [skill-authoring.md](skill-authoring.md)).

```json
{"type": "object",
 "properties": {"action": {"type": "string", "enum": ["list", "activate"]},
                "name": {"type": "string"}},
 "required": ["action"]}
```

**Example** — `{"action": "activate", "name": "pick-and-place"}`

**Success** — `list` → `{"skills": [{name, description, ...}, ...]}`;
`activate` → `{"name": "pick-and-place", "version": "1.1.0", "content": "<full SKILL.md body>"}`.

**Errors** — `{"error": "action must be 'list' or 'activate'"}`,
`{"error": "name is required to activate a skill"}`, unknown-skill KeyError text.

---

## Transport tools (escape hatch)

### dora_read — read a cached dataflow input. Tags: `["transport", "read"]`.
`{"input_id": "<name>"}` → `{"data": ..., "age_ms": 12}` or
`{"error": "no data cached for '<id>'"}`.

### dora_send — fire-and-forget JSON to an output. Tags: `["transport", "write"]`.
`{"output_id": "<name>", "data": {...}}` → `{"success": true, "sent_to": "<name>"}`.

### dora_call — send a request and wait for its response. Tags: `["transport", "request"]`.
`{"output_id": "plan_request", "data": {...}, "response_id": "plan_status", "timeout_secs": 30}`
→ `{"response": {...}}` or `{"error": "timed out waiting for '<response_id>'"}`.
For `plan_request` it resolves named-pose goals and injects `start` from the
cached joint state.

**Readable inputs**: `joint_positions`, `joint_velocities`, `plan_status`,
`execution_status`, `ik_solution`, `ik_status`, `scene_state`, `command_result`,
`gripper_status`.
**Writable outputs**: `plan_request`, `ik_request`, `scene_command`,
`cartesian_trajectory`, `gripper_command`, `agent_response`.

---

## The registry

`ToolRegistry(max_active=15)` exposes at most `max_active` tools to the model at
once: **base tools first** (always included, pinned), then the most-recently-used
others until the cap. `to_openai_schema()` emits the function-calling schema; the
`Agent` calls it each turn. See [extending.md](extending.md) to add a tool.
