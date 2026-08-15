"""Week 10 — the real dora-moveit2 RRT-Connect planner, exercised in-process.

`rrt_planner_node.py` imports the genuine `OMPLPlanner` from the sibling
dora-moveit2 checkout (path-search half only — no OMPL/TracIK/toppra C++ deps).
The pure helpers (resolve/flatten) run anywhere; the tests that actually plan are
skipped when dora-moveit2 is not importable, so a bare CI checkout still passes
while this machine runs the real planner.
"""

import numpy as np
import pytest

from simulation.named_poses import NAMED_POSES
from simulation.rrt_planner_node import (
    flatten_trajectory,
    load_planner,
    resolve_config,
)


def _dora_moveit_available() -> bool:
    try:
        load_planner()
        return True
    except ImportError:
        return False


needs_planner = pytest.mark.skipif(
    not _dora_moveit_available(),
    reason="dora-moveit2 planner not importable (set DORA_MOVEIT2_PATH)",
)


# --- pure helpers: no dora-moveit2 needed --------------------------------

def test_resolve_config_named_pose():
    assert resolve_config("above_ball") == list(NAMED_POSES["above_ball"])
    assert resolve_config("HOME") == list(NAMED_POSES["home"])  # case-insensitive


def test_resolve_config_explicit_vector():
    assert resolve_config([0, 1, 2, 3, 4, 5]) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_resolve_config_rejects_bad_input():
    assert resolve_config("nowhere") is None
    assert resolve_config([1, 2]) is None          # wrong length
    assert resolve_config(["a"] * 6) is None        # non-numeric
    assert resolve_config(None) is None


def test_flatten_trajectory_is_row_major():
    traj = [[0, 1, 2, 3, 4, 5], [6, 7, 8, 9, 10, 11]]
    flat = flatten_trajectory(traj)
    assert flat == [float(i) for i in range(12)]
    assert np.array(flat).reshape(-1, 6).tolist() == [[float(v) for v in w] for w in traj]


# --- the real planner ----------------------------------------------------

@needs_planner
def test_real_planner_produces_a_valid_ur5e_path():
    """RRT-Connect returns a multi-waypoint path that starts at start, ends at
    goal, and stays within the UR5e joint limits — the genuine dora-moveit2
    algorithm, configured for our arm."""
    from simulation.rrt_planner_node import plan_path

    np.random.seed(0)
    planner, PlanRequest, PlannerType = load_planner()
    start = resolve_config("home")
    goal = resolve_config("above_ball")

    traj, status = plan_path(planner, PlanRequest, PlannerType, start, goal)

    assert status["success"] is True
    assert status["num_waypoints"] >= 2
    t = np.array(traj)
    assert t.shape[1] == 6
    assert np.allclose(t[0], start, atol=1e-6), "path must start at the start config"
    assert np.allclose(t[-1], goal, atol=0.05), "path must end at the goal"
    lo, hi = planner.joint_limits_lower, planner.joint_limits_upper
    assert np.all(t >= lo - 1e-9) and np.all(t <= hi + 1e-9), "waypoints within limits"


def test_plan_path_surfaces_a_planner_failure():
    """When the planner reports failure, the node returns an empty trajectory and
    a success:False status — the signal dora_move replans on. Uses a fake failing
    planner so the failure *plumbing* is tested without needing dora-moveit2.

    Note: the current dora-moveit2 planner has collision checking stubbed
    (is_state_valid returns True), so RRT-Connect seldom fails on its own; the
    recovery trigger is real and tested here, and fires for real once collision
    checking is enabled (Week 11)."""
    from types import SimpleNamespace

    from simulation.rrt_planner_node import plan_path

    class FakePlannerType:
        RRT_CONNECT = "rrt_connect"

        def __init__(self, value):
            self.value = value

    def FakePlanRequest(**kwargs):
        return SimpleNamespace(**kwargs)

    class FailingPlanner:
        def plan(self, request):
            return SimpleNamespace(
                success=False, trajectory=[], planning_time=0.2,
                path_length=0.0, message="RRT-Connect failed after 5000 iterations")

    traj, status = plan_path(
        FailingPlanner(), FakePlanRequest, FakePlannerType,
        resolve_config("home"), resolve_config("above_ball"))

    assert status["success"] is False
    assert traj == []
    assert "failed" in status["message"]
