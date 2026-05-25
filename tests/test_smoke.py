from __future__ import annotations

import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest


pytest.importorskip(
    "predict_rlm",
    reason="predict-rlm is required for Fractal RLM smoke tests",
)


def workspace_available() -> bool:
    import predict_rlm

    return hasattr(predict_rlm, "Workspace")


def test_cli_parser_defaults_to_cwd() -> None:
    from fractal.cli import build_parser

    args = build_parser().parse_args([])

    assert args.workspace == Path.cwd()
    assert args.include == []
    assert args.max_iterations == 30
    assert args.quiet is False
    assert args.resume is None


def test_cli_parser_accepts_repeated_include_paths(tmp_path: Path) -> None:
    from fractal.cli import build_parser

    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    args = build_parser().parse_args([
        "--include",
        str(first),
        "--include",
        str(second),
    ])

    assert args.include == [first.resolve(), second.resolve()]


def test_cli_parser_rejects_invalid_include_paths(tmp_path: Path) -> None:
    from fractal.cli import build_parser

    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    link_path = tmp_path / "link"
    link_path.symlink_to(tmp_path, target_is_directory=True)
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--include", str(tmp_path / "missing")])
    with pytest.raises(SystemExit):
        parser.parse_args(["--include", str(file_path)])
    with pytest.raises(SystemExit):
        parser.parse_args(["--include", str(link_path)])


def test_cli_parser_accepts_resume_session_id() -> None:
    from fractal.cli import build_parser

    args = build_parser().parse_args(["--resume", "session-123"])

    assert args.resume == "session-123"


def test_run_tui_shows_shutdown_status_and_closes_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import fractal.runtime as runtime_module
    import fractal.tui as tui_module
    import rich.console as rich_console
    from fractal.cli import run_tui

    events: list[str] = []

    class FakeStatus:
        def __enter__(self) -> None:
            events.append("status_enter")

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            events.append("status_exit")

    class FakeConsole:
        def print(self, message: str, *, style: str | None = None) -> None:
            events.append(f"print:{message}:{style}")

        def status(self, message: str, *, spinner: str) -> FakeStatus:
            events.append(f"status:{message}:{spinner}")
            return FakeStatus()

    class FakeRuntime:
        def prewarm(self) -> None:
            events.append("prewarm")

        def close(self) -> None:
            events.append("close")

    class FakeFractalRuntime:
        @classmethod
        def create(cls, **kwargs: object) -> FakeRuntime:
            events.append("create")
            return FakeRuntime()

    class FakeTerminalFractalApp:
        def __init__(self, runtime: FakeRuntime, *, console: FakeConsole) -> None:
            events.append("app")

        async def run(self) -> None:
            events.append("run")

    monkeypatch.setattr(rich_console, "Console", FakeConsole)
    monkeypatch.setattr(runtime_module, "FractalRuntime", FakeFractalRuntime)
    monkeypatch.setattr(tui_module, "TerminalFractalApp", FakeTerminalFractalApp)

    result = run_tui(
        SimpleNamespace(
            workspace=tmp_path,
            include=[],
            lm=None,
            sub_lm=None,
            max_iterations=1,
            debug=False,
            resume=None,
        )
    )

    assert result == 0
    assert events == [
        "create",
        "print:sandbox: starting:dim",
        "status:[dim]starting sandbox...[/dim]:dots",
        "status_enter",
        "prewarm",
        "status_exit",
        "print:sandbox: ready:dim",
        "app",
        "run",
        "status:[dim]shutting down sandbox... press Ctrl-C again to force exit[/dim]:dots",
        "status_enter",
        "close",
        "status_exit",
    ]


def test_run_tui_allows_force_exit_during_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import fractal.runtime as runtime_module
    import fractal.tui as tui_module
    import rich.console as rich_console
    from fractal.cli import run_tui

    events: list[str] = []

    class FakeStatus:
        def __enter__(self) -> None:
            events.append("status_enter")

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            events.append("status_exit")

    class FakeConsole:
        def print(self, message: str, *, style: str | None = None) -> None:
            events.append(f"print:{message}:{style}")

        def status(self, message: str, *, spinner: str) -> FakeStatus:
            events.append(f"status:{message}:{spinner}")
            return FakeStatus()

    class FakeRuntime:
        def prewarm(self) -> None:
            events.append("prewarm")

        def close(self) -> None:
            events.append("close")
            raise KeyboardInterrupt

    class FakeFractalRuntime:
        @classmethod
        def create(cls, **kwargs: object) -> FakeRuntime:
            events.append("create")
            return FakeRuntime()

    class FakeTerminalFractalApp:
        def __init__(self, runtime: FakeRuntime, *, console: FakeConsole) -> None:
            events.append("app")

        async def run(self) -> None:
            events.append("run")

    monkeypatch.setattr(rich_console, "Console", FakeConsole)
    monkeypatch.setattr(runtime_module, "FractalRuntime", FakeFractalRuntime)
    monkeypatch.setattr(tui_module, "TerminalFractalApp", FakeTerminalFractalApp)

    result = run_tui(
        SimpleNamespace(
            workspace=tmp_path,
            include=[],
            lm=None,
            sub_lm=None,
            max_iterations=1,
            debug=False,
            resume=None,
        )
    )

    assert result == 130
    assert events[-2:] == [
        "status_exit",
        (
            "print:sandbox shutdown interrupted; a sandbox may still be running. "
            "Run `sbx ls` and `sbx rm --force <name>` to clean it up.:yellow"
        ),
    ]


def test_signature_fields() -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent.signature import build_edit_workspace_signature
    from fractal.session import SessionHistoryTurn

    signature = build_edit_workspace_signature("User: fix tests")
    fields = signature.model_fields

    assert {
        "workspace",
        "included_paths",
        "user_message",
        "session_history",
        "response",
        "changed_files",
    } <= set(fields)
    assert "session_summary" not in fields
    assert fields["session_history"].annotation == list[SessionHistoryTurn]
    assert "User: fix tests" in signature.instructions


def test_service_construction() -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent.service import FractalAgent

    agent = FractalAgent(max_iterations=7, verbose=False, debug=True)

    assert agent.max_iterations == 7
    assert agent.verbose is False
    assert agent.debug is True


def test_agent_aforward_constructs_rlm_and_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent import service
    from fractal.session import SessionHistoryTurn

    calls: dict[str, object] = {}

    class FakePredictRLM:
        def __init__(self, signature: object, **kwargs: object) -> None:
            calls["signature"] = signature
            calls["kwargs"] = kwargs

        async def acall(self, **kwargs: object) -> object:
            calls["acall_count"] = int(calls.get("acall_count", 0)) + 1
            calls["acall_kwargs"] = kwargs
            return SimpleNamespace(response="done", changed_files="README.md")

    monkeypatch.setattr(service, "PredictRLM", FakePredictRLM)

    history_turn = SessionHistoryTurn(
        turn_id="turn-1",
        user_message="previous",
        status="succeeded",
        created_at="2026-05-19T00:00:00+00:00",
        updated_at="2026-05-19T00:00:00+00:00",
    )

    agent = service.FractalAgent(max_iterations=7, verbose=False, debug=True)
    included_path = tmp_path / "included"
    included_path.mkdir()
    result = asyncio.run(
        agent.aforward(
            tmp_path,
            "update the README",
            rendered_session_summary="previous context",
            session_history=[history_turn],
            included_paths=[included_path],
        )
    )

    signature = calls["signature"]
    assert isinstance(signature, type)
    assert "previous context" in signature.instructions
    assert "session_summary" not in signature.model_fields
    assert "session_history" in signature.model_fields
    assert "included_paths" in signature.model_fields
    assert calls["kwargs"] == {
        "lm": None,
        "sub_lm": None,
        "skills": [service.filesystem_coding_skill],
        "max_iterations": 7,
        "verbose": False,
        "debug": True,
        "sandbox_backend": "sbx",
    }
    assert calls["acall_count"] == 1
    acall_kwargs = calls["acall_kwargs"]
    assert isinstance(acall_kwargs, dict)
    workspace = acall_kwargs["workspace"]
    assert isinstance(workspace, service.Workspace)
    assert workspace.path == str(tmp_path.resolve())
    assert "mount_path" not in workspace.model_fields_set
    assert workspace.mode is service.WorkspaceMode.DIRECT
    assert ".fractal" in workspace.exclude
    included_paths = acall_kwargs["included_paths"]
    assert isinstance(included_paths, list)
    assert len(included_paths) == 1
    assert included_paths[0].path == str(included_path.resolve())
    assert "mount_path" not in included_paths[0].model_fields_set
    assert included_paths[0].mode is service.WorkspaceMode.DIRECT
    assert acall_kwargs["user_message"] == "update the README"
    assert acall_kwargs["session_history"] == [history_turn]
    assert result.changed_files == ["README.md"]


def test_agent_aforward_uses_reusable_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent import service

    calls: dict[str, object] = {}
    interpreter = MagicMock()

    class FakePredictRLM:
        def __init__(self, signature: object, **kwargs: object) -> None:
            calls["kwargs"] = kwargs

        async def acall(self, **kwargs: object) -> object:
            return SimpleNamespace(response="done", changed_files=[])

    monkeypatch.setattr(service, "PredictRLM", FakePredictRLM)

    agent = service.FractalAgent(interpreter=interpreter)
    asyncio.run(agent.aforward(tmp_path, "update the README"))

    predictor_kwargs = calls["kwargs"]
    assert isinstance(predictor_kwargs, dict)
    assert predictor_kwargs["interpreter"] is interpreter
    assert "sandbox_backend" not in predictor_kwargs


def test_agent_aforward_omits_empty_included_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent import service

    calls: dict[str, object] = {}

    class FakePredictRLM:
        def __init__(self, signature: object, **kwargs: object) -> None:
            pass

        async def acall(self, **kwargs: object) -> object:
            calls["acall_kwargs"] = kwargs
            return SimpleNamespace(response="done", changed_files=[])

    monkeypatch.setattr(service, "PredictRLM", FakePredictRLM)

    agent = service.FractalAgent(max_iterations=7, verbose=False, debug=True)
    asyncio.run(agent.aforward(tmp_path, "update the README"))

    acall_kwargs = calls["acall_kwargs"]
    assert isinstance(acall_kwargs, dict)
    assert acall_kwargs["included_paths"] is None


def test_build_direct_workspace_mounts_uses_absolute_paths(tmp_path: Path) -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent.service import build_direct_workspace_mounts

    included_path = tmp_path / "included"
    included_path.mkdir()

    mounts = build_direct_workspace_mounts(tmp_path, [included_path])

    assert [
        (mount.host_path, mount.sandbox_path)
        for mount in mounts
    ] == [
        (str(tmp_path.resolve()), str(tmp_path.resolve())),
        (str(included_path.resolve()), str(included_path.resolve())),
    ]


def test_agent_prewarm_prewarms_interpreter() -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent.service import FractalAgent

    interpreter = MagicMock()
    agent = FractalAgent(interpreter=interpreter)

    agent.prewarm()

    interpreter.prewarm.assert_called_once_with()


def test_predict_rlm_sees_absolute_workspace_paths(tmp_path: Path) -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from predict_rlm import PredictRLM

    from fractal.agent.service import Workspace, WorkspaceMode
    from fractal.agent.signature import build_edit_workspace_signature

    included_path = tmp_path / "included"
    included_path.mkdir()
    workspace = Workspace(path=str(tmp_path), mode=WorkspaceMode.DIRECT)
    included = Workspace(path=str(included_path), mode=WorkspaceMode.DIRECT)
    rlm = PredictRLM(
        build_edit_workspace_signature("test"),
        sub_lm=MagicMock(),
        max_iterations=1,
        sandbox_backend="sbx",
    )

    plan, args = rlm._prepare_file_io({
        "workspace": workspace,
        "included_paths": [included],
    })

    assert plan is not None
    assert args["workspace"] == str(tmp_path.resolve())
    assert args["included_paths"] == [str(included_path.resolve())]
    assert [
        (mount.host_path, mount.sandbox_path)
        for mount in plan["direct_workspace_mounts"]
    ] == [
        (str(tmp_path.resolve()), str(tmp_path.resolve())),
        (str(included_path.resolve()), str(included_path.resolve())),
    ]


def test_coerce_result_string_changed_files() -> None:
    from fractal.agent.service import _coerce_result

    result = _coerce_result(SimpleNamespace(response="done", changed_files="README.md"))

    assert result.changed_files == ["README.md"]
