"""Unit tests for the SKILL.md loader (Week 6) and on-demand activation (Week 7)."""

from pathlib import Path

import pytest

from agent.skills import (
    SkillInfo,
    SkillRegistry,
    load_skill,
    load_skills,
    parse_frontmatter,
    skills_to_prompt,
)

REPO_SKILLS = Path(__file__).resolve().parents[1] / "skills"

_SKILL = """---
name: demo
description: a demo skill
version: 1.2.3
author: tester
always: true
---

# Body

Do the thing.
"""


def test_parse_frontmatter_splits_meta_and_body():
    meta, body = parse_frontmatter(_SKILL)
    assert meta["name"] == "demo"
    assert meta["version"] == "1.2.3"
    assert meta["always"] is True
    assert body.strip().startswith("# Body")


def test_parse_frontmatter_without_fences_returns_whole_text():
    meta, body = parse_frontmatter("no frontmatter here")
    assert meta == {}
    assert body == "no frontmatter here"


def test_load_skill_from_file(tmp_path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    skill = load_skill(str(d / "SKILL.md"))
    assert skill.name == "demo"
    assert skill.always is True
    assert skill.content.startswith("# Body")


def test_load_skill_defaults_name_to_dir_when_missing(tmp_path):
    d = tmp_path / "fallback"
    d.mkdir()
    (d / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    skill = load_skill(str(d / "SKILL.md"))
    assert skill.name == "fallback"
    assert skill.always is False


def test_load_skills_reads_subdirs_sorted(tmp_path):
    for name in ("b-skill", "a-skill"):
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\nalways: true\n---\nbody", encoding="utf-8"
        )
    skills = load_skills(str(tmp_path))
    assert [s.name for s in skills] == ["a-skill", "b-skill"]


def test_load_skills_missing_dir_is_empty():
    assert load_skills("/no/such/dir") == []


def test_skills_to_prompt_concatenates_bodies(tmp_path):
    skills = [load_skill_from_text(tmp_path, "s1", "one", True),
              load_skill_from_text(tmp_path, "s2", "two", False)]
    prompt = skills_to_prompt(skills)
    assert "## Skill: s1" in prompt and "## Skill: s2" in prompt
    assert "one" in prompt and "two" in prompt


def test_skills_to_prompt_always_only_filters(tmp_path):
    skills = [load_skill_from_text(tmp_path, "s1", "one", True),
              load_skill_from_text(tmp_path, "s2", "two", False)]
    prompt = skills_to_prompt(skills, always_only=True)
    assert "s1" in prompt and "s2" not in prompt


def test_repo_ships_always_on_skills():
    skills = load_skills(str(REPO_SKILLS))
    by_name = {s.name: s for s in skills}
    assert {"ur5e-arm", "dora-transport"} <= set(by_name)
    assert by_name["ur5e-arm"].always
    assert by_name["dora-transport"].always


def test_pick_and_place_ships_dormant():
    """The procedure is opt-in — it must not bloat every system prompt."""
    by_name = {s.name: s for s in load_skills(str(REPO_SKILLS))}
    assert "pick-and-place" in by_name
    assert not by_name["pick-and-place"].always


# --- SkillRegistry: on-demand activation (Week 7) -------------------------

def _skill(name, always=False):
    return SkillInfo(name=name, description="", version="1.0.0", author="",
                     always=always, content=f"body of {name}", path="")


def test_always_skills_start_active_and_others_dormant():
    registry = SkillRegistry([_skill("core", always=True), _skill("extra")])
    assert [s.name for s in registry.active()] == ["core"]
    assert [s.name for s in registry.dormant()] == ["extra"]


def test_activate_moves_a_skill_from_dormant_to_active():
    registry = SkillRegistry([_skill("extra")])
    assert registry.activate("extra").content == "body of extra"
    assert registry.is_active("extra")
    assert registry.dormant() == []


def test_activate_is_idempotent():
    registry = SkillRegistry([_skill("extra")])
    registry.activate("extra")
    registry.activate("extra")
    assert [s.name for s in registry.active()] == ["extra"]


def test_activate_unknown_skill_lists_the_alternatives():
    registry = SkillRegistry([_skill("extra")])
    with pytest.raises(KeyError, match="extra"):
        registry.activate("missing")


def test_catalogue_reports_activation_state():
    registry = SkillRegistry([_skill("core", always=True), _skill("extra")])
    catalogue = {e["name"]: e["active"] for e in registry.catalogue()}
    assert catalogue == {"core": True, "extra": False}


def test_registry_loads_the_repo_skills():
    registry = SkillRegistry(load_skills(str(REPO_SKILLS)))
    assert [s.name for s in registry.dormant()] == ["pick-and-place"]


def load_skill_from_text(tmp_path, name, body, always):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nalways: {str(always).lower()}\n---\n{body}",
        encoding="utf-8",
    )
    return load_skill(str(d / "SKILL.md"))
