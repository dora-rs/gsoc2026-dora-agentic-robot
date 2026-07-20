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
    are included (the rest are opt-in, to be activated on demand later).
    """
    sections = [
        f"## Skill: {skill.name}\n\n{skill.content}"
        for skill in skills
        if not (always_only and not skill.always)
    ]
    return "\n\n---\n\n".join(sections)
