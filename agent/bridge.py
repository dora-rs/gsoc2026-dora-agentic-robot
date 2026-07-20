"""Dora bridge (Week 6) — connect the `Agent` loop to a dora dataflow.

This is the glue that lets the Week 4 `Agent` (now with a real provider stack
from Week 5) *be* the pipeline driver — it replaces the Week 3 `command_source`
placeholder. The agent never imports dora: it only calls tools. The tools here
translate those calls into dora I/O through a `DoraAgentBridge`:

    Agent → dora_read  → bridge.read_cached(input_id)      # latest sensor value
    Agent → dora_send  → bridge.send_json(output_id, data) # fire-and-forget
    Agent → dora_call  → bridge.send_json + wait_for_input # request/response

The bridge is constructed with an injected `node` exposing `.next(timeout=…)` and
`.send_output(id, arr[, meta])` — the dora `Node` API — so the whole module is
unit-testable against a fake node with **no dora daemon, no MuJoCo, no LLM**.
Only `pyarrow` (already a dependency) is imported here; the `from dora import
Node` lives in `agent/bridge_node.py`.

Wire format matches this repo's other nodes (see `simulation/gripper_controller`):
JSON as `pa.array(json.dumps(x).encode())`, `joint_positions` as a raw float
array whose arm slice starts after the scene's free-joint qpos.
"""

from __future__ import annotations

import json
import time

import pyarrow as pa

from simulation.named_poses import NAMED_POSES, NUM_JOINTS
from .skills import SkillInfo, skills_to_prompt
from .tool import Tool, ToolRegistry, ToolResult

# The UR5e arm's 6 joints sit after the red-ball free joint (7 qpos) in the
# MuJoCo scene, so joint_positions[7:13] is the arm configuration.
DEFAULT_ARM_QPOS_OFFSET = 7


class DoraAgentBridge:
    """Caches dataflow inputs and sends outputs on behalf of the agent's tools."""

    def __init__(
        self,
        node,
        arm_qpos_offset: int = DEFAULT_ARM_QPOS_OFFSET,
        num_joints: int = NUM_JOINTS,
        poll_secs: float = 0.3,
    ):
        self._node = node
        self._arm_offset = arm_qpos_offset
        self._num_joints = num_joints
        self.poll_secs = poll_secs
        self.cache: dict[str, tuple[object, float]] = {}

    # --- event ingestion --------------------------------------------------

    def ingest(self, event: dict) -> None:
        """Cache one dora INPUT event (public entry point; used by the node loop)."""
        input_id = event.get("id", "")
        value = event.get("value")
        try:
            if input_id in ("joint_positions", "joint_velocities"):
                arr = value.to_numpy().astype(float)
                arm = self._slice_arm(arr)
                self.cache[input_id] = (arm, time.time())
            else:
                raw = bytes(value.to_pylist())
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = raw.hex()
                self.cache[input_id] = (data, time.time())
        except Exception as exc:  # never let a malformed event kill the loop
            self.cache[input_id] = ({"error": f"decode failed: {exc}"}, time.time())

    def _slice_arm(self, qpos) -> list[float]:
        """Extract the arm's `num_joints` values from a full qpos array."""
        end = self._arm_offset + self._num_joints
        if len(qpos) >= end:
            return qpos[self._arm_offset:end].tolist()
        return qpos[: self._num_joints].tolist()

    def drain(self, duration: float | None = None) -> None:
        """Pull and cache pending events for up to `duration` seconds."""
        duration = self.poll_secs if duration is None else duration
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            event = self._node.next(timeout=0.1)
            if event is None:
                continue
            if event.get("type") == "INPUT":
                self.ingest(event)

    def wait_for_input(self, input_id: str, timeout: float = 30.0):
        """Block until `input_id` arrives (caching everything else), or time out."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self._node.next(timeout=1.0)
            if event is None:
                continue
            if event.get("type") == "INPUT":
                self.ingest(event)
                if event.get("id") == input_id:
                    return self.cache.get(input_id, (None, 0.0))[0]
        return None

    # --- reads / sends ----------------------------------------------------

    def read_cached(self, input_id: str) -> dict:
        """Latest cached value for `input_id` with its age, or an error dict."""
        cached = self.cache.get(input_id)
        if cached is None:
            return {"error": f"no data cached for {input_id!r}"}
        data, ts = cached
        return {"data": data, "age_ms": int((time.time() - ts) * 1000)}

    def send_json(self, output_id: str, data: object) -> None:
        """Send a JSON payload on a dataflow output (repo wire convention)."""
        self._node.send_output(output_id, pa.array(json.dumps(data).encode("utf-8")))


# ---------------------------------------------------------------------------
# Dora tools — thin Tool wrappers over the bridge
# ---------------------------------------------------------------------------

class DoraReadTool(Tool):
    """Read the latest cached value of a dataflow input."""

    def __init__(self, bridge: DoraAgentBridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_read"

    def description(self) -> str:
        return (
            "Read the latest cached value of a dataflow input. Available inputs: "
            "joint_positions, plan_status, execution_status, scene_state, "
            "gripper_status."
        )

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"input_id": {"type": "string"}},
            "required": ["input_id"],
        }

    def tags(self) -> list[str]:
        return ["transport", "read"]

    def execute(self, args: dict) -> ToolResult:
        self._bridge.drain()
        result = self._bridge.read_cached(args.get("input_id", ""))
        return ToolResult(output=json.dumps(result, default=str))


class DoraSendTool(Tool):
    """Send a fire-and-forget JSON command to a dataflow output."""

    def __init__(self, bridge: DoraAgentBridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_send"

    def description(self) -> str:
        return (
            "Send a fire-and-forget JSON command to a dataflow output "
            "(e.g. scene_command). Does not wait for a response."
        )

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "output_id": {"type": "string"},
                "data": {"type": "object"},
            },
            "required": ["output_id", "data"],
        }

    def tags(self) -> list[str]:
        return ["transport", "write"]

    def execute(self, args: dict) -> ToolResult:
        output_id = args.get("output_id", "")
        if not output_id:
            return ToolResult(output=json.dumps({"error": "output_id is required"}),
                              success=False)
        self._bridge.send_json(output_id, args.get("data", {}))
        return ToolResult(output=json.dumps({"success": True, "sent_to": output_id}))


class DoraCallTool(Tool):
    """Send a request and wait for its response (e.g. plan_request → plan_status)."""

    def __init__(self, bridge: DoraAgentBridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_call"

    def description(self) -> str:
        return (
            "Send a command and wait for its response. Use for plan_request "
            "(waits for plan_status) and gripper_command (waits for gripper_status). "
            "For plan_request the data is {\"start\": [6 floats], \"goal\": [6 floats]}; "
            "a named pose ('home', 'above_ball', …) is accepted for goal, and start "
            "defaults to the current joint_positions if omitted."
        )

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "output_id": {"type": "string"},
                "data": {"type": "object"},
                "response_id": {"type": "string"},
                "timeout_secs": {"type": "number"},
            },
            "required": ["output_id", "data", "response_id"],
        }

    def tags(self) -> list[str]:
        return ["transport", "request"]

    def execute(self, args: dict) -> ToolResult:
        output_id = args.get("output_id", "")
        data = args.get("data", {})
        response_id = args.get("response_id", "")
        timeout = float(args.get("timeout_secs", 30))

        if output_id == "plan_request":
            data = self._normalize_plan_request(data)

        self._bridge.send_json(output_id, data)
        response = self._bridge.wait_for_input(response_id, timeout)
        if response is None:
            return ToolResult(
                output=json.dumps({"error": f"timed out waiting for {response_id!r}"}),
                success=False,
            )
        return ToolResult(output=json.dumps({"response": response}, default=str))

    def _normalize_plan_request(self, data: object) -> dict:
        """Resolve named-pose goals and auto-inject `start` from joint_positions."""
        if not isinstance(data, dict):
            data = {}
        for key in ("start", "goal"):
            val = data.get(key)
            if isinstance(val, str):
                resolved = NAMED_POSES.get(val.lower().strip())
                if resolved is not None:
                    data[key] = list(resolved)
        if not data.get("start"):
            cached = self._bridge.cache.get("joint_positions")
            if cached is not None:
                data["start"] = list(cached[0])
        return data


# ---------------------------------------------------------------------------
# Assembly helpers
# ---------------------------------------------------------------------------

def build_bridge_registry(bridge: DoraAgentBridge) -> ToolRegistry:
    """Register the three dora transport tools and pin them as base tools."""
    registry = ToolRegistry()
    registry.register(DoraReadTool(bridge))
    registry.register(DoraSendTool(bridge))
    registry.register(DoraCallTool(bridge))
    registry.set_base_tools(["dora_read", "dora_send", "dora_call"])
    return registry


def compose_system_prompt(base_prompt: str, skills: list[SkillInfo]) -> str:
    """Append the always-on skills to the base system prompt."""
    section = skills_to_prompt(skills, always_only=True)
    if not section:
        return base_prompt
    return f"{base_prompt}\n\n# Skills\n\n{section}"
