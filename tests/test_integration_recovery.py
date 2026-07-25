"""Integration test (Week 8) — the agent recovers from a real planner failure.

`test_integration_dataflow` proves the happy path across real dora processes.
This one proves *recovery*: `ur5e_agent_recovery.yml` tells the pipeline_stub to
reject the first two plan_requests (FAIL_FIRST_N=2), so the first motion of the
pick-and-place fails to plan twice before it can succeed. The mission only
completes if `dora_move` transparently replans — the retry loop is real code
running in a real node, and a build without it stalls at the first move.

    pytest tests/test_integration_recovery.py -q      # ~25s
    pytest tests/ -q -m "not integration"             # skip it
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATAFLOW = REPO / "dataflows" / "ur5e_agent_recovery.yml"

STOP_AFTER = "25s"
SUBPROCESS_TIMEOUT = 180
INJECTED_FAILURES = 2   # must match FAIL_FIRST_N in the dataflow
EXPECTED_MOTIONS = 6    # the pick-and-place still completes all six

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("dora") is None,
                       reason="dora CLI not installed"),
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


def test_planner_failures_were_actually_injected(output):
    """The stub really did reject the first plans — otherwise there is nothing
    to recover from and the rest of this test is vacuous."""
    rejections = _lines(output, "[pipeline_stub] REJECTED plan")
    assert len(rejections) == INJECTED_FAILURES, rejections


def test_all_motions_still_complete_despite_the_failures(output):
    """dora_move replanned through the failures: every motion executes."""
    motions = _lines(output, "[pipeline_stub] motion")
    assert len(motions) == EXPECTED_MOTIONS, f"expected {EXPECTED_MOTIONS}:\n{motions}"


def test_mission_completes_after_recovery(output):
    """The reply completes the loop — the whole task survived the failures."""
    replies = _lines(output, "[mission_source] agent:")
    assert replies, "agent_response never arrived at mission_source"
    assert "Pick-and-place complete" in replies[-1]


def test_recovery_did_not_skip_the_first_pose(output):
    """The recovered first motion is the approach pose, not a later one — proof
    the arm did not silently drop a step while replanning."""
    first_motion = _lines(output, "[pipeline_stub] motion 1")[0]
    assert "2.204" in first_motion, first_motion  # above_ball, shoulder_pan
