from __future__ import annotations

from pathlib import Path

import pytest

from fractal.agent.skill_loader import (
    DEFAULT_PREDICT_RLM_SKILLS,
    SkillManifest,
    build_catalog,
    build_turn_skills,
    discover_skill_manifests,
    make_load_skill_tool,
    parse_skill_md,
    skill_search_roots,
)


def _write_skill(root: Path, name: str, body: str, *, frontmatter: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    front = frontmatter if frontmatter is not None else f"name: {name}\ndescription: {body}"
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")
    return skill_file


def test_parse_skill_md_reads_frontmatter_and_body() -> None:
    text = (
        "---\n"
        "name: data-cleanup\n"
        "description: Clean messy CSVs and dedupe rows.\n"
        "packages: [pandas, \"rapidfuzz\"]\n"
        "---\n"
        "# Cleanup\n"
        "Follow these steps.\n"
    )

    manifest = parse_skill_md(text, source=Path("skills/data-cleanup/SKILL.md"))

    assert manifest is not None
    assert manifest.name == "data-cleanup"
    assert manifest.description == "Clean messy CSVs and dedupe rows."
    assert manifest.packages == ("pandas", "rapidfuzz")
    assert manifest.instructions.startswith("# Cleanup")


def test_parse_skill_md_supports_block_list_packages() -> None:
    text = (
        "---\n"
        "name: blk\n"
        "description: d\n"
        "packages:\n"
        "  - alpha\n"
        "  - beta\n"
        "---\n"
        "body\n"
    )

    manifest = parse_skill_md(text, source=Path("blk/SKILL.md"))

    assert manifest is not None
    assert manifest.packages == ("alpha", "beta")


def test_parse_skill_md_requires_name() -> None:
    assert parse_skill_md("no frontmatter at all", source=Path("x")) is None
    assert parse_skill_md("---\ndescription: x\n---\nbody", source=Path("x")) is None


def test_parse_skill_md_truncates_long_instructions() -> None:
    body = "x" * 25_000
    text = f"---\nname: big\ndescription: d\n---\n{body}\n"

    manifest = parse_skill_md(text, source=Path("big/SKILL.md"))

    assert manifest is not None
    assert "truncated" in manifest.instructions
    assert len(manifest.instructions) < len(body)


def test_discover_skill_manifests_project_overrides_user(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    home = tmp_path / "home"
    project_root = workspace / ".fractal" / "skills"
    user_root = home / ".fractal" / "skills"
    _write_skill(project_root, "shared", "project version")
    _write_skill(user_root, "shared", "user version")
    _write_skill(user_root, "user-only", "user only skill")

    manifests = discover_skill_manifests(
        workspace, home=home, bundled_dir=tmp_path / "nonexistent"
    )

    by_name = {manifest.name: manifest for manifest in manifests}
    assert set(by_name) == {"shared", "user-only"}
    # Project precedence wins for the shared name.
    assert by_name["shared"].description == "project version"
    # Stable, sorted ordering.
    assert [m.name for m in manifests] == ["shared", "user-only"]


def test_discover_skill_manifests_ignores_dirs_without_skill_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project_root = workspace / ".fractal" / "skills"
    (project_root / "not-a-skill").mkdir(parents=True)
    (project_root / "not-a-skill" / "README.md").write_text("nope", encoding="utf-8")
    _write_skill(project_root, "real", "real skill")

    manifests = discover_skill_manifests(
        workspace, home=tmp_path / "home", bundled_dir=tmp_path / "none"
    )

    assert [m.name for m in manifests] == ["real"]


def test_discover_skill_manifests_extra_dirs_take_precedence(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project_root = workspace / ".fractal" / "skills"
    extra_root = tmp_path / "extra"
    _write_skill(project_root, "shared", "project version")
    _write_skill(extra_root, "shared", "extra version")

    manifests = discover_skill_manifests(
        workspace,
        home=tmp_path / "home",
        bundled_dir=tmp_path / "none",
        extra_dirs=[extra_root],
    )

    by_name = {manifest.name: manifest for manifest in manifests}
    assert by_name["shared"].description == "extra version"


def test_skill_search_roots_precedence_order(tmp_path: Path) -> None:
    roots = skill_search_roots(
        tmp_path / "ws",
        home=tmp_path / "home",
        bundled_dir=tmp_path / "bundled",
        extra_dirs=[tmp_path / "extra"],
    )

    assert roots == [
        tmp_path / "extra",
        tmp_path / "ws" / ".fractal" / "skills",
        tmp_path / "home" / ".fractal" / "skills",
        tmp_path / "bundled",
    ]


def test_build_catalog_lists_names_and_descriptions() -> None:
    manifests = [
        SkillManifest("alpha", "first skill", "body", (), Path("a")),
        SkillManifest("beta", "", "body", (), Path("b")),
    ]

    catalog = build_catalog(manifests)

    assert "- **alpha**: first skill" in catalog
    assert "- **beta**: (no description provided)" in catalog


def test_make_load_skill_tool_returns_instructions_and_packages() -> None:
    manifest = SkillManifest(
        name="data-cleanup",
        description="Clean CSVs.",
        instructions="Do the cleanup.",
        packages=("pandas",),
        source=Path("data-cleanup/SKILL.md"),
    )
    load_skill = make_load_skill_tool([manifest])

    loaded = load_skill("data-cleanup")

    assert "Do the cleanup." in loaded
    assert "pandas" in loaded
    assert "data-cleanup" in (load_skill.__doc__ or "")


def test_make_load_skill_tool_reports_unknown_skill() -> None:
    manifest = SkillManifest("known", "d", "i", (), Path("k"))
    load_skill = make_load_skill_tool([manifest])

    result = load_skill("missing")

    assert "No skill named 'missing'" in result
    assert "known" in result


def test_build_turn_skills_base_only_without_builtins_or_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate from any real ~/.fractal/skills so the result is deterministic
    # even where predict_rlm is unavailable for meta-skill construction.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    skills = build_turn_skills(
        workspace, base_skills=["BASE"], include_builtin_skills=False
    )

    assert skills == ["BASE"]


def test_build_turn_skills_appends_meta_skill_for_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip(
        "predict_rlm",
        reason="meta-skill construction needs predict_rlm.Skill",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "ws"
    _write_skill(workspace / ".fractal" / "skills", "demo", "demo skill")

    skills = build_turn_skills(
        workspace, base_skills=["BASE"], include_builtin_skills=False
    )

    assert skills[0] == "BASE"
    assert skills[-1].name == "skills"
    assert "demo" in skills[-1].instructions


def test_default_predict_rlm_skill_names_constant() -> None:
    # The advertised built-ins should stay aligned with predict-rlm's bundle.
    assert DEFAULT_PREDICT_RLM_SKILLS == ("pdf", "spreadsheet", "docx")
