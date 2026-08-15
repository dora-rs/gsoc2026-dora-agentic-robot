"""Motion + introspection tools (Week 7) — the semantic layer above transport.

Week 6 gave the agent three *transport* tools (`dora_read`/`dora_send`/
`dora_call`): correct, but low level. To move the arm the model had to know the
output id, the request schema, the matching status input, and that a plan must be
followed by execution. That is pipeline plumbing, not robotics, and every bit of
it is a chance for the model to get it wrong.

Week 7 adds tools phrased in the robot's own terms, each one composed from the
Week 6 bridge:

    dora_move      move to a named pose or explicit joint vector — plans, then
                   waits for the motion to actually finish
    dora_gripper   open/close the Robotiq 2F-85 and wait for its status
    dora_perceive  one fused snapshot: arm state, gripper, scene objects
    dora_list      catalogue — named poses, dataflow inputs/outputs, scene objects
    skill          list dormant skills and pull one into the conversation

The transport tools stay registered: the semantic tools cover the common path,
and `dora_send`/`dora_call` remain the escape hatch for anything they do not.
Everything here runs against the injected bridge node, so it is unit-testable
with no dora daemon, no MuJoCo, and no LLM.
"""

from __future__ import annotations

import json

from simulation.named_poses import NAMED_POSES, NUM_JOINTS, SCENE_OBJECTS
from .bridge import (
    DoraAgentBridge,
    DoraCallTool,
    DoraReadTool,
    DoraSendTool,
)
from .skills import SkillRegistry
from .tool import Tool, ToolRegistry, ToolResult

# Inputs the agent can read, and outputs it can write, on this dataflow.
DATAFLOW_INPUTS = [
    "joint_positions",
    "joint_velocities",
    "plan_status",
    "execution_status",
    "ik_solution",
    "ik_status",
    "scene_state",
    "command_result",
    "gripper_status",
]
DATAFLOW_OUTPUTS = [
    "plan_request",
    "ik_request",
    "scene_command",
    "cartesian_trajectory",
    "gripper_command",
    "agent_response",
]

# The gripper controller's wire vocabulary (see simulation/gripper_controller.py).
GRIPPER_ACTIONS = ("open", "close")

DEFAULT_MOVE_TIMEOUT = 60.0
DEFAULT_GRIPPER_TIMEOUT = 15.0

# RRT-Connect is a randomised planner: a failed plan_status is often just an
# unlucky sample rather than a truly unreachable goal, and replanning from the
# same start frequently succeeds. dora_move therefore retries transient planning
# failures transparently, up to this many attempts, before handing the error
# back to the agent to solve at the task level (a different approach pose).
DEFAULT_MOVE_ATTEMPTS = 3

# How long a tool will actively wait for a sensor reading (joint_positions)
# before deciding the simulator is not producing state. Short: the sim
# republishes on every tick, so a value normally arrives within tens of ms.
STATE_WAIT_TIMEOUT = 1.0


def _err(message: str) -> ToolResult:
    """A failed ToolResult the agent can read and recover from."""
    return ToolResult(output=json.dumps({"error": message}), success=False)


class DoraMoveTool(Tool):
    """Move the arm to a named pose or explicit joint vector, and wait for it."""

    def __init__(self, bridge: DoraAgentBridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_move"

    def description(self) -> str:
        return (
            "Move the arm to a target configuration and wait for the motion to "
            "complete. `target` is either a named pose (" +
            ", ".join(sorted(NAMED_POSES)) +
            f") or a list of {NUM_JOINTS} joint angles in radians. The start "
            "configuration is read from the current joint_positions. A transient "
            "planning failure is retried automatically; only a persistent failure "
            "is returned as an error — recover from that by choosing a different "
            "approach pose. Returns the plan status, the number of attempts, and "
            "the execution status once the executor reports back."
        )

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "description": "Named pose, or a list of 6 joint angles (radians).",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "number"}},
                    ],
                },
                "timeout_secs": {"type": "number"},
                "max_attempts": {
                    "type": "integer",
                    "description": "How many times to replan a failed plan before "
                                   f"giving up (default {DEFAULT_MOVE_ATTEMPTS}).",
                },
            },
            "required": ["target"],
        }

    def tags(self) -> list[str]:
        return ["motion", "write"]

    def execute(self, args: dict) -> ToolResult:
        goal = self._resolve_target(args.get("target"))
        if isinstance(goal, str):  # resolution failed, `goal` is the message
            return _err(goal)

        timeout = float(args.get("timeout_secs", DEFAULT_MOVE_TIMEOUT))
        max_attempts = max(1, int(args.get("max_attempts", DEFAULT_MOVE_ATTEMPTS)))

        last_error = "planning failed"
        for attempt in range(1, max_attempts + 1):
            # Re-read the start each attempt: the arm may have settled or moved,
            # and the planner must plan from wherever it actually is now. Actively
            # wait for a joint reading if none is cached yet, so a first move does
            # not fail purely because nothing has drained the sensor queue.
            start = self._bridge.ensure_cached("joint_positions", STATE_WAIT_TIMEOUT)
            if start is None:
                return _err("no joint_positions available — is the simulator running?")

            request = {"start": list(start[0]), "goal": goal}
            self._bridge.send_json("plan_request", request)
            plan = self._bridge.wait_for_input("plan_status", timeout)

            if plan is None:
                last_error = f"timed out after {timeout}s waiting for plan_status"
                continue
            if isinstance(plan, dict) and plan.get("success") is False:
                last_error = f"planning failed: {plan.get('message', plan)}"
                continue

            # Planned. The trajectory goes planner -> executor directly; the
            # executor reports completion on execution_status. A pipeline without
            # an executor still counts as a successful plan, so a missing status
            # is not an error.
            execution = self._bridge.wait_for_input("execution_status", timeout)
            return ToolResult(output=json.dumps(
                {"goal": goal, "attempts": attempt,
                 "plan_status": plan, "execution_status": execution},
                default=str,
            ))

        return _err(f"{last_error} (gave up after {max_attempts} attempts)")

    def _resolve_target(self, target) -> list[float] | str:
        """Return the goal joint vector, or an error message string."""
        if isinstance(target, str):
            pose = NAMED_POSES.get(target.lower().strip())
            if pose is None:
                return f"unknown pose '{target}'. Known poses: {sorted(NAMED_POSES)}"
            return list(pose)
        if isinstance(target, (list, tuple)):
            if len(target) != NUM_JOINTS:
                return f"expected {NUM_JOINTS} joint angles, got {len(target)}"
            try:
                return [float(v) for v in target]
            except (TypeError, ValueError):
                return "joint angles must be numbers"
        return "target must be a named pose or a list of joint angles"


class DoraGripperTool(Tool):
    """Open or close the Robotiq 2F-85 and wait for its status."""

    def __init__(self, bridge: DoraAgentBridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_gripper"

    def description(self) -> str:
        return (
            "Open or close the Robotiq 2F-85 gripper and wait for it to report "
            "back. Close to grasp an object, open to release it."
        )

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(GRIPPER_ACTIONS)},
                "timeout_secs": {"type": "number"},
            },
            "required": ["action"],
        }

    def tags(self) -> list[str]:
        return ["motion", "gripper", "write"]

    def execute(self, args: dict) -> ToolResult:
        action = str(args.get("action", "")).lower().strip()
        if action not in GRIPPER_ACTIONS:
            return _err(f"action must be one of {list(GRIPPER_ACTIONS)}")

        timeout = float(args.get("timeout_secs", DEFAULT_GRIPPER_TIMEOUT))
        self._bridge.send_json("gripper_command", {"action": action})
        status = self._bridge.wait_for_input("gripper_status", timeout)
        if status is None:
            return _err(f"timed out after {timeout}s waiting for gripper_status")
        return ToolResult(output=json.dumps({"action": action, "gripper_status": status},
                                            default=str))


class DoraPerceiveTool(Tool):
    """One fused snapshot of the world: arm, gripper, and scene objects."""

    def __init__(self, bridge: DoraAgentBridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_perceive"

    def description(self) -> str:
        return (
            "Get a single snapshot of the current world state: arm joint "
            "positions (with the nearest named pose), gripper status, and the "
            "known scene objects with their positions. Call this before planning."
        )

    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    def tags(self) -> list[str]:
        return ["perception", "read"]

    def execute(self, args: dict) -> ToolResult:
        # Actively wait for a joint reading if none is cached yet, so perceive is
        # reliable as the very first call of a mission (nothing has drained the
        # sensor queue at that point).
        self._bridge.ensure_cached("joint_positions", STATE_WAIT_TIMEOUT)
        joints = self._bridge.read_cached("joint_positions")
        snapshot: dict = {
            "joint_positions": joints.get("data"),
            "joint_positions_age_ms": joints.get("age_ms"),
            "gripper_status": self._bridge.read_cached("gripper_status").get("data"),
            "scene_state": self._bridge.read_cached("scene_state").get("data"),
            "scene_objects": SCENE_OBJECTS,
        }
        if isinstance(snapshot["joint_positions"], list):
            snapshot["nearest_named_pose"] = _nearest_pose(snapshot["joint_positions"])
        else:
            snapshot["error"] = "no joint_positions cached yet"
        return ToolResult(output=json.dumps(snapshot, default=str))


class DoraListTool(Tool):
    """Catalogue what the agent can address: poses, wires, scene objects."""

    _KINDS = ("poses", "inputs", "outputs", "objects", "all")

    def __init__(self, bridge: DoraAgentBridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_list"

    def description(self) -> str:
        return (
            "List what is available to address in this dataflow: 'poses' (named "
            "arm configurations), 'inputs' (readable dataflow inputs), 'outputs' "
            "(writable dataflow outputs), 'objects' (scene objects), or 'all'."
        )

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(self._KINDS)}},
        }

    def tags(self) -> list[str]:
        return ["introspection", "read"]

    def execute(self, args: dict) -> ToolResult:
        kind = str(args.get("kind", "all")).lower().strip() or "all"
        if kind not in self._KINDS:
            return _err(f"kind must be one of {list(self._KINDS)}")

        catalogue = {
            "poses": {name: list(vals) for name, vals in NAMED_POSES.items()},
            "inputs": DATAFLOW_INPUTS,
            "outputs": DATAFLOW_OUTPUTS,
            "objects": SCENE_OBJECTS,
        }
        result = catalogue if kind == "all" else {kind: catalogue[kind]}
        # Show which inputs actually have data, so the model can tell a wire that
        # exists from one that has produced something.
        if kind in ("inputs", "all"):
            result["cached_inputs"] = sorted(self._bridge.cache)
        return ToolResult(output=json.dumps(result, default=str))


OBSTACLE_ACTIONS = ("add", "remove", "clear")
OBSTACLE_SHAPES = ("box", "sphere", "cylinder")
DEFAULT_SCENE_TIMEOUT = 5.0


class DoraObstacleTool(Tool):
    """Tell the planner about a workspace obstacle to avoid (or clear one).

    The planner does real collision checking, so an obstacle registered here is
    routed around by every subsequent `dora_move`. This is how the agent acts on
    an instruction like "there is a box at (x, y, z), don't hit it".
    """

    def __init__(self, bridge: DoraAgentBridge):
        self._bridge = bridge

    def name(self) -> str:
        return "dora_obstacle"

    def description(self) -> str:
        return (
            "Register or remove a workspace obstacle the arm must avoid. The "
            "planner routes every following motion around known obstacles. Use "
            "action='add' with a shape and world position to add one (before "
            "planning a move near it), 'remove' with a name, or 'clear' to remove "
            "all. Positions are in the world frame, in metres — the same frame "
            "dora_perceive reports scene objects in."
        )

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(OBSTACLE_ACTIONS)},
                "name": {"type": "string", "description": "Obstacle name (add/remove)."},
                "shape": {"type": "string", "enum": list(OBSTACLE_SHAPES)},
                "position": {"type": "array", "items": {"type": "number"},
                             "description": "World [x, y, z] in metres (add)."},
                "half_extents": {"type": "array", "items": {"type": "number"},
                                 "description": "Box half-sizes [hx, hy, hz]."},
                "radius": {"type": "number", "description": "Sphere/cylinder radius."},
                "height": {"type": "number", "description": "Cylinder height."},
                "timeout_secs": {"type": "number"},
            },
            "required": ["action"],
        }

    def tags(self) -> list[str]:
        return ["scene", "write"]

    def execute(self, args: dict) -> ToolResult:
        action = str(args.get("action", "")).lower().strip()
        if action not in OBSTACLE_ACTIONS:
            return _err(f"action must be one of {list(OBSTACLE_ACTIONS)}")

        command: dict = {"action": action}
        if action == "add":
            shape = str(args.get("shape", "")).lower().strip()
            if shape not in OBSTACLE_SHAPES:
                return _err(f"shape must be one of {list(OBSTACLE_SHAPES)}")
            obj = {"name": args.get("name", "obstacle"), "type": shape,
                   "position": args.get("position")}
            for key in ("half_extents", "radius", "height"):
                if key in args:
                    obj[key] = args[key]
            command["object"] = obj
        elif action == "remove":
            command["name"] = args.get("name")

        timeout = float(args.get("timeout_secs", DEFAULT_SCENE_TIMEOUT))
        self._bridge.send_json("scene_command", command)
        result = self._bridge.wait_for_input("scene_result", timeout)
        if result is None:
            # A planner without a scene_result wire still applied the command; a
            # missing ack is not a failure, just unconfirmed.
            return ToolResult(output=json.dumps({"action": action, "scene_result": None},
                                                default=str))
        return ToolResult(output=json.dumps({"action": action, "scene_result": result},
                                            default=str))


class SkillTool(Tool):
    """List dormant skills and pull one into the conversation on demand."""

    def __init__(self, registry: SkillRegistry):
        self._skills = registry

    def name(self) -> str:
        return "skill"

    def description(self) -> str:
        return (
            "Access the skill library — procedural knowledge that is not loaded "
            "into your system prompt by default. Use action='list' to see the "
            "available skills, then action='activate' with a name to read one in "
            "full. Activate the relevant skill before attempting a multi-step "
            "task such as picking and placing an object."
        )

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "activate"]},
                "name": {"type": "string"},
            },
            "required": ["action"],
        }

    def tags(self) -> list[str]:
        return ["skills", "read"]

    def execute(self, args: dict) -> ToolResult:
        action = str(args.get("action", "list")).lower().strip()
        if action == "list":
            return ToolResult(output=json.dumps({"skills": self._skills.catalogue()}))
        if action != "activate":
            return _err("action must be 'list' or 'activate'")

        name = str(args.get("name", "")).strip()
        if not name:
            return _err("name is required to activate a skill")
        try:
            skill = self._skills.activate(name)
        except KeyError as exc:
            return _err(str(exc).strip("\"'"))
        return ToolResult(output=json.dumps({
            "name": skill.name,
            "version": skill.version,
            "content": skill.content,
        }))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nearest_pose(joints: list[float], tolerance: float = 0.1) -> str | None:
    """Name of the closest named pose within `tolerance` (max per-joint error)."""
    best_name, best_error = None, float("inf")
    for name, pose in NAMED_POSES.items():
        if len(pose) != len(joints):
            continue
        error = max(abs(a - b) for a, b in zip(pose, joints))
        if error < best_error:
            best_name, best_error = name, error
    return best_name if best_error <= tolerance else None


def build_full_registry(
    bridge: DoraAgentBridge,
    skills: SkillRegistry | None = None,
) -> ToolRegistry:
    """Register the Week 6 transport tools plus the Week 7 semantic tools.

    The semantic tools (`dora_move`, `dora_gripper`, `dora_perceive`,
    `dora_list`) and `skill` are pinned as base tools — they are the ones the
    model needs on every mission, so LRU eviction must never take them. The raw
    transport tools stay registered as the escape hatch.
    """
    registry = ToolRegistry()
    registry.register(DoraReadTool(bridge))
    registry.register(DoraSendTool(bridge))
    registry.register(DoraCallTool(bridge))
    registry.register(DoraMoveTool(bridge))
    registry.register(DoraGripperTool(bridge))
    registry.register(DoraPerceiveTool(bridge))
    registry.register(DoraListTool(bridge))
    registry.register(DoraObstacleTool(bridge))

    base = ["dora_move", "dora_gripper", "dora_perceive", "dora_list", "dora_obstacle"]
    if skills is not None:
        registry.register(SkillTool(skills))
        base.append("skill")
    registry.set_base_tools(base)
    return registry
