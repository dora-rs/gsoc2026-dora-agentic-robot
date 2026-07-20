"""SKILL.md loader (Week 6) — mirrors `octos_agent::skills::SkillsLoader`.

A *skill* is a `SKILL.md` file: YAML-ish frontmatter (name/description/version/
author/always) followed by Markdown body. Skills are the agent's domain
knowledge — how to drive this particular robot and dataflow — injected into the
system prompt so the behaviour is authored in Markdown, not hard-coded in Python.

    skills/
      ur5e-arm/SKILL.md        (always: true  → always injected)
      dora-transport/SKILL.md  (always: true)

`load_skills(dir)` reads every `<subdir>/SKILL.md`; `skills_to_prompt(skills)`
concatenates their bodies into a prompt section. Frontmatter is parsed with a
tiny hand-rolled reader so the loader has no YAML dependency.

Week 7 adds `SkillRegistry`: `always: false` skills stay dormant (the agent sees
only their catalogue entry) until it activates them mid-mission via the `skill`
tool, keeping long procedures out of the prompt until they are needed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass
class SkillInfo:
    """One loaded SKILL.md: its metadata and Markdown body."""

    name: str
    description: str
    version: str
    author: str
    always: bool
    content: str
    path: str


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into (frontmatter dict, body).

    Frontmatter is the block between the leading `---` fences. Only flat
    `key: value` pairs are understood; `true`/`false` become booleans. Files
    without frontmatter return `({}, text)`.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    meta: dict = {}
    for line in match.group(1).strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if value.lower() in ("true", "false"):
            meta[key] = value.lower() == "true"
        else:
            meta[key] = value
    return meta, match.group(2)


def load_skill(path: str) -> SkillInfo:
    """Load a single SKILL.md file into a `SkillInfo`."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    meta, content = parse_frontmatter(text)
    return SkillInfo(
        name=str(meta.get("name") or os.path.basename(os.path.dirname(path))),
        description=str(meta.get("description", "")),
        version=str(meta.get("version", "0.0.0")),
        author=str(meta.get("author", "")),
        always=bool(meta.get("always", False)),
        content=content.strip(),
        path=path,
    )


def load_skills(skills_dir: str) -> list[SkillInfo]:
    """Load every `<subdir>/SKILL.md` under `skills_dir`, sorted by subdir name.

    A missing directory yields an empty list (skills are optional).
    """
    skills: list[SkillInfo] = []
    if not os.path.isdir(skills_dir):
        return skills

    for entry in sorted(os.listdir(skills_dir)):
        skill_path = os.path.join(skills_dir, entry, "SKILL.md")
        if os.path.isfile(skill_path):
            skills.append(load_skill(skill_path))
    return skills


def skills_to_prompt(skills: list[SkillInfo], always_only: bool = False) -> str:
    """Concatenate skill bodies into a system-prompt section.

    With `always_only=True`, only skills whose frontmatter has `always: true`
    are included; the rest are opt-in, activated on demand via `SkillRegistry`.
    """
    sections = [
        f"## Skill: {skill.name}\n\n{skill.content}"
        for skill in skills
        if not (always_only and not skill.always)
    ]
    return "\n\n---\n\n".join(sections)


class SkillRegistry:
    """On-demand skill activation (Week 7).

    `always: true` skills go into the system prompt up front (Week 6). Everything
    else stays *dormant*: the agent sees only a one-line catalogue entry, and
    pulls the full Markdown body into the conversation by activating the skill.
    That keeps long procedural skills (the pick-and-place recipe, say) out of the
    context window until the mission actually calls for them — the same idea as
    the `ToolRegistry`'s LRU lifecycle, applied to knowledge instead of tools.
    """

    def __init__(self, skills: list[SkillInfo] | None = None):
        self._skills: dict[str, SkillInfo] = {}
        self._active: list[str] = []  # activation order, for reproducible prompts
        for skill in skills or []:
            self.add(skill)

    def add(self, skill: SkillInfo) -> None:
        """Register a skill; `always: true` skills start out already active."""
        self._skills[skill.name] = skill
        if skill.always and skill.name not in self._active:
            self._active.append(skill.name)

    def names(self) -> list[str]:
        return list(self._skills.keys())

    def get(self, name: str) -> SkillInfo | None:
        return self._skills.get(name)

    def active(self) -> list[SkillInfo]:
        return [self._skills[n] for n in self._active]

    def dormant(self) -> list[SkillInfo]:
        return [s for n, s in self._skills.items() if n not in self._active]

    def is_active(self, name: str) -> bool:
        return name in self._active

    def activate(self, name: str) -> SkillInfo:
        """Activate a dormant skill and return it.

        Raises KeyError (listing what *is* available) for an unknown name so the
        tool layer can hand the model a recoverable error.
        """
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"unknown skill '{name}'. Available: {sorted(self._skills)}")
        if name not in self._active:
            self._active.append(name)
        return skill

    def catalogue(self) -> list[dict]:
        """One entry per skill — what the agent sees before activating anything."""
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "version": skill.version,
                "active": self.is_active(skill.name),
            }
            for skill in self._skills.values()
        ]
