#!/usr/bin/env python3
"""Render the pick-and-place as a GIF from MuJoCo (Week 13 — demo polish).

Drives the real UR5e scene through the named-pose pick-and-place sequence with the
position actuators and the Robotiq gripper — the same motions the agent issues —
settling at each pose like the trajectory executor does, and captures offscreen
frames into an animated GIF. This is a *visual* of the deliverable: the arm grasps
the red ball and places it on the green plate under real physics. It steps the
simulator directly (no dora / no LLM) purely to render; the agent-driven
equivalent is `python -m agent.pick_place_demo` and the live dataflows.

    python scripts/render_demo.py                 # -> docs/assets/pick_and_place.gif

Needs MuJoCo, the fetched meshes (scripts/fetch_ur5e_assets.sh), and imageio.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from simulation.named_poses import NAMED_POSES  # noqa: E402

GRIPPER_ACTUATOR_IDX = 6
SETTLE_TOL = 0.02

# (pose, gripper 0=open..255=closed, kind). "move" settles to the pose; "grip"
# dwells in place while the gripper opens/closes and the contact resolves.
SEQUENCE = [
    ("home", 0, "move"), ("above_ball", 0, "move"), ("grasp_ball", 0, "move"),
    ("grasp_ball", 255, "grip"), ("lift", 255, "move"), ("above_plate", 255, "move"),
    ("place_plate", 255, "move"), ("place_plate", 0, "grip"),
    ("above_plate", 0, "move"), ("home", 0, "move"),
]


def render(out_path: str, width: int, height: int, fps: int,
           frames_per_phase: int) -> None:
    import imageio.v2 as imageio
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(REPO / "simulation/models/ur5e_scene.xml"))
    data = mujoco.MjData(model)
    if model.nkey > 0:
        mujoco.mj_resetDataKeyframe(model, data, 0)
    mujoco.mj_forward(model, data)

    renderer = mujoco.Renderer(model, height=height, width=width)
    cam = mujoco.MjvCamera()
    cam.azimuth, cam.elevation, cam.distance = 140.0, -25.0, 1.9
    cam.lookat[:] = [0.15, 0.0, 0.25]
    frames: list[np.ndarray] = []

    def capture():
        renderer.update_scene(data, cam)
        frames.append(renderer.render())

    def run_phase(target, grip, kind):
        tgt = np.array(target, dtype=float)
        # A "move" runs until the arm settles; a "grip" dwells a fixed while.
        max_steps = 2500 if kind == "move" else 900
        every = max(1, max_steps // frames_per_phase)
        for step in range(max_steps):
            data.ctrl[:6] = tgt
            data.ctrl[GRIPPER_ACTUATOR_IDX] = grip
            mujoco.mj_step(model, data)
            if step % every == 0:
                capture()
            if kind == "move" and np.abs(data.qpos[7:13] - tgt).max() < SETTLE_TOL:
                capture()
                break

    for pose_name, grip, kind in SEQUENCE:
        run_phase(NAMED_POSES[pose_name], grip, kind)

    ball_xy = data.qpos[:2]
    plate = np.array([0.35, 0.25])
    ok = float(np.linalg.norm(ball_xy - plate)) < 0.06

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    imageio.mimsave(out_path, frames, duration=1.0 / fps, loop=0)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"wrote {out_path} — {len(frames)} frames, {size_kb:.0f} KB, "
          f"ball {'ON' if ok else 'NOT ON'} plate ({[round(float(v), 3) for v in ball_xy]})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?",
                        default=str(REPO / "docs/assets/pick_and_place.gif"))
    parser.add_argument("--width", type=int, default=360)
    parser.add_argument("--height", type=int, default=270)
    parser.add_argument("--fps", type=int, default=18)
    parser.add_argument("--frames-per-phase", type=int, default=11)
    args = parser.parse_args()
    render(args.out, args.width, args.height, args.fps, args.frames_per_phase)


if __name__ == "__main__":
    main()
