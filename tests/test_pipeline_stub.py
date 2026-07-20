"""Unit tests for the Week 7 pipeline stub's pure logic — no dora needed.

The stub node itself is exercised for real by `test_integration_dataflow`; these
cover the goal parsing and qpos layout it depends on, which is where a silent
mismatch with `mujoco_sim` would hide.
"""

from simulation.named_poses import NAMED_POSES
from simulation.pipeline_stub import FREE_JOINT_QPOS, full_qpos, parse_goal

HOME = list(NAMED_POSES["home"])


def test_full_qpos_matches_the_scene_layout():
    """The arm must land at qpos[7:13], where the bridge expects to read it."""
    qpos = full_qpos(HOME)
    assert len(qpos) == FREE_JOINT_QPOS + 6
    assert qpos[FREE_JOINT_QPOS:] == HOME
    assert qpos[:FREE_JOINT_QPOS] == [0.0] * FREE_JOINT_QPOS


def test_parse_goal_resolves_named_poses():
    assert parse_goal({"goal": "above_ball"}) == list(NAMED_POSES["above_ball"])
    assert parse_goal({"goal": "HOME"}) == HOME  # case-insensitive


def test_parse_goal_accepts_explicit_joints():
    assert parse_goal({"goal": [0, 1, 2, 3, 4, 5]}) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_parse_goal_rejects_bad_input():
    assert parse_goal({}) is None
    assert parse_goal({"goal": "nowhere"}) is None
    assert parse_goal({"goal": [1, 2]}) is None
    assert parse_goal({"goal": ["a"] * 6}) is None
