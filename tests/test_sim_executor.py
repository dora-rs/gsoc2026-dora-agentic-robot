"""Unit tests for the Week 8 sim executor's pure logic — no dora, no MuJoCo.

The node itself is exercised for real by `test_integration_mujoco`; these cover
the settle check and arm-slicing, where a silent mismatch with `mujoco_sim`'s
qpos layout would hide.
"""

from simulation.named_poses import NAMED_POSES
from simulation.sim_executor import _float_env, arm_slice, settled

HOME = list(NAMED_POSES["home"])


def test_arm_slice_reads_qpos_7_to_13():
    """The arm lives after the red ball's 7-dof free joint, whatever the total nq."""
    # Real ur5e_scene has nq=21 (ball 7 + arm 6 + gripper 8); the arm is [7:13].
    full = [9.0] * 7 + HOME + [0.5] * 8
    assert arm_slice(full) == HOME


def test_arm_slice_tolerates_a_short_vector():
    assert arm_slice(HOME) == HOME  # already just the 6 arm joints


def test_settled_true_within_tolerance():
    goal = list(HOME)
    near = [v + 0.04 for v in goal]
    assert settled(near, goal, tol=0.05)


def test_settled_false_when_any_joint_is_off():
    goal = list(HOME)
    off = list(goal)
    off[3] += 0.2
    assert not settled(off, goal, tol=0.05)


def test_settled_false_on_length_mismatch():
    assert not settled([0.0, 0.0], HOME, tol=1.0)


def test_float_env_parses_and_defaults(monkeypatch):
    monkeypatch.setenv("SETTLE_TOL_RAD", "0.02")
    assert _float_env("SETTLE_TOL_RAD", 0.05) == 0.02
    monkeypatch.setenv("SETTLE_TOL_RAD", "junk")
    assert _float_env("SETTLE_TOL_RAD", 0.05) == 0.05
    monkeypatch.delenv("SETTLE_TOL_RAD", raising=False)
    assert _float_env("SETTLE_TOL_RAD", 0.05) == 0.05
