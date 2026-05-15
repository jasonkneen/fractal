"""Fractal RLM agent components."""

__all__ = [
    "EditWorkspace",
    "FractalAgent",
    "FractalResult",
    "filesystem_coding_skill",
]


def __getattr__(name: str):
    if name == "EditWorkspace":
        from .signature import EditWorkspace

        return EditWorkspace
    if name == "FractalAgent":
        from .service import FractalAgent

        return FractalAgent
    if name == "FractalResult":
        from .schema import FractalResult

        return FractalResult
    if name == "filesystem_coding_skill":
        from .skills import filesystem_coding_skill

        return filesystem_coding_skill
    raise AttributeError(name)
