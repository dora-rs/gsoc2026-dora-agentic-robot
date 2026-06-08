"""Unit tests for the gripper controller mapping — no MuJoCo/dora needed."""

import pytest

from simulation.gripper_controller import (
    GRIPPER_MAX_WIDTH,
    CTRL_OPEN,
    CTRL_CLOSED,
    clamp_width,
    width_to_ctrl,
    state_for_width,
    resolve_command,
)


def test_width_to_ctrl_endpoints():
    assert width_to_ctrl(GRIPPER_MAX_WIDTH) == pytest.approx(CTRL_OPEN)   # open -> 0
    assert width_to_ctrl(0.0) == pytest.approx(CTRL_CLOSED)               # closed -> 255


def test_width_to_ctrl_is_monotonic_decreasing():
    # Wider opening -> smaller ctrl value.
    assert width_to_ctrl(0.02) > width_to_ctrl(0.06)


def test_clamp_width_bounds():
    assert clamp_width(-1.0) == 0.0
    assert clamp_width(10.0) == GRIPPER_MAX_WIDTH


def test_state_for_width():
    assert state_for_width(GRIPPER_MAX_WIDTH) == "open"
    assert state_for_width(0.0) == "closed"
    assert state_for_width(0.04) == "partial"


def test_resolve_open_close():
    o = resolve_command({"action": "open"})
    assert o["state"] == "open" and o["ctrl"] == pytest.approx(0.0)
    c = resolve_command({"action": "close"})
    assert c["state"] == "closed" and c["ctrl"] == pytest.approx(255.0)


def test_resolve_set_position_clamped():
    r = resolve_command({"action": "set_position", "position": 0.04})
    assert r["width"] == 0.04 and r["state"] == "partial"
    assert 0.0 < r["ctrl"] < 255.0


def test_resolve_set_position_requires_position():
    with pytest.raises(ValueError):
        resolve_command({"action": "set_position"})


def test_resolve_unknown_action_raises():
    with pytest.raises(ValueError):
        resolve_command({"action": "wiggle"})


def test_resolve_is_case_insensitive():
    assert resolve_command({"action": "OPEN"})["state"] == "open"
