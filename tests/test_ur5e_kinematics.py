"""Week 11 — the UR5e forward kinematics matches MuJoCo exactly.

The collision checker trusts `ur5e_kinematics.link_positions` to say where the
arm is in space, so its correctness has to be pinned to ground truth. This loads
the real MuJoCo model and asserts every arm link's world position matches the
analytic FK to well under a millimetre, across fixed and random configurations.
Skips cleanly where MuJoCo isn't installed.
"""

import numpy as np
import pytest

from simulation.named_poses import NAMED_POSES
from simulation.ur5e_kinematics import link_positions

mujoco = pytest.importorskip("mujoco")

MODEL = "simulation/models/ur5e_scene.xml"
BODIES = ["base", "shoulder_link", "upper_arm_link", "forearm_link",
          "wrist_1_link", "wrist_2_link", "wrist_3_link"]


def _mujoco_positions(model, data, q):
    data.qpos[7:13] = q                       # arm sits after the ball's free joint
    mujoco.mj_kinematics(model, data)
    ids = {b: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b) for b in BODIES}
    return {b: np.array(data.xpos[ids[b]]) for b in BODIES}


def test_fk_matches_mujoco_across_configs():
    model = mujoco.MjModel.from_xml_path(MODEL)
    data = mujoco.MjData(model)

    rng = np.random.default_rng(0)
    configs = [list(q) for q in NAMED_POSES.values()]
    configs += [list(rng.uniform(-2.0, 2.0, 6)) for _ in range(25)]

    max_err = 0.0
    for q in configs:
        truth = _mujoco_positions(model, data, q)
        fk = link_positions(q)
        for b in BODIES:
            max_err = max(max_err, float(np.linalg.norm(fk[b] - truth[b])))

    assert max_err < 1e-6, f"FK diverged from MuJoCo by {max_err:.2e} m"


def test_above_ball_tip_is_over_the_ball():
    """A sanity check independent of MuJoCo: the wrist sits above the red ball."""
    from simulation.named_poses import SCENE_OBJECTS

    tip = link_positions(NAMED_POSES["above_ball"])["wrist_3_link"]
    ball = SCENE_OBJECTS["red_ball"]["position"]
    assert np.allclose(tip[:2], ball[:2], atol=1e-3)
    assert tip[2] > ball[2]
