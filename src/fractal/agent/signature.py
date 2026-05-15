from __future__ import annotations

import dspy
from predict_rlm import Workspace


class EditWorkspace(dspy.Signature):
    """Act as a focused coding agent over the mounted workspace.

    You receive a Workspace mounted at /sandbox/workspace, the user's current
    request, and concise context from prior turns. Inspect and edit files only
    through Python code in /sandbox/workspace. Prefer pathlib/os operations
    rooted at /sandbox/workspace, and prefer os.open with dir_fd/root_fd,
    os.pread/os.pwrite/os.ftruncate, and temp-file plus os.replace patterns
    when they make edits safer.

    Keep changes focused on the user request. Inspect files before modifying
    them, preserve unrelated content, and verify important edits. Return only a concise user-facing response
    and a list of relative changed file paths.
    """

    workspace: Workspace = dspy.InputField(
        desc="Project workspace mounted at /sandbox/workspace and synced after code blocks."
    )
    user_message: str = dspy.InputField(
        desc="The user's current request for this turn."
    )
    session_summary: str = dspy.InputField(
        default="",
        desc="Concise context from prior Fractal turns in this workspace.",
    )

    response: str = dspy.OutputField(desc="Concise response to show in the CLI.")
    changed_files: list[str] = dspy.OutputField(
        desc="Relative paths of files changed in /sandbox/workspace."
    )
