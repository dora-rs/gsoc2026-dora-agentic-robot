---
name: ur5e-arm
description: How to move the UR5e arm and Robotiq 2F-85 gripper in this scene
version: 1.1.0
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

1. **Perceive first.** Call `dora_perceive` before planning so you know the
   current configuration, the gripper state, and where the scene objects are.
2. **Move with `dora_move`.** Pass `target` as a named pose or 6 floats; the
   start configuration is filled in for you. It returns only once the motion has
   completed, so one call is one motion — never overlap two.
3. **Grasp sequence:** `above_ball` → `grasp_ball` → close gripper → `lift`.
4. **Place sequence:** `above_plate` → `place_plate` → open gripper → `home`.

## Gripper

Use `dora_gripper` with `{"action": "open"}` or `{"action": "close"}`; it waits
for `gripper_status`. Open before grasping; close to grasp; open to release.

## Multi-step tasks

For anything beyond a single motion — picking up an object, moving it somewhere —
call `skill` with `action: "list"` and activate the matching skill first. The
procedure it returns is authoritative; follow it step by step.
