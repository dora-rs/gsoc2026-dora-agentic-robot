# Pipeline authoring guide

A pipeline is a **dora dataflow**: a graph of nodes exchanging messages, described
in YAML. This is the format the whole project uses — the motion pipeline, the
agent bridge, the simulator, and the demos are all nodes in a dataflow.

> **A note on the graph format.** The original proposal sketched pipelines as a
> DOT-style graph with gate nodes and a direct-execution syntax. In the delivered
> implementation that role is filled by **dora's native dataflow YAML** — the same
> node-and-edge graph, run by the dora runtime rather than a bespoke executor, so
> the pipeline is a first-class, tool-supported artifact (`dora run`, `dora
> graph`, the coordinator/daemon) instead of a custom format. "Gate" behaviour —
> a node that only forwards when a condition holds — is expressed as an ordinary
> node (e.g. `sim_executor` reports `execution_status` only once the arm settles;
> `rrt_planner` emits a `trajectory` only on a successful plan). This guide
> documents the format as it exists.

## The YAML

A dataflow is a top-level `nodes:` list. Each node:

```yaml
nodes:
  - id: rrt_planner                       # unique node name
    path: ../simulation/rrt_planner_node.py   # the script dora spawns
    inputs:
      plan_request: agent_bridge/plan_request   # <this input>: <src_node>/<output>
      scene_command: agent_bridge/scene_command
    outputs:                              # names this node may send_output()
      - plan_status
      - trajectory
      - scene_result
    env:                                  # environment for the spawned process
      ROBOT_CONFIG_MODULE: simulation.ur5e_moveit_config
      PLANNER_TYPE: rrt_connect
      COLLISION: "1"
```

- **`id`** — the node's name; other nodes wire to `id/output`.
- **`path`** — a Python file dora runs as a top-level script. Because it's spawned
  by path, the repo isn't importable by default — nodes do
  `sys.path.insert(0, <repo root>)` at the top (see any node in `simulation/`).
- **`inputs`** — a map of *this node's* input name → the source `node/output` it
  reads. The value may be a bare `"node/output"` string, or a mapping with a
  `source:` key.
- **`outputs`** — the list of output names this node is allowed to `send_output()`.
  Sending an undeclared output is an error, so declare every one you emit.
- **`env`** — process environment (strings). How nodes are configured — the
  provider, the robot config, feature flags.

### Timer inputs

A node that should tick on a clock (e.g. to step the sim or emit a mission) reads
a builtin timer input:

```yaml
inputs:
  tick: dora/timer/millis/10      # fire every 10 ms
```

`dora/timer/millis/N` and other `dora/...` builtins are provided by the runtime —
they need no source node.

## Wire encodings

Two conventions cross the graph (see [architecture.md](architecture.md)):

- **JSON messages** — `send_output("plan_request", pa.array(json.dumps(obj).encode("utf-8")))`,
  read back with `json.loads(bytes(value.to_pylist()).decode("utf-8"))`.
- **Float arrays** — `send_output("joint_positions", pa.array(qpos))`,
  read back with `value.to_numpy()`.

The `DoraAgentBridge` wraps both for the agent's tools; a plain motion node uses
`pyarrow` directly.

## Validate before you run

`dataflow_check` statically verifies every wire resolves — the fastest way to
catch a typo'd `node/output`:

```bash
PYTHONPATH=. python -m simulation.dataflow_check dataflows/ur5e_mujoco_planner.yml
# OK   dataflows/ur5e_mujoco_planner.yml: 6 nodes, all input edges resolve
```

It checks, for every input source that isn't a `dora/` builtin: that it's of the
form `node/output`, that `node` is a declared node, and that `output` is declared
by that node. It validates **wiring only** — not env values, timers, or whether
the node files exist. Exit code 0 = ok, 1 = dangling edges. It's used in the test
suite (`tests/test_dataflow_topology.py`) to keep every shipped dataflow valid.

## Running a dataflow

```bash
dora run <dataflow>.yml --stop-after 70s     # embedded coordinator, runs then stops
# or, long-running:
dora up
dora start <dataflow>.yml
dora stop
```

Set env at the node level in the YAML (`env:`), or export it before `dora run`
for values the node reads from the process environment (e.g. `OPENAI_API_KEY`,
`MUJOCO_HEADLESS`).

## The shipped dataflows

| File | What it is |
|------|-----------|
| `ur5e_sim.yml` | The simulator alone. |
| `ur5e_gripper_demo.yml` | The gripper controller driving the sim. |
| `ur5e_full_pipeline.yml` | The full motion pipeline with a scripted `command_source`. |
| `ur5e_agent_demo.yml` | The agent bridge driving the dora-moveit2 pipeline. |
| `ur5e_agent_loopback.yml` | Agent + an in-process stub (no external deps). |
| `ur5e_agent_recovery.yml` | The loopback with injected planner failures. |
| `ur5e_mujoco_agent.yml` | Agent → direct-control executor → real MuJoCo. |
| `ur5e_mujoco_planner.yml` | Agent → RRT-Connect + collision → real MuJoCo (mock). |
| `ur5e_mujoco_planner_llm.yml` | The same, driven by a live GPT-4o. |
| `ur5e_mujoco_agent_llm.yml` | Direct-control executor, live GPT-4o. |

## Composing your own

1. Copy the closest dataflow.
2. Swap or add nodes: give each an `id`, a `path`, its `inputs` (wired to existing
   `node/output`s), and its declared `outputs`.
3. Keep the agent side intact if you're only changing the motion stack — the
   `agent_bridge` block is identical across the pipeline variants; that's the
   design (the agent doesn't care what's below it).
4. `dataflow_check` it, then `dora run` it.

See [extending.md](extending.md) to write the node a new pipeline step needs.
