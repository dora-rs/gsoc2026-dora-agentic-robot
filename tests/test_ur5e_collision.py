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
    obstacle_from_spec,
    self_collision,
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


# --- self-collision (Week 12) --------------------------------------------

def test_named_poses_have_no_self_collision():
    """Adjacency masking must not false-positive on any legitimate pose."""
    for name, q in NAMED_POSES.items():
        assert not self_collision(q), f"{name} wrongly flagged as self-collision"


def test_a_folded_arm_is_a_self_collision():
    """A configuration that doubles the wrist back onto the shoulder is caught."""
    assert self_collision([0.0, -2.9, 2.9, 0.0, 0.0, 0.0])
    assert self_collision([0.0, -3.0, 3.0, -3.0, 0.0, 0.0])


def test_config_in_collision_includes_self_by_default():
    folded = [0.0, -2.9, 2.9, 0.0, 0.0, 0.0]
    assert config_in_collision(folded)                       # self-collision counted
    assert not config_in_collision(folded, check_self=False)  # ...unless disabled


# --- obstacle specs (Week 12) --------------------------------------------

def test_obstacle_from_spec_dispatches_on_type():
    box = obstacle_from_spec({"name": "b", "type": "box", "position": [0.3, 0, 0.3],
                              "half_extents": [0.05, 0.05, 0.05]})
    assert box["type"] == "box" and box["half_extents"] == [0.05, 0.05, 0.05]
    # `dimensions` is accepted as full sizes and halved
    box2 = obstacle_from_spec({"name": "b", "type": "box", "position": [0, 0, 0],
                               "dimensions": [0.1, 0.1, 0.1]})
    assert box2["half_extents"] == [0.05, 0.05, 0.05]
    sph = obstacle_from_spec({"name": "s", "type": "sphere", "position": [0, 0, 0],
                              "radius": 0.05})
    assert sph["radius"] == 0.05
    cyl = obstacle_from_spec({"name": "c", "type": "cylinder", "position": [0, 0, 0],
                              "radius": 0.05, "height": 0.5})
    assert cyl["height"] == 0.5


def test_obstacle_from_spec_rejects_bad_input():
    import pytest
    with pytest.raises(ValueError):
        obstacle_from_spec({"name": "x", "type": "blob", "position": [0, 0, 0]})
    with pytest.raises(ValueError):
        obstacle_from_spec({"name": "x", "type": "box", "position": [0, 0]})
