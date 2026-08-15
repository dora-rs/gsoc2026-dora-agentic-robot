# Extension guide — new tools, providers, and robots

Every axis of the system is a small interface you implement and register. This
guide covers the three most common extensions.

---

## Add a tool

A tool is what the agent can *do*. Implement the `Tool` ABC (`agent/tool.py`):

```python
from agent.tool import Tool, ToolResult

class DoraWaveTool(Tool):
    def __init__(self, bridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_wave"

    def description(self) -> str:
        return "Wave the arm: move to 'upright' and back to 'home'."

    def input_schema(self) -> dict:            # JSON Schema for the arguments
        return {"type": "object",
                "properties": {"times": {"type": "integer"}},
                "required": []}

    def tags(self) -> list[str]:               # optional; for find_by_tag
        return ["motion", "write"]

    def execute(self, args: dict) -> ToolResult:
        times = int(args.get("times", 1))
        for _ in range(times):
            self._bridge.send_json("plan_request", {"goal": "upright"})
            self._bridge.wait_for_input("plan_status")
            self._bridge.send_json("plan_request", {"goal": "home"})
            self._bridge.wait_for_input("plan_status")
        return ToolResult(output=json.dumps({"waved": times}))
```

**Contract**
- `execute` returns a `ToolResult(output=<json string>, success=True|False)`. On
  failure return `success=False` with `{"error": "..."}` (use the `_err` helper
  pattern) — the message goes back to the model to recover from, never raised.
- Talk to the dataflow only through the injected `bridge`
  (`send_json` / `wait_for_input` / `read_cached` / `ensure_cached`) so the tool
  stays unit-testable with a fake node — no dora, no MuJoCo, no LLM.

**Register it** in `build_full_registry` (`agent/motion_tools.py`):

```python
registry.register(DoraWaveTool(bridge))
# pin it if the model needs it on every mission:
base = ["dora_move", "dora_gripper", "dora_perceive", "dora_list",
        "dora_obstacle", "dora_wave"]
registry.set_base_tools(base)
```

Base tools are never LRU-evicted; non-base tools live under the registry's
`max_active` cap and rotate by recency. `to_openai_schema()` exports the tool to
the model automatically — no other wiring.

---

## Add an LLM provider

A provider is the model behind the agent loop. Implement `LlmProvider`
(`agent/provider.py`) — two abstract methods:

```python
from agent.provider import LlmProvider, ChatResponse, ChatConfig

class MyProvider(LlmProvider):
    def name(self) -> str:
        return "my-provider"

    def chat(self, messages: list[dict], tools: list[dict],
             config: ChatConfig) -> ChatResponse:
        # call your model with `messages` (OpenAI-style) and `tools`
        # (the registry's function schemas), then return:
        return ChatResponse(
            content="...",                 # assistant text, or None
            tool_calls=[...],              # OpenAI-style tool calls, or []
            finish_reason="tool_calls")    # "tool_calls" to continue, "stop" to end
```

Optional overrides: `chat_stream`, `context_window` (default `128_000`),
`export_metrics` (default `{}`), `report_late_failure`.

Because it's just an `LlmProvider`, it drops into the `Agent` — and into the
failover stack: wrap it in `RetryProvider` (retries with backoff), compose several
with `ProviderChain` (ordered failover) or `AdaptiveRouter` (routes to the
lowest error-rate/latency provider). See [week5-llm-providers.md](week5-llm-providers.md).

To make it the bridge's provider, extend `build_provider` in `agent/bridge_node.py`
(it selects on `OCTOS_PROVIDER`).

For tests, prefer the deterministic `MockProvider(script)` (replays a fixed
sequence) or `ReactiveMockProvider(decide)` (reacts to tool results) — no API key.

---

## Add a robot

Two pieces: a **scene** the simulator loads, and a **RobotConfig** the planner
uses. The library operators are robot-agnostic; you inject the robot.

### 1. The scene + poses

- A MuJoCo scene `.xml` under `simulation/models/` (arm + gripper + objects).
  Point a dataflow's `mujoco_sim` node at it with `MODEL_NAME`.
- Named joint configurations in a `named_poses.py`-style module: `NUM_JOINTS`, a
  `NAMED_POSES` dict (`name -> [joint angles]`), and `SCENE_OBJECTS`. Verify the
  poses with MuJoCo FK, as `simulation/named_poses.py` documents.

### 2. The RobotConfig

The dora-moveit2 planner loads a config class named by `ROBOT_CONFIG_MODULE`.
Implement the `RobotConfig` protocol — see `simulation/ur5e_moveit_config.py` for
a complete UR5e example. Required members:

| Member | Meaning |
|--------|---------|
| `NUM_JOINTS` | DOF count. |
| `JOINT_LOWER_LIMITS`, `JOINT_UPPER_LIMITS` | `np.ndarray` joint limits (rad). |
| `JOINT_VELOCITY_LIMITS` | `np.ndarray` (rad/s). |
| `LINK_TRANSFORMS` | list of link transforms (may be empty if unused). |
| `COLLISION_GEOMETRY` | list of `(shape, dims)` per link. |
| `COLLISION_MARGIN` | float safety margin. |
| `HOME_CONFIG`, `SAFE_CONFIG` | `np.ndarray` default poses. |
| `NAMED_POSES` | `{name: np.ndarray}`. |
| `get_joint_limits()` | `-> (lower, upper)`. |
| `is_config_valid(q)` | `-> bool`. |
| `clip_to_limits(q)` | `-> np.ndarray`. |

Then point the planner at it in the dataflow:

```yaml
  - id: rrt_planner
    path: ../simulation/rrt_planner_node.py
    env:
      ROBOT_CONFIG_MODULE: myrobot.config      # your module
```

### 3. Teach the agent

Write a `SKILL.md` describing the new robot's conventions and any task recipes
([skill-authoring.md](skill-authoring.md)). The tools (`dora_move` etc.) are
robot-agnostic — they resolve poses from whatever `NAMED_POSES` is loaded — so a
new robot usually needs **no tool changes**, just a config, a scene, poses, and a
skill.

---

## What you almost never have to touch

The `Agent` loop, the `ToolRegistry`, the `DoraAgentBridge`, and the wire
encodings are stable substrate. Adding a capability is: a `Tool` (a verb), a
`SKILL.md` (a procedure), a `RobotConfig` + scene (a body), or an `LlmProvider` (a
brain) — each an isolated, testable unit.
