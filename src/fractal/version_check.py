"""Stealth website update check.

On startup Fractal quietly asks the Fractal website whether a newer release of
the ``fractal-rlm`` distribution exists and, if so, surfaces a one-line yellow
notice with the upgrade command. The request includes an anonymous install ID so
the website can count active installs. The check is best-effort: it runs on a
daemon thread, never blocks the CLI, keeps a one-day fallback cache of the latest
version, and swallows every error so an offline machine or a flaky network is
silent rather than noisy.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
import uuid
from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "fractal-rlm"
VERSION_CHECK_URL = "https://fractal.trampoline.ai/api/version-check"
UPGRADE_COMMAND = f"uv tool upgrade {PACKAGE_NAME}"

FETCH_TIMEOUT_SECONDS = 2.0
CACHE_TTL_SECONDS = 24 * 60 * 60
JOIN_TIMEOUT_SECONDS = 0.4


def current_version() -> str | None:
    """The installed ``fractal-rlm`` version, or ``None`` when undeterminable."""
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return None


def _cache_path() -> Path:
    # Reuse the config directory so the cache lives beside the user's config
    # without re-deriving the XDG / home-dir conventions here.
    from .config import default_config_path

    return default_config_path().parent / "version_check.json"


def _read_cache() -> dict[str, object]:
    try:
        raw = _cache_path().read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache(payload: dict[str, object]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        # Caching is an optimisation; a read-only home directory is not an error.
        pass


def _read_cached_latest() -> str | None:
    payload = _read_cache()
    try:
        fetched_at = float(payload["fetched_at"])
        latest = payload["latest"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(latest, str):
        return None
    if time.time() - fetched_at > CACHE_TTL_SECONDS:
        return None
    return latest


def _write_cached_latest(latest: str) -> None:
    payload = _read_cache()
    payload["latest"] = latest
    payload["fetched_at"] = time.time()
    _write_cache(payload)


def _install_id() -> str:
    payload = _read_cache()
    cached = payload.get("install_id")
    if isinstance(cached, str) and cached:
        return cached
    install_id = str(uuid.uuid4())
    payload["install_id"] = install_id
    _write_cache(payload)
    return install_id


def _build_payload() -> dict[str, object]:
    return {
        "install_id": _install_id(),
        "current_version": current_version(),
    }


def _user_agent(current: str | None) -> str:
    return f"{PACKAGE_NAME}/{current or 'unknown'}"


def _fetch_latest_from_website() -> str | None:
    payload = _build_payload()
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        VERSION_CHECK_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": _user_agent(
                payload.get("current_version")
                if isinstance(payload.get("current_version"), str)
                else None
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latest = payload.get("latest")
    return latest if isinstance(latest, str) else None


def _resolve_latest() -> str | None:
    cached = _read_cached_latest()
    try:
        latest = _fetch_latest_from_website()
    except Exception:
        return cached
    if latest is not None:
        _write_cached_latest(latest)
        return latest
    return cached


def _is_newer(latest: str, current: str) -> bool:
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return False
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def format_notice(current: str, latest: str) -> str:
    """The yellow update banner shown when a newer release exists."""
    return (
        f"A new version of Fractal is available: {current} -> {latest}\n"
        f"Update with: {UPGRADE_COMMAND}"
    )


class UpdateNotifier:
    """Runs the website check on a daemon thread and yields a notice when ready."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._latest: str | None = None

    @classmethod
    def start(cls) -> "UpdateNotifier":
        notifier = cls()
        thread = threading.Thread(
            target=notifier._run, name="fractal-version-check", daemon=True
        )
        notifier._thread = thread
        thread.start()
        return notifier

    def _run(self) -> None:
        try:
            self._latest = _resolve_latest()
        except Exception:
            # Stealth: any failure (network, parse, DNS) leaves no trace.
            self._latest = None

    def notice(self, *, timeout: float = JOIN_TIMEOUT_SECONDS) -> str | None:
        """Return the upgrade notice, briefly waiting for the check to finish.

        Returns ``None`` when up to date, the check is unfinished, or anything
        went wrong. Never raises.
        """
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        latest = self._latest
        current = current_version()
        if not latest or not current:
            return None
        if _is_newer(latest, current):
            return format_notice(current, latest)
        return None
