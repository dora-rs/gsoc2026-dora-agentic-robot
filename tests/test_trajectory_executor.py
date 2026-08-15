"""Week 10 — the trajectory executor's path-following state machine.

`sim_executor` drove to a single goal; this node walks the arm through the
planner's whole multi-waypoint path, advancing only once each waypoint has
physically settled. The integration path (real dora + MuJoCo) is what
`test_integration_mujoco` covers; here a closed-loop fake node — one that answers
every `control_input` with a settled `joint_positions` — drives the state machine
deterministically, so the advance-on-settle and report-once-at-the-end logic is
pinned down without a simulator.
"""

import json

import numpy as np

from simulation.trajectory_executor_node import main, reshape_trajectory

FREE = [0.0] * 7  # the red ball's free joint occupies qpos[0:7]; arm follows


class _Val:
    """Minimal stand-in for a pyarrow array as the node reads it."""

    def __init__(self, data):
        self._data = data

    def to_numpy(self):
        return np.array(self._data, dtype=float)

    def to_pylist(self):
        return list(self._data)


def _decode(value) -> dict:
    return json.loads(bytes(value.to_pylist()).decode("utf-8"))


class ClosedLoopNode:
    """Feeds a trajectory, then answers each commanded waypoint as reached.

    Simulates a perfect actuator: whenever the executor emits a `control_input`,
    the next event is a `joint_positions` sitting exactly on that target, so the
    executor should settle and advance through the entire path.
    """

    def __init__(self, trajectory_flat):
        self._traj = trajectory_flat
        self._sent_traj = False
        self._answered = 0
        self.outputs: list[tuple[str, object]] = []

    def send_output(self, output_id, value):
        self.outputs.append((output_id, value))

    def _control_inputs(self):
        return [v for (i, v) in self.outputs if i == "control_input"]

    def __iter__(self):
        return self

    def __next__(self):
        if not self._sent_traj:
            self._sent_traj = True
            return {"type": "INPUT", "id": "trajectory", "value": _Val(self._traj)}
        ctrls = self._control_inputs()
        if self._answered < len(ctrls):
            target = ctrls[self._answered].to_pylist()
            self._answered += 1
            return {"type": "INPUT", "id": "joint_positions",
                    "value": _Val(FREE + list(target))}
        raise StopIteration


class ScriptedNode:
    """Yields a fixed event list and records outputs — no feedback."""

    def __init__(self, events):
        self._events = iter(events)
        self.outputs: list[tuple[str, object]] = []

    def send_output(self, output_id, value):
        self.outputs.append((output_id, value))

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._events)


def _install_node(monkeypatch, node):
    """Point the node module's `Node()` at our fake, so main() drives it."""
    monkeypatch.setattr("simulation.trajectory_executor_node.Node", lambda: node)


# --- pure helper ---------------------------------------------------------

def test_reshape_trajectory_row_major():
    flat = [float(i) for i in range(18)]
    wps = reshape_trajectory(flat)
    assert len(wps) == 3
    assert wps[0] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert wps[-1] == [12.0, 13.0, 14.0, 15.0, 16.0, 17.0]


def test_reshape_drops_trailing_partial_waypoint():
    assert len(reshape_trajectory([float(i) for i in range(18)] + [99.0])) == 3
    assert reshape_trajectory([]) == []


# --- the state machine ---------------------------------------------------

def test_executor_follows_the_whole_path_and_reports_once(monkeypatch):
    waypoints = [[0.1] * 6, [0.2] * 6, [0.3] * 6]
    flat = [v for wp in waypoints for v in wp]
    node = ClosedLoopNode(flat)
    _install_node(monkeypatch, node)

    main()

    commanded = [v.to_pylist() for (i, v) in node.outputs if i == "control_input"]
    assert commanded == waypoints, "arm must be driven through every waypoint in order"

    statuses = [_decode(v) for (i, v) in node.outputs if i == "execution_status"]
    assert len(statuses) == 1, "exactly one completion for one trajectory"
    assert statuses[0]["status"] == "completed"
    assert statuses[0]["waypoints"] == len(waypoints)
    assert statuses[0]["joint_positions"] == waypoints[-1]


def test_executor_does_not_advance_until_settled(monkeypatch):
    """Joint states far from the target must not advance the path or complete it."""
    waypoints = [[0.1] * 6, [0.9] * 6]
    flat = [v for wp in waypoints for v in wp]
    # Arm sits nowhere near waypoint 0 across several readings.
    far = FREE + [5.0] * 6
    events = [
        {"type": "INPUT", "id": "trajectory", "value": _Val(flat)},
        {"type": "INPUT", "id": "joint_positions", "value": _Val(far)},
        {"type": "INPUT", "id": "joint_positions", "value": _Val(far)},
        {"type": "STOP"},
    ]
    node = ScriptedNode(events)
    _install_node(monkeypatch, node)

    main()

    commanded = [v.to_pylist() for (i, v) in node.outputs if i == "control_input"]
    assert commanded == [waypoints[0]], "must hold waypoint 0 until it is reached"
    assert not any(i == "execution_status" for (i, _) in node.outputs)
