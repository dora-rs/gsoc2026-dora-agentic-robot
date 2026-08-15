"""UR5e collision checking (Week 11) — is a joint configuration collision-free?

Weeks 8-10 planned *paths* but never checked them for collision — dora-moveit2's
own checker is stubbed (`is_state_valid` returns True). This module gives the
planner a real answer: for a joint configuration it places a coarse capsule model
of the arm in the world (via the MuJoCo-exact `ur5e_kinematics` FK) and tests it
against the table and any workspace obstacles.

It is self-contained — the sphere/box/cylinder/ground primitives are implemented
here, so collision checking has no dora-moveit2 dependency and is unit-testable
anywhere. Obstacles are plain dicts (`make_box`/`make_sphere`/`make_cylinder`), so
a scene can be described in a dataflow message later.

The arm is modelled as spheres swept along the link segments (shoulder → wrist_3)
at each link's radius — coarse but conservative: it over- rather than
under-approximates the arm, so a path it calls clear really is clear. The fixed
base is excluded from the ground test (it rests on the table by design). Self-
collision is intentionally out of scope here (a coarse sphere model produces false
positives between adjacent links); the table + workspace obstacles are what the
pick-and-place actually needs to avoid.
"""

from __future__ import annotations

import numpy as np

from simulation.ur5e_kinematics import link_positions

# Coarse collision radius per arm link (metres) — envelops the UR5e link.
LINK_RADII = {
    "shoulder_link": 0.06,
    "upper_arm_link": 0.06,
    "forearm_link": 0.05,
    "wrist_1_link": 0.045,
    "wrist_2_link": 0.045,
    "wrist_3_link": 0.045,
}
# Kinematic order of the movers whose segments form the arm's body.
_CHAIN = ["shoulder_link", "upper_arm_link", "forearm_link",
          "wrist_1_link", "wrist_2_link", "wrist_3_link"]

# Table surface height in the world frame (objects rest at z ~ 0).
GROUND_Z = 0.0


# --- obstacle constructors (plain dicts, message-friendly) ---------------

def make_box(name: str, position, half_extents) -> dict:
    return {"name": name, "type": "box",
            "position": [float(v) for v in position],
            "half_extents": [float(v) for v in half_extents]}


def make_sphere(name: str, position, radius: float) -> dict:
    return {"name": name, "type": "sphere",
            "position": [float(v) for v in position], "radius": float(radius)}


def make_cylinder(name: str, position, radius: float, height: float) -> dict:
    """A vertical cylinder whose base sits at `position`."""
    return {"name": name, "type": "cylinder",
            "position": [float(v) for v in position],
            "radius": float(radius), "height": float(height)}


# --- primitive sphere tests ----------------------------------------------

def _sphere_box_hit(c, r, box_pos, half, margin) -> bool:
    box_pos = np.asarray(box_pos); half = np.asarray(half)
    closest = np.clip(c, box_pos - half, box_pos + half)
    return float(np.linalg.norm(c - closest)) < r + margin


def _sphere_sphere_hit(c, r, pos, rr, margin) -> bool:
    return float(np.linalg.norm(c - np.asarray(pos))) < r + rr + margin


def _sphere_cylinder_hit(c, r, pos, cr, ch, margin) -> bool:
    pos = np.asarray(pos)
    z_clamped = np.clip(c[2], pos[2], pos[2] + ch)
    xy = c[:2] - pos[:2]
    xy_dist = float(np.linalg.norm(xy))
    closest_xy = pos[:2] + xy / xy_dist * min(xy_dist, cr) if xy_dist > 1e-6 else pos[:2]
    closest = np.array([closest_xy[0], closest_xy[1], z_clamped])
    return float(np.linalg.norm(c - closest)) < r + margin


def _sphere_hits_obstacle(c, r, obs, margin) -> bool:
    kind = obs["type"]
    if kind == "box":
        return _sphere_box_hit(c, r, obs["position"], obs["half_extents"], margin)
    if kind == "sphere":
        return _sphere_sphere_hit(c, r, obs["position"], obs["radius"], margin)
    if kind == "cylinder":
        return _sphere_cylinder_hit(c, r, obs["position"], obs["radius"],
                                    obs["height"], margin)
    return False


# --- arm model + state validity ------------------------------------------

def arm_spheres(joint_angles, samples_per_segment: int = 4):
    """Spheres swept along the arm's link segments: list of (center, radius)."""
    fk = link_positions(joint_angles)
    spheres: list[tuple[np.ndarray, float]] = []
    for a, b in zip(_CHAIN[:-1], _CHAIN[1:]):
        pa, pb = fk[a], fk[b]
        radius = max(LINK_RADII[a], LINK_RADII[b])
        for t in np.linspace(0.0, 1.0, samples_per_segment):
            spheres.append((pa * (1.0 - t) + pb * t, radius))
    return spheres


def config_in_collision(joint_angles, obstacles=None, ground_z: float = GROUND_Z,
                        margin: float = 0.0) -> bool:
    """True if the arm at this configuration hits the table or any obstacle."""
    obstacles = obstacles or []
    for center, radius in arm_spheres(joint_angles):
        if center[2] - radius < ground_z - 1e-9:      # dipped below the table
            return True
        for obs in obstacles:
            if _sphere_hits_obstacle(center, radius, obs, margin):
                return True
    return False
