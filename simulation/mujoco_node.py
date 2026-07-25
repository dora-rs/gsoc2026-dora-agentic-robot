"""MuJoCo simulation dora node — UR5e 6-DOF arm + Robotiq 2F-85 gripper.

Week 1 deliverable: a reproducible physics simulation exposed as a dora node.

Inputs:
  - tick           : timer, steps the simulation
  - control_input  : float array, arm joint position targets (actuators 0..5)
  - gripper_ctrl   : float array[1], gripper actuator command (actuator 6; 0=open .. 255=closed)

Outputs:
  - joint_positions  : qpos (float array)
  - joint_velocities : qvel (float array)
  - sensor_data      : sensordata (float array, if the model defines sensors)

Environment:
  - MODEL_NAME      : path to a MuJoCo .xml (default: models/ur5e_scene.xml next to this file)
  - MUJOCO_HEADLESS : "1"/"true" to run without the viewer (CI / no display)
  - SIM_SUBSTEPS    : physics steps per received event (default 1). Raise it to
                      advance sim time faster than the dora tick rate so a full
                      agent-driven motion sequence settles in bounded wall-clock
                      time on a headless run; the viewer path keeps 1 for smoothness.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
from dora import Node

DEFAULT_MODEL = Path(__file__).parent / "models" / "ur5e_scene.xml"
CMD_TIMEOUT_SECS = 0.2
GRIPPER_ACTUATOR_IDX = 6


class UR5eSimulator:
    """Thin wrapper around a MuJoCo model + data for the UR5e scene."""

    def __init__(self, model_path: str | None = None):
        import mujoco  # imported here so the module imports without mujoco installed

        self._mujoco = mujoco
        self.model_path = str(model_path or os.getenv("MODEL_NAME") or DEFAULT_MODEL)
        if not Path(self.model_path).exists():
            raise FileNotFoundError(
                f"MuJoCo model not found: {self.model_path}. "
                f"Did you run scripts/fetch_ur5e_assets.sh to fetch the meshes?"
            )

        self.model = mujoco.MjModel.from_xml_path(self.model_path)
        self.data = mujoco.MjData(self.model)
        self.num_actuators = self.model.nu
        self.last_cmd_time = 0.0
        self.target_arm: np.ndarray | None = None

        # Start from the 'home' keyframe if present, else a safe folded config.
        if self.model.nkey > 0:
            mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        else:
            mujoco.mj_resetData(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)

        print(
            f"[sim] loaded {self.model_path} | nq={self.model.nq} nv={self.model.nv} "
            f"nu={self.model.nu} nsensor={self.model.nsensor}"
        )

    def apply_arm_control(self, control: np.ndarray) -> None:
        if control is None or len(control) == 0 or self.num_actuators == 0:
            return
        n = min(len(control), min(self.num_actuators, GRIPPER_ACTUATOR_IDX))
        self.target_arm = np.asarray(control[:n], dtype=float).copy()
        self.last_cmd_time = time.time()

    def apply_gripper_control(self, value: np.ndarray) -> None:
        if len(value) == 0 or self.num_actuators <= GRIPPER_ACTUATOR_IDX:
            return
        self.data.ctrl[GRIPPER_ACTUATOR_IDX] = float(value[0])

    def step(self) -> None:
        # Position actuators hold the last command; only re-assert while a recent
        # command is active so the gripper/other actuators are not overwritten.
        if self.target_arm is not None and (time.time() - self.last_cmd_time) <= CMD_TIMEOUT_SECS:
            n = len(self.target_arm)
            self.data.ctrl[:n] = self.target_arm
        self._mujoco.mj_step(self.model, self.data)

    def state(self) -> dict:
        return {
            "time": float(self.data.time),
            "qpos": self.data.qpos.copy(),
            "qvel": self.data.qvel.copy(),
            "sensordata": (
                self.data.sensordata.copy() if self.model.nsensor > 0 else np.empty(0)
            ),
        }


def _publish(node: Node, sim: UR5eSimulator) -> None:
    s = sim.state()
    meta = {"timestamp": s["time"]}
    node.send_output("joint_positions", pa.array(s["qpos"]), {**meta, "encoding": "jointstate"})
    node.send_output("joint_velocities", pa.array(s["qvel"]), meta)
    if len(s["sensordata"]) > 0:
        node.send_output("sensor_data", pa.array(s["sensordata"]), meta)


def _run(node: Node, sim: UR5eSimulator, viewer=None, substeps: int = 1) -> None:
    for event in node:
        if event["type"] == "INPUT":
            if event["id"] == "control_input":
                sim.apply_arm_control(event["value"].to_numpy())
            elif event["id"] == "gripper_ctrl":
                sim.apply_gripper_control(event["value"].to_numpy())

            for _ in range(substeps):
                sim.step()
            if viewer is not None:
                viewer.sync()
            _publish(node, sim)


def main() -> None:
    node = Node()
    sim = UR5eSimulator()
    print("[sim] UR5e MuJoCo node started")

    try:
        substeps = max(1, int(os.getenv("SIM_SUBSTEPS", "1")))
    except ValueError:
        substeps = 1

    headless = os.getenv("MUJOCO_HEADLESS", "").lower() in ("1", "true", "yes")
    if headless:
        print(f"[sim] headless mode (no viewer), substeps={substeps}")
        _run(node, sim, substeps=substeps)
        return

    import mujoco.viewer

    try:
        with mujoco.viewer.launch_passive(sim.model, sim.data) as viewer:
            print("[sim] viewer launched")
            _run(node, sim, viewer)  # viewer path stays real-time (substeps=1)
    except RuntimeError as exc:
        # macOS requires mjpython for the interactive viewer; fall back gracefully.
        print(f"[sim] viewer unavailable ({exc}); falling back to headless")
        _run(node, sim, substeps=substeps)


if __name__ == "__main__":
    main()
