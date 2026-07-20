---
name: ur5e-arm
description: How to move the UR5e arm and Robotiq 2F-85 gripper in this scene
version: 1.0.0
author: 23garyd
always: true
---

# UR5e arm + Robotiq 2F-85 gripper

The arm has **6 revolute joints** in this order (radians):
`[shoulder_pan, shoulder_lift, elbow, wrist_1, wrist_2, wrist_3]`.

## Named poses

Prefer these FK-verified named poses for `goal` (you may pass the name directly):

| Name | Meaning |
|------|---------|
| `home` | Default rest configuration |
| `upright` | Arm straight up |
| `zero` | All joints at 0 |
| `above_ball` | Just above the red ball |
| `grasp_ball` | Down at the red ball (grasp height) |
| `lift` | Lifted after grasping |
| `above_plate` | Above the green plate |
| `place_plate` | Down at the green plate (place height) |

## Scene

Red ball at world `(0.35, -0.25, 0.028)`; green plate at `(0.35, 0.25, 0.001)`.

## Motion rules

1. **Read first.** Call `dora_read` on `joint_positions` before planning so you
   know the current configuration.
2. **Plan with `dora_call` on `plan_request`.** Provide `goal` (a named pose or 6
   floats). `start` defaults to the current joint_positions if you omit it. Wait
   for `plan_status` before the next motion.
3. **Grasp sequence:** `above_ball` → `grasp_ball` → close gripper → `lift`.
4. **Place sequence:** `above_plate` → `place_plate` → open gripper → `home`.

## Gripper

Use `dora_call` on `gripper_command` with `{"action": "open"}` or
`{"action": "close"}`, waiting on `gripper_status`. Open before grasping; close
to grasp; open to release.
