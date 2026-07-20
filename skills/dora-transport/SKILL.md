---
name: dora-transport
description: How to use the dora transport tools to read state and send commands
version: 1.0.0
author: 23garyd
always: true
---

# Dora transport tools

You drive the robot through three tools that bridge to the dora dataflow.

## `dora_read`

Read the latest cached value of an input. Returns `{"data": …, "age_ms": …}` or
`{"error": …}`. Readable inputs:
`joint_positions`, `plan_status`, `execution_status`, `scene_state`,
`gripper_status`.

## `dora_send`

Fire-and-forget a JSON command on an output; does **not** wait. Use for
`scene_command` (adding/attaching collision objects). Returns
`{"success": true, "sent_to": …}`.

## `dora_call`

Send a command and **wait** for its response — use whenever a motion or action
must complete before you continue:

- `plan_request` → waits for `plan_status`. Data: `{"start": [6], "goal": [6]}`.
  A named pose is accepted for `goal`; `start` auto-fills from current
  `joint_positions` if omitted.
- `gripper_command` → waits for `gripper_status`. Data: `{"action": "open"|"close"}`.

## Discipline

- One motion at a time: never issue a second `plan_request` before the previous
  `plan_status` returns.
- If a tool returns `{"error": …}`, read the relevant status input, then retry or
  adjust — do not repeat the identical failing call.
