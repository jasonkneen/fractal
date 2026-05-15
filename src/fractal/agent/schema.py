from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FractalResult:
    """Minimal typed result returned by the Fractal agent service."""

    response: str
    changed_files: list[str] = field(default_factory=list)
