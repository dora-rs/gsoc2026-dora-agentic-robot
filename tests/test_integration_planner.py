"""Integration test (Week 11) — the REAL collision-checked planner over dora.

`test_integration_mujoco` drives the arm with `sim_executor` (direct control).
This runs `ur5e_mujoco_planner.yml`: the genuine dora-moveit2 RRT-Connect planner
(collision checking on) plans a multi-waypoint path for each `dora_move`, and the
Week 10 trajectory executor follows it through the real MuJoCo physics — a live
dora run of the whole planner pipeline. The scripted mock provider drives it so it
is deterministic and needs no API key. The live-LLM counterpart is
`ur5e_mujoco_planner_llm.yml`.

Skipped unless the `dora` CLI, `mujoco`, the fetched meshes, and the dora-moveit2
planner are all present.

    pytest tests/test_integration_planner.py -q       # ~60s
    pytest tests/ -q -m "not integration"             # skip it
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATAFLOW = REPO / "dataflows" / "ur5e_mujoco_planner.yml"
ASSETS = REPO / "simulation" / "models" / "ur5e_assets"

STOP_AFTER = "70s"
SUBPROCESS_TIMEOUT = 260
EXPECTED_MOTIONS = 6


def _planner_importable() -> bool:
    import sys
    try:
        from simulation.rrt_planner_node import load_planner
        load_planner()
        return True
    except Exception:
        return False


_have_meshes = ASSETS.is_dir() and any(ASSETS.glob("*.obj"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("dora") is None, reason="dora CLI not installed"),
    pytest.mark.skipif(importlib.util.find_spec("mujoco") is None,
                       reason="mujoco not installed"),
    pytest.mark.skipif(not _have_meshes,
                       reason="UR5e meshes not fetched (scripts/fetch_ur5e_assets.sh)"),
    pytest.mark.skipif(not _planner_importable(),
                       reason="dora-moveit2 planner not importable"),
]


@pytest.fixture(scope="module")
def output() -> str:
    result = subprocess.run(
        ["dora", "run", DATAFLOW.name, "--stop-after", STOP_AFTER],
        cwd=DATAFLOW.parent, capture_output=True, text=True,
        timeout=SUBPROCESS_TIMEOUT,
    )
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, f"a node crashed:\n{combined[-3000:]}"
    return combined


def _lines(output: str, needle: str) -> list[str]:
    return [line for line in output.splitlines() if needle in line]


def test_the_real_planner_ran_with_collision_on(output):
    ready = _lines(output, "[rrt_planner] ready")
    assert ready, "the planner node never started"
    assert "collision=on" in ready[0], ready[0]


def test_every_motion_produced_a_multi_waypoint_plan(output):
    """Each dora_move went through real RRT-Connect, not a teleport."""
    planned = _lines(output, "[rrt_planner] planned")
    assert len(planned) == EXPECTED_MOTIONS, f"expected {EXPECTED_MOTIONS}:\n{planned}"
    # every plan had multiple waypoints
    for line in planned:
        n = int(line.split("planned ")[1].split(" waypoints")[0])
        assert n >= 2, line


def test_every_path_was_followed_on_real_physics(output):
    """The trajectory executor settled the arm through each whole planned path."""
    complete = _lines(output, "[trajectory_executor] path")
    complete = [line for line in complete if "complete" in line]
    assert len(complete) == EXPECTED_MOTIONS, f"expected {EXPECTED_MOTIONS}:\n{complete}"


def test_no_path_timed_out(output):
    assert not _lines(output, "[trajectory_executor] path timed out"), \
        "a planned path failed to converge in the real sim"


def test_mission_completes_through_the_real_planner(output):
    replies = _lines(output, "[mission_source] agent:")
    assert replies, "agent_response never arrived at mission_source"
    assert "Pick-and-place complete" in replies[-1]
