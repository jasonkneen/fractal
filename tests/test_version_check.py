from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from fractal import version_check


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "version_check.json"
    monkeypatch.setattr(version_check, "_cache_path", lambda: cache)
    return cache


def test_is_newer_compares_versions() -> None:
    assert version_check._is_newer("0.2.0", "0.1.0")
    assert version_check._is_newer("0.7.0a3", "0.7.0a2")
    assert not version_check._is_newer("0.1.0", "0.1.0")
    assert not version_check._is_newer("0.0.9", "0.1.0")
    assert not version_check._is_newer("garbage", "0.1.0")


def test_format_notice_includes_versions_and_command() -> None:
    notice = version_check.format_notice("0.1.0", "0.2.0")
    assert "0.1.0 -> 0.2.0" in notice
    assert version_check.UPGRADE_COMMAND in notice


def test_resolve_latest_fetches_and_caches(
    cache_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_fetch() -> str:
        nonlocal calls
        calls += 1
        return "9.9.9"

    monkeypatch.setattr(version_check, "_fetch_latest_from_pypi", fake_fetch)

    assert version_check._resolve_latest() == "9.9.9"
    assert calls == 1
    # Second call is served from the freshly written cache.
    assert version_check._resolve_latest() == "9.9.9"
    assert calls == 1


def test_resolve_latest_ignores_stale_cache(
    cache_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = time.time() - version_check.CACHE_TTL_SECONDS - 1
    cache_in_tmp.write_text(json.dumps({"latest": "1.0.0", "fetched_at": stale}))
    monkeypatch.setattr(version_check, "_fetch_latest_from_pypi", lambda: "2.0.0")

    assert version_check._resolve_latest() == "2.0.0"


def test_notifier_returns_notice_when_newer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_check, "_resolve_latest", lambda: "9.9.9")
    monkeypatch.setattr(version_check, "current_version", lambda: "0.1.0")

    notifier = version_check.UpdateNotifier.start()
    notice = notifier.notice(timeout=2.0)
    assert notice is not None
    assert "9.9.9" in notice


def test_notifier_silent_when_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_check, "_resolve_latest", lambda: "0.1.0")
    monkeypatch.setattr(version_check, "current_version", lambda: "0.1.0")

    notifier = version_check.UpdateNotifier.start()
    assert notifier.notice(timeout=2.0) is None


def test_notifier_silent_on_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> str:
        raise OSError("network down")

    monkeypatch.setattr(version_check, "_resolve_latest", boom)
    monkeypatch.setattr(version_check, "current_version", lambda: "0.1.0")

    notifier = version_check.UpdateNotifier.start()
    assert notifier.notice(timeout=2.0) is None
