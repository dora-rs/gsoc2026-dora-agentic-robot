"""Week 10 — the pick-and-place completes through the REAL planner.

Deterministic (MockProvider, no API key), so it belongs in the normal suite —
but it needs the dora-moveit2 planner importable, so it skips on a bare checkout.
It is the CI-able proof that `dora_move` now goes through genuine RRT-Connect path
search: the ball reaches the plate *and* every motion was a real multi-waypoint
plan, not a teleport. The live-LLM counterpart lives in `test_live_llm.py`.
"""

import pytest

from simulation.rrt_planner_node import load_planner


def _dora_moveit_available() -> bool:
    try:
        load_planner()
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _dora_moveit_available(),
    reason="dora-moveit2 planner not importable (set DORA_MOVEIT2_PATH)",
)


def test_scripted_pick_place_runs_through_the_real_planner():
    from agent.live_planner_demo import run_scripted

    node = run_scripted(verbose=False)

    assert node.ball_on_plate(), f"ball ended at {node.ball_position}"
    assert not node.holding and not node.gripper_closed
    # Every move was a genuine plan, and every plan had multiple waypoints.
    assert node.plans, "no motions were planned"
    assert all(n >= 2 for n in node.plans), f"a motion was not planned: {node.plans}"
    assert len(node.plans) == 6, f"expected 6 planned motions, got {node.plans}"
