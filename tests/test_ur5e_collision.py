"""Week 11 — the UR5e collision model (pure geometry, no dora/MuJoCo needed).

Covers the two things the planner relies on: the legitimate pick-and-place poses
are all collision-free (so real checking doesn't reject the mission), and a
configuration that drives the arm into the table or an obstacle is detected.
"""

import numpy as np

from simulation.named_poses import NAMED_POSES
from simulation.ur5e_collision import (
    arm_spheres,
    config_in_collision,
    make_box,
    make_cylinder,
    make_sphere,
)
from simulation.ur5e_kinematics import link_positions


def test_all_named_poses_are_collision_free_over_the_table():
    """Every pose the pick-and-place uses must pass, or real checking would break
    the mission it is meant to protect."""
    for name, q in NAMED_POSES.items():
        assert not config_in_collision(q), f"{name} wrongly flagged as in collision"


def test_a_box_on_a_link_is_detected():
    q = NAMED_POSES["above_ball"]
    here = link_positions(q)["wrist_3_link"]
    box = make_box("pillar", here, [0.08, 0.08, 0.08])
    assert config_in_collision(q, [box])
    # move the box well away and the same config is clear again
    far = make_box("pillar", here + np.array([1.0, 0.0, 0.0]), [0.08, 0.08, 0.08])
    assert not config_in_collision(q, [far])


def test_sphere_and_cylinder_obstacles_are_detected():
    q = NAMED_POSES["above_ball"]
    here = link_positions(q)["wrist_3_link"]
    assert config_in_collision(q, [make_sphere("ball", here, 0.1)])
    assert config_in_collision(q, [make_cylinder("post", [here[0], here[1], 0.0],
                                                 0.1, here[2] + 0.2)])


def test_dropping_below_the_table_is_a_collision():
    """A configuration whose arm dips beneath the table surface is rejected."""
    # 'zero' has the forearm nearly horizontal and low; push the ground up to it.
    q = NAMED_POSES["zero"]
    assert not config_in_collision(q, ground_z=0.0)
    assert config_in_collision(q, ground_z=0.1)   # raise the table into the arm


def test_arm_spheres_follow_the_kinematics():
    q = NAMED_POSES["home"]
    spheres = arm_spheres(q, samples_per_segment=4)
    assert len(spheres) == 5 * 4     # five segments, four samples each
    fk = link_positions(q)
    # the last sphere sits at the wrist_3 origin
    assert np.allclose(spheres[-1][0], fk["wrist_3_link"])
