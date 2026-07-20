"""Integration test (Week 7) — run a **real** dora dataflow, end to end.

Every other test in this suite is in-process: a fake node, a fake pipeline. Those
catch logic bugs, but they cannot catch the class of bug that only exists once
dora spawns each node as its own process — import layout, wire encodings, and
startup ordering between nodes. (Both of those bit us: the bridge node's relative
imports failed under `dora start`, and a mission published during the bridge's
startup drain was cached instead of run. Neither was visible in-process.)

So this test runs `dora run dataflows/ur5e_agent_loopback.yml` for real — four
node processes, real dora IPC — and asserts the agent completed the whole
pick-and-place. It needs no dora-moveit2, no MuJoCo, no display, and no API key;
it is skipped when the `dora` CLI is not installed.

    pytest tests/test_integration_dataflow.py -q       # ~30s
    pytest tests/ -q -m "not integration"              # skip it
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATAFLOW = REPO / "dataflows" / "ur5e_agent_loopback.yml"

# The dataflow has no natural end (nodes wait for more missions), so we stop it
# on a timer. The mission itself finishes in a few seconds.
STOP_AFTER = "25s"
SUBPROCESS_TIMEOUT = 180

# The pick-and-place procedure: 6 planned motions, and a close between the two
# opens. Asserted as an ordered sequence, so a shuffled run fails.
EXPECTED_MOTIONS = 6

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("dora") is None,
                       reason="dora CLI not installed"),
]


@pytest.fixture(scope="module")
def dataflow_output() -> str:
    """Run the loopback dataflow once and return its combined output."""
    result = subprocess.run(
        ["dora", "run", DATAFLOW.name, "--stop-after", STOP_AFTER],
        cwd=DATAFLOW.parent,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
    )
    output = result.stdout + result.stderr
    assert "Traceback" not in output, f"a node crashed:\n{output[-3000:]}"
    return output


def _lines(output: str, needle: str) -> list[str]:
    return [line for line in output.splitlines() if needle in line]


def test_all_nodes_start(dataflow_output):
    """Every node reaches its main loop — catches import/spawn failures."""
    assert _lines(dataflow_output, "[gripper] Robotiq 2F-85 controller started")
    assert _lines(dataflow_output, "[mission_source] ->")
    assert _lines(dataflow_output, "[bridge] loaded skill")


def test_skills_load_in_the_live_node(dataflow_output):
    """Both always-on skills and the dormant one are discovered from disk."""
    for skill in ("ur5e-arm", "dora-transport", "pick-and-place"):
        assert _lines(dataflow_output, f"loaded skill '{skill}'"), skill


def test_agent_receives_the_mission(dataflow_output):
    """The mission crosses the mission_source -> agent_bridge wire."""
    assert _lines(dataflow_output, "[bridge] >>> mission: Pick up the red ball")


def test_dormant_skill_is_activated_on_demand(dataflow_output):
    """The agent pulls the pick-and-place procedure in mid-mission."""
    assert _lines(dataflow_output, 'skill({"action":"activate","name":"pick-and-place"}')


def test_all_motions_execute_through_the_pipeline(dataflow_output):
    """Each dora_move round-trips: plan_request -> plan_status -> execution."""
    motions = _lines(dataflow_output, "[pipeline_stub] motion")
    assert len(motions) == EXPECTED_MOTIONS, f"expected {EXPECTED_MOTIONS}:\n{motions}"


def test_gripper_sequence_is_open_close_open(dataflow_output):
    """The real Week 2 gripper node sees a valid grasp/release ordering."""
    actions = [line.split("[gripper] ")[1].split(" ")[0]
               for line in _lines(dataflow_output, "[gripper] ")
               if "controller started" not in line]
    assert actions == ["open", "close", "open"], actions


def test_arm_ends_at_home(dataflow_output):
    """The final motion returns the arm to the home pose."""
    last = _lines(dataflow_output, "[pipeline_stub] motion")[-1]
    assert "-1.571, -1.571, 1.571, -1.571, -1.571, 0.0" in last, last


def test_agent_reply_reaches_the_mission_source(dataflow_output):
    """The response completes the loop back to the node that sent the mission."""
    replies = _lines(dataflow_output, "[mission_source] agent:")
    assert replies, "agent_response never arrived at mission_source"
    assert "Pick-and-place complete" in replies[-1]
