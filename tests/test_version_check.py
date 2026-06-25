from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID

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


def test_install_id_is_generated_and_reused(cache_in_tmp: Path) -> None:
    install_id = version_check._install_id()

    assert str(UUID(install_id)) == install_id
    assert version_check._install_id() == install_id
    payload = json.loads(cache_in_tmp.read_text())
    assert payload["install_id"] == install_id


def test_build_payload_includes_only_install_id_and_current_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_check, "_install_id", lambda: "install-1")
    monkeypatch.setattr(version_check, "current_version", lambda: "0.1.0")

    assert version_check._build_payload() == {
        "install_id": "install-1",
        "current_version": "0.1.0",
    }


def test_fetch_latest_from_website_posts_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"latest": "3.0.0"}'

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        calls["request"] = request
        calls["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(version_check.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(version_check, "_install_id", lambda: "install-1")
    monkeypatch.setattr(version_check, "current_version", lambda: "0.1.0")
    payload = {"install_id": "install-1", "current_version": "0.1.0"}

    assert version_check._fetch_latest_from_website() == "3.0.0"

    request = calls["request"]
    assert getattr(request, "full_url") == version_check.VERSION_CHECK_URL
    assert getattr(request, "data") == json.dumps(payload).encode("utf-8")
    assert getattr(request, "get_method")() == "POST"
    assert calls["timeout"] == version_check.FETCH_TIMEOUT_SECONDS


def test_resolve_latest_fetches_and_caches(
    cache_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_fetch() -> str:
        nonlocal calls
        calls += 1
        return "9.9.9"

    monkeypatch.setattr(version_check, "_fetch_latest_from_website", fake_fetch)

    assert version_check._resolve_latest() == "9.9.9"
    assert calls == 1
    assert json.loads(cache_in_tmp.read_text())["latest"] == "9.9.9"


def test_resolve_latest_falls_back_to_cached_latest_on_fetch_failure(
    cache_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_in_tmp.write_text(
        json.dumps(
            {
                "install_id": "install-1",
                "latest": "1.0.0",
                "fetched_at": time.time(),
            }
        )
    )

    def boom() -> str:
        raise OSError("network down")

    monkeypatch.setattr(version_check, "_fetch_latest_from_website", boom)

    assert version_check._resolve_latest() == "1.0.0"


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
