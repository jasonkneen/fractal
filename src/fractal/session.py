from __future__ import annotations

import json
import shutil
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SESSION_DIR = ".fractal"
SESSION_FILE = "session.json"
MAX_TURNS = 12


@dataclass(slots=True)
class SessionTurn:
    role: str
    content: str
    changed_files: list[str]


@dataclass(slots=True)
class FractalSession:
    turns: list[SessionTurn]

    @classmethod
    def load(cls, workspace_path: str | Path) -> "FractalSession":
        path = session_path(workspace_path)
        if not path.exists():
            return cls(turns=[])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            backup_path = _backup_bad_session(path)
            backup_note = (
                f"Preserved a backup at {backup_path}."
                if backup_path is not None
                else "Could not preserve a backup."
            )
            warnings.warn(
                f"Ignoring unreadable Fractal session at {path}: {exc}. "
                f"{backup_note}",
                RuntimeWarning,
                stacklevel=2,
            )
            return cls(turns=[])

        if not isinstance(data, dict):
            warnings.warn(
                f"Ignoring malformed Fractal session at {path}: expected a JSON object.",
                RuntimeWarning,
                stacklevel=2,
            )
            return cls(turns=[])

        raw_turns = data.get("turns", [])
        if not isinstance(raw_turns, list):
            warnings.warn(
                f"Ignoring malformed Fractal session at {path}: 'turns' must be a list.",
                RuntimeWarning,
                stacklevel=2,
            )
            return cls(turns=[])

        turns: list[SessionTurn] = []
        skipped = 0
        for item in raw_turns:
            if not isinstance(item, dict):
                skipped += 1
                continue
            changed_files = _coerce_changed_files(item.get("changed_files"))
            turns.append(
                SessionTurn(
                    role=str(item.get("role", "")),
                    content=str(item.get("content", "")),
                    changed_files=changed_files,
                )
            )
        if skipped:
            warnings.warn(
                f"Ignored {skipped} malformed Fractal session entr"
                f"{'y' if skipped == 1 else 'ies'} at {path}.",
                RuntimeWarning,
                stacklevel=2,
            )
        return cls(turns=turns[-MAX_TURNS:])

    def save(self, workspace_path: str | Path) -> None:
        path = session_path(workspace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "turns": [asdict(turn) for turn in self.turns[-MAX_TURNS:]],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def summary(self) -> str:
        if not self.turns:
            return ""
        lines: list[str] = []
        for turn in self.turns[-MAX_TURNS:]:
            changed = f" changed_files={turn.changed_files}" if turn.changed_files else ""
            lines.append(f"{turn.role}: {turn.content}{changed}")
        return "\n".join(lines)

    def add_user_message(self, content: str) -> None:
        self.turns.append(SessionTurn(role="user", content=content, changed_files=[]))
        self.turns = self.turns[-MAX_TURNS:]

    def add_agent_response(self, content: str, changed_files: list[str]) -> None:
        self.turns.append(
            SessionTurn(role="assistant", content=content, changed_files=changed_files)
        )
        self.turns = self.turns[-MAX_TURNS:]


def session_path(workspace_path: str | Path) -> Path:
    return Path(workspace_path) / SESSION_DIR / SESSION_FILE


def _backup_bad_session(path: Path) -> Path | None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = path.with_suffix(f"{path.suffix}.bad-{stamp}")
    counter = 1
    while backup_path.exists():
        backup_path = path.with_suffix(f"{path.suffix}.bad-{stamp}-{counter}")
        counter += 1
    try:
        shutil.copy2(path, backup_path)
    except OSError:
        return None
    return backup_path


def _coerce_changed_files(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(path) for path in value]
    return [str(value)]
