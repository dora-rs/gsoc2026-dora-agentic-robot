"""Integration test (Week 8) — the agent drives the REAL MuJoCo simulator.

`test_integration_dataflow` runs the agent against `pipeline_stub`, which
*teleports* the arm. This test runs `ur5e_mujoco_agent.yml`: the same real agent,
but the motions are executed by the real Week 1 MuJoCo node — real actuators,
real settling dynamics, the real 21-dof qpos layout, the real Robotiq gripper.
The `sim_executor` streams joint targets and only reports a motion complete once
the physical arm has settled within tolerance, so `dora_move`'s "one call, one
finished motion" contract holds against real physics rather than a teleport.

This is the closest the pick-and-place gets to the live pipeline without the
dora-moveit2 OMPL/IK stack. It is skipped unless the `dora` CLI, `mujoco`, and
the fetched meshes (`scripts/fetch_ur5e_assets.sh`) are all present.

    pytest tests/test_integration_mujoco.py -q        # ~30s
    pytest tests/ -q -m "not integration"             # skip it
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATAFLOW = REPO / "dataflows" / "ur5e_mujoco_agent.yml"
ASSETS = REPO / "simulation" / "models" / "ur5e_assets"

STOP_AFTER = "30s"
SUBPROCESS_TIMEOUT = 240
EXPECTED_MOTIONS = 6

_have_meshes = ASSETS.is_dir() and any(ASSETS.glob("*.obj"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("dora") is None,
                       reason="dora CLI not installed"),
    pytest.mark.skipif(importlib.util.find_spec("mujoco") is None,
                       reason="mujoco not installed"),
    pytest.mark.skipif(not _have_meshes,
                       reason="UR5e meshes not fetched (scripts/fetch_ur5e_assets.sh)"),
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


def test_the_real_simulator_loaded(output):
    """It is the real UR5e+Robotiq scene (nq=21), not the stub's teleport."""
    loaded = _lines(output, "[sim] loaded")
    assert loaded, "mujoco_sim never loaded the model"
    assert "nq=21" in loaded[0], loaded[0]


def test_every_motion_physically_settled(output):
    """Each dora_move drove the real arm to within tolerance of its goal."""
    settled = _lines(output, "[sim_executor] motion")
    settled = [line for line in settled if "settled" in line]
    assert len(settled) == EXPECTED_MOTIONS, f"expected {EXPECTED_MOTIONS}:\n{settled}"


def test_no_motion_timed_out(output):
    """A settle timeout would mean the arm never reached a commanded pose."""
    assert not _lines(output, "[sim_executor] motion timed out"), \
        "a motion failed to converge in the real sim"


def test_gripper_grasp_release_sequence(output):
    """The real Robotiq node actuated open -> close -> open through the sim."""
    actions = [line.split("[gripper] ")[1].split(" ")[0]
               for line in _lines(output, "[gripper] ")
               if "controller started" not in line]
    assert actions == ["open", "close", "open"], actions


def test_mission_completes_on_real_physics(output):
    replies = _lines(output, "[mission_source] agent:")
    assert replies, "agent_response never arrived at mission_source"
    assert "Pick-and-place complete" in replies[-1]
