from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .session import FractalSession, session_path


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
        "--sub-lm", default="openai/gpt-5.1", help="DSPy sub-LM model string"
    )
    parser.add_argument("--max-iterations", type=int, default=30)
    parser.add_argument(
        "--quiet", action="store_true", help="disable verbose RLM logging"
    )
    parser.add_argument(
        "--debug", action="store_true", help="enable PredictRLM debug mode"
    )
    return parser


async def run_repl(args: argparse.Namespace) -> int:
    from predict_rlm.trace import extract_trace_from_exc

    from .agent.service import FractalAgent, coerce_trace

    workspace = args.workspace.resolve()
    session = FractalSession.load(workspace)
    agent = FractalAgent(
        lm=args.lm,
        sub_lm=args.sub_lm,
        max_iterations=args.max_iterations,
        verbose=not args.quiet,
        debug=args.debug,
    )

    print(f"Fractal workspace: {workspace}")
    print("Type /exit or /quit to quit.")

    while True:
        try:
            user_message = input("fractal> ").strip()
        except EOFError:
            print()
            return 0

        if not user_message:
            continue
        if user_message in {"/exit", "/quit"}:
            return 0
        if user_message == "/status":
            print(f"session id: {session.session_id}")
            print(f"session: {session_path(workspace, session.session_id)}")
            print(f"turns: {len(session.turns)}")
            continue

        turn_id = session.add_user_message(user_message)
        session.save(workspace)

        try:
            result = await agent.aforward(
                workspace_path=workspace,
                user_message=user_message,
                rendered_session_summary=session.summary(),
                session_history=session.session_history_payload(),
            )
        except Exception as exc:
            session.add_agent_failure(
                str(exc),
                trace=coerce_trace(extract_trace_from_exc(exc)),
                turn_id=turn_id,
            )
            session.save(workspace)
            raise

        session.add_agent_response(
            result.response,
            result.changed_files,
            trace=result.trace,
            turn_id=turn_id,
        )
        session.save(workspace)

        if result.response:
            print(result.response)
        if result.changed_files:
            print("changed files:")
            for path in result.changed_files:
                print(f"  {path}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(run_repl(args))


if __name__ == "__main__":
    raise SystemExit(main())
