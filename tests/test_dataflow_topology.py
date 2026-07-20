"""Topology tests for the full motion-planning dataflow — no MuJoCo/dora needed."""

from pathlib import Path

import pytest
import yaml

from simulation.dataflow_check import check, load_nodes

DATAFLOWS = Path(__file__).resolve().parents[1] / "dataflows"
FULL = DATAFLOWS / "ur5e_full_pipeline.yml"
AGENT = DATAFLOWS / "ur5e_agent_demo.yml"
LOOPBACK = DATAFLOWS / "ur5e_agent_loopback.yml"

LOOPBACK_NODES = {"pipeline_stub", "gripper_controller", "agent_bridge", "mission_source"}

PIPELINE_NODES = {
    "mujoco_sim", "planning_scene", "planner", "ik_solver",
    "trajectory_executor", "gripper_controller", "command_source",
}

AGENT_NODES = {
    "mujoco_sim", "planning_scene", "planner", "ik_solver",
    "trajectory_executor", "gripper_controller", "agent_bridge", "mission_source",
}


def test_full_pipeline_has_no_dangling_edges():
    result = check(str(FULL))
    assert result.ok, "dangling wires:\n" + "\n".join(result.errors)


def test_all_pipeline_nodes_present():
    nodes = load_nodes(str(FULL))
    assert set(nodes) == PIPELINE_NODES


def test_sensor_outputs_exposed():
    nodes = load_nodes(str(FULL))
    sim_out = set(nodes["mujoco_sim"].outputs)
    assert {"joint_positions", "joint_velocities", "sensor_data"} <= sim_out


def test_actuator_inputs_wired():
    nodes = load_nodes(str(FULL))
    sim_in = nodes["mujoco_sim"].inputs
    # Arm tracking: control comes from the trajectory executor.
    assert sim_in["control_input"] == "trajectory_executor/joint_commands"
    # Grasping: gripper actuator comes from the gripper controller.
    assert sim_in["gripper_ctrl"] == "gripper_controller/gripper_ctrl"


def test_planner_feedback_loop():
    nodes = load_nodes(str(FULL))
    # planner consumes scene updates and emits trajectory + status
    assert nodes["planner"].inputs["scene_update"] == "planning_scene/scene_update"
    assert "trajectory" in nodes["planner"].outputs
    assert "plan_status" in nodes["planner"].outputs
    # executor consumes the planned trajectory
    assert nodes["trajectory_executor"].inputs["trajectory"] == "planner/trajectory"


def test_all_six_subsystems_chain_to_sim():
    """Every motion subsystem must ultimately reach mujoco_sim (closed loop)."""
    nodes = load_nodes(str(FULL))
    # trajectory_executor -> mujoco_sim, planner -> trajectory_executor,
    # planning_scene -> planner, gripper_controller -> mujoco_sim.
    assert nodes["trajectory_executor"].inputs["joint_positions"] == "mujoco_sim/joint_positions"
    assert nodes["planning_scene"].inputs["robot_state"] == "mujoco_sim/joint_positions"


def test_agent_dataflow_has_no_dangling_edges():
    result = check(str(AGENT))
    assert result.ok, "dangling wires:\n" + "\n".join(result.errors)


def test_agent_dataflow_nodes_present():
    assert set(load_nodes(str(AGENT))) == AGENT_NODES


def test_agent_bridge_replaces_command_source():
    """The agent_bridge drives the pipeline inputs command_source used to drive."""
    nodes = load_nodes(str(AGENT))
    assert "command_source" not in nodes
    assert nodes["planner"].inputs["plan_request"] == "agent_bridge/plan_request"
    assert nodes["gripper_controller"].inputs["gripper_command"] == "agent_bridge/gripper_command"
    assert nodes["planning_scene"].inputs["scene_command"] == "agent_bridge/scene_command"


def test_agent_bridge_reads_mission_and_sensors():
    nodes = load_nodes(str(AGENT))
    bridge = nodes["agent_bridge"]
    assert bridge.inputs["user_command"] == "mission_source/user_command"
    assert bridge.inputs["joint_positions"] == "mujoco_sim/joint_positions"
    assert "agent_response" in bridge.outputs


# --- Week 7 loopback dataflow --------------------------------------------

def test_loopback_dataflow_has_no_dangling_edges():
    result = check(str(LOOPBACK))
    assert result.ok, "dangling wires:\n" + "\n".join(result.errors)


def test_loopback_dataflow_nodes_present():
    assert set(load_nodes(str(LOOPBACK))) == LOOPBACK_NODES


def test_loopback_uses_only_repo_nodes():
    """It must run without dora-moveit2 — every node path is inside this repo."""
    spec = yaml.safe_load(LOOPBACK.read_text())
    paths = [node.get("path", "") for node in spec["nodes"]]
    assert paths, "no node paths found"
    for path in paths:
        assert path.startswith("../"), path
        assert "dora-moveit2" not in path, path
        assert (DATAFLOWS / path).resolve().is_file(), path


def test_loopback_bridge_is_the_same_node_as_the_live_dataflow():
    """The agent side is real; only the motion stack is substituted."""
    live = load_nodes(str(AGENT))["agent_bridge"]
    loop = load_nodes(str(LOOPBACK))["agent_bridge"]
    assert set(loop.outputs) == set(live.outputs)
    assert loop.inputs["user_command"] == live.inputs["user_command"]


def test_loopback_stub_closes_the_motion_loop():
    nodes = load_nodes(str(LOOPBACK))
    stub = nodes["pipeline_stub"]
    assert stub.inputs["plan_request"] == "agent_bridge/plan_request"
    assert {"joint_positions", "plan_status", "execution_status"} <= set(stub.outputs)
    assert nodes["agent_bridge"].inputs["joint_positions"] == "pipeline_stub/joint_positions"


def test_detects_dangling_edge(tmp_path):
    """The checker must flag a broken wire."""
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "nodes:\n"
        "  - id: a\n"
        "    outputs: [x]\n"
        "  - id: b\n"
        "    inputs:\n"
        "      y: a/does_not_exist\n"
    )
    result = check(str(bad))
    assert not result.ok
    assert any("does_not_exist" in e for e in result.errors)
