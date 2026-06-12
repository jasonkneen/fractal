"""Standard agent skill support for Fractal.

Fractal exposes capabilities to the RLM through predict-rlm ``Skill`` bundles.
Two flavours coexist:

* **Default skills** that are mounted on every turn so their instructions and
  sandbox packages are ready from the first call — Fractal's own
  ``filesystem-coding`` skill plus predict-rlm's built-in ``pdf``,
  ``spreadsheet`` and ``docx`` skills.
* **Discovered skills** authored as ``SKILL.md`` files (the standard
  coding-agent layout: a folder per skill with YAML-ish frontmatter and a
  Markdown body). These are advertised to the model as a lightweight catalogue
  and their full instructions are pulled in **on demand** via a host-side
  ``load_skill`` tool, so the base prompt stays small no matter how many skills
  a workspace ships.

Everything in this module that parses or discovers skills is intentionally free
of any ``predict_rlm`` import so it stays unit-testable in environments where
the RLM runtime is not installed. The functions that actually build
``predict_rlm.Skill`` objects import it lazily.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# predict-rlm ships these built-in skills; Fractal mounts them by default so
# PDF, spreadsheet, and Word capabilities are available from the get-go. Names
# are kept here as a plain constant so the CLI can advertise them without
# importing predict_rlm (which may be unavailable in dev/test environments).
DEFAULT_PREDICT_RLM_SKILLS: tuple[str, ...] = ("pdf", "spreadsheet", "docx")

# Short, Fractal-side summaries used only for user-facing listings. The
# authoritative instructions live inside predict-rlm.
BUILTIN_SKILL_SUMMARIES: dict[str, str] = {
    "pdf": "Read and extract text, tables, and structure from PDF documents.",
    "spreadsheet": (
        "Read, write, and compute Excel/CSV spreadsheets "
        "(openpyxl, pandas, formulas)."
    ),
    "docx": "Read and write Microsoft Word .docx documents.",
}

SKILL_FILE_NAME = "SKILL.md"
# Skills live under <root>/skills, where <root> is the project's .fractal
# directory or the user's ~/.fractal directory.
SKILLS_SUBDIR = "skills"
PROJECT_CONFIG_DIR_NAME = ".fractal"
USER_CONFIG_DIR_NAME = ".fractal"

_MAX_SKILL_INSTRUCTIONS_CHARS = 20_000

SKILLS_META_INSTRUCTIONS = """\
# Available skills

Fractal has additional, optional skills available for this workspace. Each one
bundles task-specific instructions you can pull in when the current request
matches. They are listed here by name and description only; their full
instructions are loaded on demand to keep this prompt small.

{catalog}

To use a skill, call the host tool `load_skill("<name>")` from your Python REPL
to retrieve its full instructions, then follow them for the task. Only load a
skill when the task actually calls for it.
"""


@dataclass(frozen=True)
class SkillManifest:
    """A skill discovered from a ``SKILL.md`` file.

    ``instructions`` is the Markdown body loaded on demand; ``description`` is
    the short frontmatter blurb shown in the always-visible catalogue.
    """

    name: str
    description: str
    instructions: str
    packages: tuple[str, ...]
    source: Path


# --------------------------------------------------------------------------- #
# SKILL.md parsing
# --------------------------------------------------------------------------- #


def parse_skill_md(text: str, *, source: Path) -> SkillManifest | None:
    """Parse a ``SKILL.md`` document into a :class:`SkillManifest`.

    Expects optional YAML-style frontmatter delimited by ``---`` lines with at
    least a ``name``; ``description`` and ``packages`` are optional. The Markdown
    body after the frontmatter becomes the on-demand instructions. Returns
    ``None`` when there is no usable ``name``.
    """
    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        return None
    name = _as_scalar(frontmatter.get("name")).strip()
    if not name:
        return None
    description = _as_scalar(frontmatter.get("description")).strip()
    packages = _as_list(frontmatter.get("packages"))
    instructions = body.strip()
    if len(instructions) > _MAX_SKILL_INSTRUCTIONS_CHARS:
        instructions = (
            instructions[:_MAX_SKILL_INSTRUCTIONS_CHARS]
            + "\n\n[SKILL.md truncated — read the full file from disk if needed.]"
        )
    return SkillManifest(
        name=name,
        description=description,
        instructions=instructions,
        packages=tuple(packages),
        source=source,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, object] | None, str]:
    # Tolerate a leading UTF-8 BOM so editor-written files still parse.
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return None, text
    lines = stripped.splitlines()
    if lines[0].strip() != "---":
        return None, text
    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return None, text
    frontmatter = _parse_frontmatter_block(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :])
    return frontmatter, body


def _parse_frontmatter_block(lines: list[str]) -> dict[str, object]:
    """Parse the small YAML subset Fractal needs: scalars and string lists.

    Supports ``key: value`` scalars, inline ``key: [a, b]`` lists, and block
    lists written as ``key:`` followed by indented ``- item`` lines. Anything
    fancier is ignored rather than raising, so a malformed skill file degrades
    to "no usable metadata" instead of crashing a turn.
    """
    data: dict[str, object] = {}
    list_key: str | None = None
    list_values: list[str] = []

    def flush_list() -> None:
        nonlocal list_key, list_values
        if list_key is not None:
            data[list_key] = list_values
            list_key = None
            list_values = []

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if list_key is not None and line.lstrip().startswith("- "):
            list_values.append(_unquote(line.lstrip()[2:].strip()))
            continue
        flush_list()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            # A bare ``key:`` may introduce a block list on the next lines.
            list_key = key
            list_values = []
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            items = (
                [_unquote(part.strip()) for part in inner.split(",") if part.strip()]
                if inner
                else []
            )
            data[key] = items
        else:
            data[key] = _unquote(value)
    flush_list()
    return data


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _as_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def skill_search_roots(
    workspace_path: str | Path,
    *,
    home: str | Path | None = None,
    bundled_dir: str | Path | None = None,
    extra_dirs: Iterable[str | Path] | None = None,
) -> list[Path]:
    """Return skill directories in precedence order (highest first).

    Project skills override user skills, which override skills bundled with
    Fractal. ``extra_dirs`` are searched at the highest precedence so callers
    (and tests) can inject explicit locations.
    """
    roots: list[Path] = []
    for extra in extra_dirs or []:
        roots.append(Path(extra))
    roots.append(Path(workspace_path) / PROJECT_CONFIG_DIR_NAME / SKILLS_SUBDIR)
    home_path = Path(home).expanduser() if home is not None else Path.home()
    roots.append(home_path / USER_CONFIG_DIR_NAME / SKILLS_SUBDIR)
    if bundled_dir is not None:
        roots.append(Path(bundled_dir))
    else:
        default_bundled = Path(__file__).resolve().parent.parent / SKILLS_SUBDIR
        roots.append(default_bundled)
    return roots


def discover_skill_manifests(
    workspace_path: str | Path,
    *,
    home: str | Path | None = None,
    bundled_dir: str | Path | None = None,
    extra_dirs: Iterable[str | Path] | None = None,
) -> list[SkillManifest]:
    """Discover ``SKILL.md`` skills across all search roots.

    Each immediate subdirectory of a root that contains a ``SKILL.md`` is one
    skill. The first occurrence of a given skill name wins, so higher-precedence
    roots shadow lower ones. Results are sorted by name for stable presentation.
    """
    seen: dict[str, SkillManifest] = {}
    roots = skill_search_roots(
        workspace_path,
        home=home,
        bundled_dir=bundled_dir,
        extra_dirs=extra_dirs,
    )
    for root in roots:
        for manifest in _scan_root(root):
            seen.setdefault(manifest.name, manifest)
    return sorted(seen.values(), key=lambda manifest: manifest.name)


def _scan_root(root: Path) -> list[SkillManifest]:
    try:
        if not root.is_dir():
            return []
        children = sorted(root.iterdir())
    except OSError:
        return []
    manifests: list[SkillManifest] = []
    for child in children:
        skill_file = child / SKILL_FILE_NAME
        try:
            if not skill_file.is_file():
                continue
            text = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        manifest = parse_skill_md(text, source=skill_file)
        if manifest is not None:
            manifests.append(manifest)
    return manifests


# --------------------------------------------------------------------------- #
# Catalogue + on-demand loading
# --------------------------------------------------------------------------- #


def build_catalog(manifests: Sequence[SkillManifest]) -> str:
    """Render the always-visible catalogue of discovered skills."""
    lines: list[str] = []
    for manifest in manifests:
        description = manifest.description or "(no description provided)"
        lines.append(f"- **{manifest.name}**: {description}")
    return "\n".join(lines)


def make_load_skill_tool(
    manifests: Sequence[SkillManifest],
) -> Callable[[str], str]:
    """Build the host-side ``load_skill`` tool exposed to the RLM.

    predict-rlm runs skill tools on the host and bridges the call from the
    sandbox, so this closure can read the manifests captured here and return the
    full instructions for the requested skill on demand.
    """
    by_name = {manifest.name: manifest for manifest in manifests}
    available = ", ".join(sorted(by_name)) or "(none)"

    def load_skill(name: str) -> str:
        manifest = by_name.get(name.strip())
        if manifest is None:
            return (
                f"No skill named {name!r}. "
                f"Available skills: {available}."
            )
        sections = [manifest.instructions.strip()]
        if manifest.packages:
            sections.append(
                "Required packages — install them in the sandbox before use, "
                'e.g. `subprocess.run(["pip", "install", '
                f'{", ".join(repr(pkg) for pkg in manifest.packages)}])`.'
            )
        return "\n\n".join(section for section in sections if section)

    load_skill.__doc__ = (
        "Load the full instructions for one of Fractal's available skills.\n\n"
        "Call this when the current task matches a skill listed in the skills "
        "catalogue. Pass the skill name exactly as listed and follow the "
        "returned Markdown instructions for this task.\n\n"
        f"Available skills: {available}."
    )
    return load_skill


def build_skills_meta_skill(manifests: Sequence[SkillManifest]) -> Any | None:
    """Build the predict-rlm ``Skill`` that advertises discovered skills.

    Returns ``None`` when there are no discovered skills so callers can skip
    mounting an empty catalogue. Imports ``predict_rlm`` lazily so this module
    stays importable without the runtime installed.
    """
    if not manifests:
        return None
    from predict_rlm import Skill

    instructions = SKILLS_META_INSTRUCTIONS.format(catalog=build_catalog(manifests))
    return Skill(
        name="skills",
        instructions=instructions,
        tools={"load_skill": make_load_skill_tool(manifests)},
    )


def default_predict_rlm_skills() -> list[Any]:
    """Return predict-rlm's built-in pdf/spreadsheet/docx skills.

    Imports defensively: if the installed predict-rlm predates the bundled
    skills, this returns an empty list rather than failing a turn.
    """
    try:
        from predict_rlm.skills import docx, pdf, spreadsheet
    except Exception:
        return []
    return [pdf, spreadsheet, docx]


def build_turn_skills(
    workspace_path: str | Path,
    *,
    base_skills: Sequence[Any],
    included_paths: Iterable[str | Path] | None = None,
    include_builtin_skills: bool = True,
) -> list[Any]:
    """Assemble the full skill list passed to ``PredictRLM`` for one turn.

    Order: always-on ``base_skills`` (e.g. filesystem-coding), then predict-rlm
    built-ins, then a single on-demand catalogue skill for any discovered
    ``SKILL.md`` skills.
    """
    skills: list[Any] = list(base_skills)
    if include_builtin_skills:
        skills.extend(default_predict_rlm_skills())
    manifests = discover_skill_manifests(workspace_path, extra_dirs=included_paths)
    meta_skill = build_skills_meta_skill(manifests)
    if meta_skill is not None:
        skills.append(meta_skill)
    return skills
