# Skills

Fractal gives the RLM extra, task-specific capabilities through
[predict-rlm](https://github.com/Trampoline-AI/predict-rlm) `Skill` bundles. A
skill is a reusable bundle of prose instructions, sandbox packages, mountable
modules, and host-side tools. Fractal supports two kinds of skills, mirroring
how a standard coding agent exposes capabilities.

## Default skills (always on)

These are mounted on every turn so they are ready from the first call — packages
installed, instructions present, no action needed from the model:

- **filesystem-coding** — Fractal's own filesystem cheatsheet for editing the
  workspace safely.
- **pdf**, **spreadsheet**, **docx** — predict-rlm's built-in document skills,
  giving PDF extraction, Excel/CSV work (openpyxl, pandas, formulas), and Word
  document handling out of the box.

The built-ins are imported from `predict_rlm.skills`. If the installed
predict-rlm predates them, Fractal degrades gracefully and simply skips them.
Built-ins can be turned off per agent with
`FractalAgent(include_builtin_skills=False)`.

## Workspace skills (loaded on demand)

You can add your own skills as `SKILL.md` files, the standard coding-agent
layout — one folder per skill. Fractal discovers them, advertises a lightweight
catalogue (name + description only) to the model, and pulls a skill's full
instructions into context **on demand** via a host-side `load_skill("<name>")`
tool. This keeps the base prompt small no matter how many skills a workspace
ships.

### Locations

Skills are discovered from these roots, highest precedence first (a skill name
found in an earlier root shadows the same name in a later one):

1. Project: `<workspace>/.fractal/skills/<name>/SKILL.md`
2. User: `~/.fractal/skills/<name>/SKILL.md`
3. Bundled with Fractal: `src/fractal/skills/<name>/SKILL.md`

### `SKILL.md` format

```markdown
---
name: data-cleanup
description: Clean messy CSVs and dedupe near-duplicate rows.
packages: [pandas, rapidfuzz]   # optional
---

# Data cleanup

Step-by-step instructions the model should follow when this skill is loaded...
```

- `name` (required) — the identifier passed to `load_skill`.
- `description` (recommended) — one line shown in the always-visible catalogue;
  this is what the model uses to decide whether to load the skill.
- `packages` (optional) — PyPI packages the skill needs. They are listed back to
  the model when the skill is loaded so it can install them in the sandbox.

The Markdown body after the frontmatter is the full instruction set, returned by
`load_skill` only when the model asks for it.

## Listing available skills

In the interactive TUI, run `/skills` to see every skill available in the
current workspace — built-ins (always on) and discovered `SKILL.md` skills
(loaded on demand).
