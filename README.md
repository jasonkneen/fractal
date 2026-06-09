Fractal
=======

Fractal is an interactive coding-agent CLI built around a Recursive Language
Model. Each user turn is one RLM call over a direct `Workspace` input mounted
into a Docker Sandbox through predict-rlm's SBX backend, so Python subprocesses
and project commands operate on the real workspace path.

Usage
-----

```bash
fractal                       # interactive session in the current directory
fractal -p "fix the tests"    # one non-interactive turn
fractal --resume <session-id> # resume a stored workspace session
```

Interactive slash commands: `/help`, `/sessions`, `/resume <id>`, `/new`,
`/usage`, `/verbose`, `/exit`.

After each turn Fractal shows host-recorded facts: iterations, wall time,
tokens in/out, the current RLM context size, billed cost, and changed files.
Because the RLM loop re-summarizes between turns, "context" is the prompt size
of the latest main-LM call rather than a cumulative count. `/usage` reports
session totals, which persist in `.fractal/sessions/<session-id>.json` and
survive `--resume`.

Development
-----------

This project depends on a local editable checkout of predict-rlm:

```bash
uv run fractal --help
uv run pytest
```

The RLM-facing imports require `predict_rlm.WorkspaceMode` and direct workspace
support from the local `/Users/emile/git/predict-rlm` checkout.
