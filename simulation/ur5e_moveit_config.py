"""UR5e robot configuration for the dora-moveit2 planner (Week 10).

The dora-moveit2 OMPL/RRT-Connect planner (`planner_ompl_with_collision_op.py`)
is robot-agnostic: it reads its arm through `dora_moveit.config.load_config()`,
which imports the class named by the `ROBOT_CONFIG_MODULE` env var. Every prior
week drove the real MuJoCo arm through `sim_executor`, which is *direct joint
control* — it teleports the position target to the goal, no path search. This
config is what lets the real sampling-based planner plan for our specific arm
instead of the GEN72 it ships configured for.

It satisfies dora-moveit2's `RobotConfig` protocol for the 6-DOF UR5e:
joint limits taken from `models/ur5e_scene.xml` (elbow is the only limited joint,
+/-pi; the rest are +/-2pi), the FK-verified named poses from `named_poses.py`,
and a coarse per-link cylinder collision model. Set in a dataflow with:

    env:
      ROBOT_CONFIG_MODULE: "simulation.ur5e_moveit_config"

Only the path-search half of dora-moveit2 is exercised here (RRT-Connect over
the joint limits). Its collision checker is currently stubbed upstream
(`is_state_valid` returns True) and its FK is GEN72-shaped, so obstacle avoidance
is not yet active — enabling it with real UR5e FK is the Week 11 follow-up.
"""

from __future__ import annotations

import numpy as np

from simulation.named_poses import NAMED_POSES, NUM_JOINTS as _NUM_JOINTS

# 2*pi for every joint except the elbow, which ur5e_scene.xml limits to +/-pi
# (class "size3_limited"). Matches the actuator ctrlrange in the model.
_2PI = 2.0 * np.pi
_LOWER = np.array([-_2PI, -_2PI, -np.pi, -_2PI, -_2PI, -_2PI])
_UPPER = np.array([_2PI, _2PI, np.pi, _2PI, _2PI, _2PI])
# UR5e datasheet joint speed limits (rad/s): base/shoulder/elbow 3.14, wrists 6.28.
_VEL = np.array([np.pi, np.pi, np.pi, _2PI, _2PI, _2PI])


class UR5eConfig:
    """RobotConfig implementation for the 6-DOF UR5e arm."""

    NUM_JOINTS = _NUM_JOINTS  # 6

    JOINT_LOWER_LIMITS = _LOWER
    JOINT_UPPER_LIMITS = _UPPER
    JOINT_VELOCITY_LIMITS = _VEL

    # The planner's SimpleFK is GEN72-shaped and only feeds the (currently
    # stubbed) collision checker, so LINK_TRANSFORMS stays empty here.
    LINK_TRANSFORMS: list[dict] = []

    # Coarse per-link capsule radii/lengths for the collision model. Robot links
    # are read as ("cylinder", (radius, length)) pairs by the planner's
    # _setup_robot(); values approximate the UR5e link envelope in metres.
    COLLISION_GEOMETRY: list[tuple] = [
        ("cylinder", (0.06, 0.15)),  # base
        ("cylinder", (0.06, 0.43)),  # upper arm
        ("cylinder", (0.05, 0.39)),  # forearm
        ("cylinder", (0.04, 0.10)),  # wrist 1
        ("cylinder", (0.04, 0.10)),  # wrist 2
        ("cylinder", (0.04, 0.08)),  # wrist 3
    ]

    COLLISION_MARGIN = 0.01

    HOME_CONFIG = np.array(NAMED_POSES["home"])
    SAFE_CONFIG = np.array(NAMED_POSES["upright"])

    NAMED_POSES = {name: np.array(q) for name, q in NAMED_POSES.items()}

    @staticmethod
    def get_joint_limits() -> tuple[np.ndarray, np.ndarray]:
        return _LOWER, _UPPER

    @staticmethod
    def is_config_valid(q: np.ndarray) -> bool:
        q = np.asarray(q, dtype=float)
        return q.shape[-1] == _NUM_JOINTS and bool(
            np.all(q >= _LOWER) and np.all(q <= _UPPER)
        )

    @staticmethod
    def clip_to_limits(q: np.ndarray) -> np.ndarray:
        return np.clip(np.asarray(q, dtype=float), _LOWER, _UPPER)
