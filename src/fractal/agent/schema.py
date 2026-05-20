from __future__ import annotations

from dataclasses import dataclass, field

from predict_rlm import RunTrace


@dataclass(slots=True)
class FractalResult:
    """Minimal typed result returned by the Fractal agent service."""

    response: str
    changed_files: list[str] = field(default_factory=list)
    trace: RunTrace | None = None
