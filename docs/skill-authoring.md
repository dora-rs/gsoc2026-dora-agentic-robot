# Skill authoring guide

A **skill** is procedural knowledge for the agent, written as a `SKILL.md` file —
Markdown with a YAML frontmatter header. Skills teach the model *how* to do
multi-step tasks (a pick-and-place recipe, a robot's conventions) without hard-
coding anything. They're how you adapt the agent to a new robot or task with no
code.

## Where skills live

One directory per skill under the skills root (`OCTOS_SKILLS_DIR`, default
`./skills`), each containing a `SKILL.md`:

```
skills/
  pick-and-place/SKILL.md
  ur5e-arm/SKILL.md
  dora-transport/SKILL.md
```

`load_skills(dir)` reads them all; the `SkillRegistry` serves them to the agent.

## Anatomy of a SKILL.md

```markdown
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
1. **Perceive.** `dora_perceive` — confirm the arm's configuration ...
2. **Open the gripper.** `dora_gripper` with `action: "open"` ...
...

## Rules
- **One motion at a time.** `dora_move` only returns once the motion has finished.
- **Never skip the approach poses.** ...
```

### Frontmatter fields

| Field | Meaning |
|-------|---------|
| `name` | Unique identifier; the `skill` tool activates by this name. |
| `description` | One line. Shown in the dormant-skill catalogue so the model can pick the right one. |
| `version` | Semver string, for your own tracking. |
| `author` | Freeform. |
| `always` | `true` → injected into the system prompt every run. `false` → **dormant**: advertised by name + description, loaded in full only when the agent calls `skill(activate, <name>)`. |

### Body

Free Markdown. Structure it as a numbered **Procedure** and a **Rules** section —
the model follows it literally. Reference tools by their real names (`dora_move`,
`dora_gripper`, …) and named poses/objects the robot actually has.

## `always: true` vs dormant

- **`always: true`** — for short, universal conventions the model should never be
  without (e.g. "this arm's joint order is …", safety rules). Kept in the prompt
  every turn, so keep them small — they cost context on every call.
- **`always: false` (dormant)** — for task recipes. The prompt lists only their
  name + description under *"Skill procedures available on demand"*; the full body
  loads only when activated. This keeps the prompt lean while still letting the
  model discover and pull in the right procedure. The pick-and-place skill is
  dormant; the model activates it on the first turn of a pick-and-place mission.

## Writing a skill for a new robot

1. **Name the tools and poses it will use.** A skill is only as good as the
   vocabulary it references — make sure the robot's `NAMED_POSES` and tools exist
   (see [extending.md](extending.md) for adding a robot).
2. **Write the procedure as ordered steps**, each naming one tool call. Prefer the
   semantic tools (`dora_move`/`dora_gripper`) over transport.
3. **Add the non-obvious rules** — the ordering constraints and failure-recovery
   habits a model won't infer (e.g. "open the gripper *before* descending").
4. **Keep task recipes dormant** (`always: false`); reserve `always: true` for
   the few universal conventions.
5. **Test it** without an API key: `agent/pick_place_demo.py` drives a scripted
   provider through a skill and checks the outcome — copy that pattern.

## How the agent sees a skill

- On startup, `compose_system_prompt(base_prompt, skills)` injects the
  `always: true` skills in full and lists the dormant ones by name + description.
- Mid-mission, `skill(list)` returns the catalogue and `skill(activate, name)`
  returns `{"name", "version", "content"}` — the full body enters the
  conversation and the model follows it.

See the shipped `skills/pick-and-place/SKILL.md` for a complete, working example,
and [week6-agent-bridge.md](week6-agent-bridge.md) / [week7-motion-tools.md](week7-motion-tools.md)
for the design story.
