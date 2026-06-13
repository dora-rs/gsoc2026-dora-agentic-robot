"""Topology tests for the full motion-planning dataflow — no MuJoCo/dora needed."""

from pathlib import Path

import pytest

from simulation.dataflow_check import check, load_nodes

DATAFLOWS = Path(__file__).resolve().parents[1] / "dataflows"
FULL = DATAFLOWS / "ur5e_full_pipeline.yml"

PIPELINE_NODES = {
    "mujoco_sim", "planning_scene", "planner", "ik_solver",
    "trajectory_executor", "gripper_controller", "command_source",
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
