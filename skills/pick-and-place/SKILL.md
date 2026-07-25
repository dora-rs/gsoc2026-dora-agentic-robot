---
name: pick-and-place
description: Full procedure for picking up the red ball and placing it on the green plate
version: 1.1.0
author: 23garyd
always: false
---

# Pick and place: red ball → green plate

Activate this skill before any mission that asks you to pick up, move, or place
an object. It assumes the `dora_move`, `dora_gripper`, and `dora_perceive` tools.

## Procedure

1. **Perceive.** `dora_perceive` — confirm the arm's current configuration and
   that the gripper state is known. Every later step assumes you started here.
2. **Open the gripper.** `dora_gripper` with `action: "open"`. Do this *before*
   approaching; opening on top of the ball will knock it away.
3. **Approach.** `dora_move` to `above_ball`. This is a clear pose directly over
   the ball, chosen so the descent in step 4 is a straight drop.
4. **Descend.** `dora_move` to `grasp_ball` — the fingers now straddle the ball.
5. **Grasp.** `dora_gripper` with `action: "close"`.
6. **Lift.** `dora_move` to `lift`. Lift before travelling, or the ball drags
   across the table.
7. **Traverse.** `dora_move` to `above_plate`.
8. **Descend.** `dora_move` to `place_plate`.
9. **Release.** `dora_gripper` with `action: "open"`.
10. **Retreat.** `dora_move` to `home`, clearing the plate.

## Rules

- **One motion at a time.** `dora_move` only returns once the motion has
  finished; never start the next step before it does.
- **Never skip the approach poses.** Going straight to `grasp_ball` or
  `place_plate` from an arbitrary configuration plans a path through the table.
- **Gripper before motion, not during.** Open/close while the arm is stationary.
- **Transient planning failures are handled for you.** `dora_move` replans a
  failed plan automatically before it returns — the planner is randomised, so a
  single failure is usually just an unlucky sample. Do not retry a `dora_move`
  that succeeded.
- **On a returned error** (a failure `dora_move` could *not* recover from), call
  `dora_perceive` to see where the arm actually is, then recover at the task
  level: approach from a different pose rather than repeating the identical call
  that just failed. A persistent failure means the goal is unreachable from here,
  not that it needs one more try.

## Reverse (place → pick)

To return the ball, run the same sequence with the plate poses first:
`above_plate` → `place_plate` → close → `lift` → `above_ball` → `grasp_ball` →
open → `home`.
