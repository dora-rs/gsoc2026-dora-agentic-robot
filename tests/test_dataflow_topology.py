"""Topology tests for the full motion-planning dataflow — no MuJoCo/dora needed."""

from pathlib import Path

import pytest
import yaml

from simulation.dataflow_check import check, load_nodes

DATAFLOWS = Path(__file__).resolve().parents[1] / "dataflows"
FULL = DATAFLOWS / "ur5e_full_pipeline.yml"
AGENT = DATAFLOWS / "ur5e_agent_demo.yml"
LOOPBACK = DATAFLOWS / "ur5e_agent_loopback.yml"
RECOVERY = DATAFLOWS / "ur5e_agent_recovery.yml"
MUJOCO_AGENT = DATAFLOWS / "ur5e_mujoco_agent.yml"

LOOPBACK_NODES = {"pipeline_stub", "gripper_controller", "agent_bridge", "mission_source"}
MUJOCO_AGENT_NODES = {"mujoco_sim", "sim_executor", "gripper_controller",
                      "agent_bridge", "mission_source"}

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


# --- Week 8 recovery dataflow --------------------------------------------

def test_recovery_dataflow_has_no_dangling_edges():
    result = check(str(RECOVERY))
    assert result.ok, "dangling wires:\n" + "\n".join(result.errors)


def test_recovery_is_the_loopback_plus_fault_injection():
    """Same topology as the loopback; the only change is the injected failures."""
    assert set(load_nodes(str(RECOVERY))) == LOOPBACK_NODES
    spec = yaml.safe_load(RECOVERY.read_text())
    stub = next(n for n in spec["nodes"] if n["id"] == "pipeline_stub")
    assert int(stub["env"]["FAIL_FIRST_N"]) >= 1, "recovery run must inject a failure"


def test_recovery_uses_only_repo_nodes():
    spec = yaml.safe_load(RECOVERY.read_text())
    for node in spec["nodes"]:
        path = node.get("path", "")
        assert path.startswith("../") and "dora-moveit2" not in path, path
        assert (DATAFLOWS / path).resolve().is_file(), path


# --- Week 8 real-MuJoCo agent dataflow -----------------------------------

def test_mujoco_agent_has_no_dangling_edges():
    result = check(str(MUJOCO_AGENT))
    assert result.ok, "dangling wires:\n" + "\n".join(result.errors)


def test_mujoco_agent_nodes_present():
    assert set(load_nodes(str(MUJOCO_AGENT))) == MUJOCO_AGENT_NODES


def test_mujoco_agent_drives_the_real_sim_through_the_executor():
    """The agent commands sim_executor, which drives the real mujoco_sim, whose
    joint_positions feed back to the agent — a closed loop through real physics."""
    nodes = load_nodes(str(MUJOCO_AGENT))
    assert nodes["sim_executor"].inputs["plan_request"] == "agent_bridge/plan_request"
    assert nodes["mujoco_sim"].inputs["control_input"] == "sim_executor/control_input"
    assert nodes["sim_executor"].inputs["joint_positions"] == "mujoco_sim/joint_positions"
    assert nodes["agent_bridge"].inputs["joint_positions"] == "mujoco_sim/joint_positions"
    assert nodes["agent_bridge"].inputs["plan_status"] == "sim_executor/plan_status"
    # Grasping still goes through the real Week 2 gripper node into the sim.
    assert nodes["mujoco_sim"].inputs["gripper_ctrl"] == "gripper_controller/gripper_ctrl"


def test_mujoco_agent_uses_the_real_sim_not_the_stub():
    spec = yaml.safe_load(MUJOCO_AGENT.read_text())
    paths = {n["id"]: n.get("path", "") for n in spec["nodes"]}
    assert paths["mujoco_sim"].endswith("mujoco_node.py")
    assert "pipeline_stub" not in paths  # this dataflow is real physics
    for path in paths.values():
        assert "dora-moveit2" not in path, path
        assert (DATAFLOWS / path).resolve().is_file(), path


def test_mujoco_agent_bridge_matches_the_live_dataflow():
    """The agent side is identical to ur5e_agent_demo.yml — only the motion
    stack below it differs."""
    live = load_nodes(str(AGENT))["agent_bridge"]
    mj = load_nodes(str(MUJOCO_AGENT))["agent_bridge"]
    assert set(mj.outputs) == set(live.outputs)
    assert mj.inputs["joint_positions"] == live.inputs["joint_positions"]


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
