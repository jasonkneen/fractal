from __future__ import annotations

import asyncio
from types import SimpleNamespace
from pathlib import Path

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
    assert args.max_iterations == 30


def test_session_round_trip(tmp_path: Path) -> None:
    from fractal.session import FractalSession

    session = FractalSession(turns=[])
    session.add_user_message("change the README")
    session.add_agent_response("updated", ["README.md"])
    session.save(tmp_path)

    loaded = FractalSession.load(tmp_path)

    assert "change the README" in loaded.summary()
    assert loaded.turns[-1].changed_files == ["README.md"]


def test_signature_fields() -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent.signature import EditWorkspace

    fields = EditWorkspace.model_fields

    assert {"workspace", "user_message", "session_summary", "response", "changed_files"} <= set(
        fields
    )


def test_service_construction() -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent.service import FractalAgent

    agent = FractalAgent(max_iterations=7, verbose=False, debug=True)

    assert agent.max_iterations == 7
    assert agent.verbose is False
    assert agent.debug is True


def test_agent_aforward_constructs_rlm_and_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if not workspace_available():
        pytest.skip("predict_rlm.Workspace is not exported by the local branch yet")

    from fractal.agent import service

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

    agent = service.FractalAgent(max_iterations=7, verbose=False, debug=True)
    result = asyncio.run(
        agent.aforward(tmp_path, "update the README", session_summary="previous context")
    )

    assert calls["signature"] is service.EditWorkspace
    assert calls["kwargs"] == {
        "lm": None,
        "sub_lm": None,
        "skills": [service.filesystem_coding_skill],
        "max_iterations": 7,
        "verbose": False,
        "debug": True,
    }
    assert calls["acall_count"] == 1
    acall_kwargs = calls["acall_kwargs"]
    assert isinstance(acall_kwargs, dict)
    workspace = acall_kwargs["workspace"]
    assert isinstance(workspace, service.Workspace)
    assert workspace.path == str(tmp_path)
    assert ".fractal" in workspace.exclude
    assert acall_kwargs["user_message"] == "update the README"
    assert acall_kwargs["session_summary"] == "previous context"
    assert result.changed_files == ["README.md"]


def test_coerce_result_string_changed_files() -> None:
    from fractal.agent.service import _coerce_result

    result = _coerce_result(SimpleNamespace(response="done", changed_files="README.md"))

    assert result.changed_files == ["README.md"]
