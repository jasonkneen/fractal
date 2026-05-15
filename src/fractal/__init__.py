"""Fractal interactive coding-agent CLI."""

__all__ = ["FractalAgent", "FractalResult"]


def __getattr__(name: str):
    if name == "FractalAgent":
        from .agent.service import FractalAgent

        return FractalAgent
    if name == "FractalResult":
        from .agent.schema import FractalResult

        return FractalResult
    raise AttributeError(name)
