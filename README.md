Fractal
=======

Fractal is an interactive coding-agent CLI built around a Recursive Language
Model. Each user turn is one RLM call over a `Workspace` input mounted at
`/sandbox/workspace`; predict-rlm is responsible for syncing workspace edits
back after code blocks.

Development
-----------

This project depends on a local editable checkout of predict-rlm:

```bash
uv run fractal --help
uv run pytest
```

The RLM-facing imports require `predict_rlm.Workspace`, expected from the local
`/Users/emile/git/predict-rlm` checkout on `feat/workspace-input`.
