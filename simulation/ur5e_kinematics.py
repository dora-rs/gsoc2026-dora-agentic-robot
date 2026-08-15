"""UR5e forward kinematics (Week 11) — link world positions from joint angles.

Collision checking needs to know *where the arm is in space* for a given
configuration, not just the joint values. This module computes that: given the 6
joint angles, it returns the world-frame origin of each arm link.

The fixed link transforms and joint axes below are the UR5e's own kinematic
parameters, read straight out of `models/ur5e_scene.xml` via MuJoCo
(`body_pos` / `body_quat` / `jnt_axis`), so the analytic chain here reproduces
MuJoCo's `mj_kinematics` exactly — `test_ur5e_kinematics` asserts that against the
live simulator to a fraction of a millimetre. Because the parameters are baked in,
FK runs with no MuJoCo dependency at planning time (thousands of validity checks
per plan), which is the point.

Frames: the base is at the world origin with a 180 deg rotation about Z
(quat 0,0,0,-1), matching the Menagerie mount; every position returned is in the
MuJoCo world frame, the same frame the scene objects (ball, plate) live in.
"""

from __future__ import annotations

import numpy as np

# Arm links in kinematic order, each with its fixed transform relative to its
# parent: (name, translation, quaternion (w, x, y, z), joint axis in body frame).
# Verbatim from the MuJoCo model — do not hand-edit; regenerate from body_pos/
# body_quat/jnt_axis if the model changes.
_SQRT_HALF = 0.7071067811865476
LINKS = [
    ("base",           (0.0, 0.0, 0.0),   (0.0, 0.0, 0.0, -1.0),               None),
    ("shoulder_link",  (0.0, 0.0, 0.163), (1.0, 0.0, 0.0, 0.0),                (0.0, 0.0, 1.0)),
    ("upper_arm_link", (0.0, 0.138, 0.0), (_SQRT_HALF, 0.0, _SQRT_HALF, 0.0),  (0.0, 1.0, 0.0)),
    ("forearm_link",   (0.0, -0.131, 0.425), (1.0, 0.0, 0.0, 0.0),             (0.0, 1.0, 0.0)),
    ("wrist_1_link",   (0.0, 0.0, 0.392), (_SQRT_HALF, 0.0, _SQRT_HALF, 0.0),  (0.0, 1.0, 0.0)),
    ("wrist_2_link",   (0.0, 0.127, 0.0), (1.0, 0.0, 0.0, 0.0),                (0.0, 0.0, 1.0)),
    ("wrist_3_link",   (0.0, 0.0, 0.1),   (1.0, 0.0, 0.0, 0.0),                (0.0, 1.0, 0.0)),
]

# The arm links that carry a joint (base is fixed), in order — the 6 DOF.
ARM_LINK_NAMES = [name for name, _, _, axis in LINKS if axis is not None]


def _quat_to_mat(q: tuple[float, float, float, float]) -> np.ndarray:
    """Rotation matrix from a (w, x, y, z) quaternion (MuJoCo convention)."""
    w, x, y, z = q
    n = np.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-12:
        return np.eye(3)
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w)],
        [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y)],
    ])


def _axis_angle_to_mat(axis: tuple[float, float, float], theta: float) -> np.ndarray:
    """Rotation matrix for rotating by `theta` about a unit `axis` (Rodrigues)."""
    ax = np.asarray(axis, dtype=float)
    ax = ax / (np.linalg.norm(ax) or 1.0)
    c, s = np.cos(theta), np.sin(theta)
    x, y, z = ax
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + s * k + (1 - c) * (k @ k)


def link_positions(joint_angles) -> dict[str, np.ndarray]:
    """World-frame origin of every arm link for the given 6 joint angles.

    Returns a dict {link_name: np.array([x, y, z])}, including the fixed `base`.
    """
    q = list(joint_angles)
    rot = np.eye(3)      # world orientation of the current frame
    pos = np.zeros(3)    # world position of the current frame origin
    out: dict[str, np.ndarray] = {}
    joint_i = 0

    for name, translation, quat, axis in LINKS:
        # Fixed transform to this link's frame (relative to the running frame).
        pos = pos + rot @ np.asarray(translation, dtype=float)
        rot = rot @ _quat_to_mat(quat)
        # Revolute joint (if any) rotates this frame about its axis.
        if axis is not None:
            rot = rot @ _axis_angle_to_mat(axis, q[joint_i])
            joint_i += 1
        out[name] = pos.copy()

    return out


def ee_position(joint_angles) -> np.ndarray:
    """World-frame position of the wrist_3 link origin (the arm's tip)."""
    return link_positions(joint_angles)["wrist_3_link"]
