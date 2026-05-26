from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


def include_path(value: str) -> Path:
    path = Path(value)
    if path.is_symlink():
        raise argparse.ArgumentTypeError(
            f"included path cannot be a symlink: {value}"
        )
    resolved = path.resolve()
    if not resolved.exists():
        raise argparse.ArgumentTypeError(f"included path does not exist: {value}")
    if not resolved.is_dir():
        raise argparse.ArgumentTypeError(f"included path is not a directory: {value}")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fractal")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="workspace directory to edit; defaults to the current directory",
    )
    parser.add_argument(
        "--lm", default="openai/gpt-5.5", help="DSPy LM model string for PredictRLM"
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        type=include_path,
        help=(
            "additional directory to mount into the sandbox at its absolute path; "
            "may be passed multiple times"
        ),
    )
    parser.add_argument(
        "--sub-lm", default="openai/gpt-5.1", help="DSPy sub-LM model string"
    )
    parser.add_argument("--max-iterations", type=int, default=30)
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="resume an existing workspace-local session by id",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="reserved for quieter terminal output"
    )
    parser.add_argument(
        "--debug", action="store_true", help="enable PredictRLM debug mode"
    )
    return parser


def run_tui(args: argparse.Namespace) -> int:
    from rich.console import Console

    from .runtime import FractalRuntime
    from .tui import TerminalFractalApp

    console = Console()
    workspace = args.workspace.resolve()
    runtime = FractalRuntime.create(
        workspace_path=workspace,
        included_paths=args.include,
        lm=args.lm,
        sub_lm=args.sub_lm,
        max_iterations=args.max_iterations,
        verbose=False,
        debug=args.debug,
        session_id=args.resume,
    )
    try:
        with console.status("[dim]starting sandbox...[/dim]", spinner="dots"):
            runtime.prewarm()
        asyncio.run(TerminalFractalApp(runtime, console=console).run())
    finally:
        try:
            with console.status(
                "[dim]shutting down sandbox... press Ctrl-C again to force exit[/dim]",
                spinner="dots",
            ):
                runtime.close()
        except KeyboardInterrupt:
            console.print(
                "sandbox shutdown interrupted; a sandbox may still be running. "
                "Run `sbx ls` and `sbx rm --force <name>` to clean it up.",
                style="yellow",
            )
            return 130
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_tui(args)


if __name__ == "__main__":
    raise SystemExit(main())
