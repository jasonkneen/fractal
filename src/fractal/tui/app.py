from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import textwrap
from functools import partial
from io import StringIO
from pathlib import Path
from typing import Any, Protocol, TextIO

from predict_rlm import RunTrace
from predict_rlm.trace import IterationStep
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    Float,
    FloatContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.menus import CompletionsMenu, MultiColumnCompletionsMenu
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Label
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.status import Status
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from fractal.agent.schema import FractalIterationEvent, FractalResult
from fractal.context_meter import (
    ContextEstimateCacheKey,
    context_estimate_cache_key,
    estimate_next_context_tokens,
)
from fractal.events import FractalRuntimeEvent
from fractal.session import (
    SessionSummary,
    SummaryTurn,
    TurnUsage,
    list_sessions,
    summarize_usage,
    turn_usage_from_trace,
)

PROMPT_ICON = "❯"
NERD_FONT_PROMPT_ICON = "\uf105"
USER_MESSAGE_LABEL = "you"
CONTEXT_LEVEL_SEGMENTS = 5
CONTEXT_LEVEL_SEGMENT_TOKENS = 40_000

PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "bold #8b5cf6",
        "session": "ansibrightblack",
        "bottom-toolbar": "noreverse",
        "bottom-toolbar.label": "#6b7280",
        "bottom-toolbar.value": "ansicyan",
        "fixed-header": "noreverse",
        "fixed-header.title": "bold #8b5cf6",
        "fixed-header.label": "#6b7280",
        "fixed-header.value": "ansicyan",
        "fixed-header.help": "#6b7280",
        "fixed-input": "noreverse",
        "fixed-input.border": "#4b5563",
        "fixed-input.title": "#6b7280",
    }
)
SLASH_COMPLETION_MENU_ROWS = 0
FIXED_INPUT_ROWS = 3
FIXED_INPUT_MIN_ROWS = FIXED_INPUT_ROWS
FIXED_INPUT_MAX_ROWS = FIXED_INPUT_ROWS
FIXED_INPUT_PREFERRED_ROWS = FIXED_INPUT_ROWS

SLASH_COMMANDS = {
    "/help": "Show available commands",
    "/sessions": "List resumable sessions in this workspace",
    "/resume": "Resume an existing session by id",
    "/new": "Start a fresh session",
    "/model": "Change the main model and sub-model",
    "/provider": "Change provider, model, and auth setup",
    "/providers": "Change provider, model, and auth setup",
    "/usage": "Show token usage and cost for this session",
    "/verbose": "Toggle verbose RLM iteration output",
    "/exit": "Exit Fractal",
    "/quit": "Exit Fractal",
}
RUNNING_STATUS = "[dim]running RLM... (Ctrl-C to interrupt)[/dim]"
RUNNING_FRAME_TEXT = "running RLM... Ctrl-C to interrupt"
INTERRUPTING_STATUS = "[yellow]interrupting RLM...[/yellow] [dim](waiting for shutdown)[/dim]"
RUNTIME_EVENT_STYLES = {
    "file_read": "cyan",
    "file_write": "yellow",
    "command": "magenta",
}
MARKDOWN_STYLE_OVERRIDES = {
    # Rich defaults inline code to "cyan on black", which reads as a
    # highlight block inside Fractal's already framed response panel.
    # Keep Markdown emphasis visible without adding another background.
    "markdown.code": "bold cyan",
    "markdown.code_block": "cyan",
    "markdown.strong": "bold",
    "markdown.item.bullet": "bright_black",
    "markdown.list": "none",
}
MARKDOWN_THEME = Theme(MARKDOWN_STYLE_OVERRIDES)


class FractalMarkdown(Markdown):
    def __rich_console__(self, console: Console, options: object) -> object:
        with console.use_theme(MARKDOWN_THEME):
            yield from super().__rich_console__(console, options)


class SlashCommandCompleter(Completer):
    def get_completions(self, document: Document, complete_event: object) -> object:
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for command, description in SLASH_COMMANDS.items():
            if command.startswith(text):
                yield Completion(
                    command,
                    start_position=-len(text),
                    display_meta=description,
                )


def slash_command_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event: object) -> None:
        buffer = event.current_buffer
        complete_state = buffer.complete_state
        completion = (
            complete_state.current_completion if complete_state is not None else None
        )
        if completion is not None:
            buffer.apply_completion(completion)
            if not buffer.document.text_before_cursor.endswith(" "):
                buffer.insert_text(" ")
            return
        buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _(event: object) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("c-j")
    def _(event: object) -> None:
        event.current_buffer.insert_text("\n")

    return bindings


def _prompt_continuation(width: int, line_number: int, is_soft_wrap: bool) -> str:
    return "  "


class _FixedFramePromptSession(PromptSession[str]):
    """PromptSession with a fixed header and fixed bottom input area.

    prompt_toolkit owns the screen while the user is typing. By wrapping the
    normal prompt layout in a top header plus a flexible middle window, the
    header remains anchored at the top and the multiline input/toolbar stays at
    the bottom; terminal scrollback and command output occupy the space between
    prompts rather than pushing the input area around.
    """

    def __init__(self, *args: object, frame_factory: object, **kwargs: object) -> None:
        self._frame_factory = frame_factory
        super().__init__(*args, **kwargs)

    def _create_layout(self) -> Layout:
        layout = super()._create_layout()
        prompt_container = layout.container
        if isinstance(prompt_container, HSplit):
            message_area = Window(height=Dimension(preferred=0, weight=1))
            completion_floats = _lift_completion_menu_floats(prompt_container)
            layout.container = self._frame_factory(
                message_area=message_area,
                input_body=prompt_container,
                floats=completion_floats,
                show_transcript=True,
            )
        return layout


def _rounded_input_frame(body: object, title: object) -> HSplit:
    fill = partial(Window, style="class:fixed-input.border")
    top = VSplit(
        [
            fill(width=1, height=1, char="╭"),
            fill(char="─"),
            Label(
                title,
                style="class:fixed-input.title",
                dont_extend_width=True,
            ),
            fill(width=1, height=1, char="╮"),
        ],
        height=1,
    )
    middle = VSplit(
        [
            fill(width=1, char="│"),
            body,
            fill(width=1, char="│"),
        ],
        padding=0,
    )
    bottom = VSplit(
        [
            fill(width=1, height=1, char="╰"),
            fill(char="─"),
            fill(width=1, height=1, char="╯"),
        ],
        height=1,
    )
    return HSplit(
        [top, middle, bottom],
        height=Dimension(
            min=FIXED_INPUT_MIN_ROWS,
            preferred=FIXED_INPUT_PREFERRED_ROWS,
            max=FIXED_INPUT_MAX_ROWS,
        ),
        style="class:fixed-input",
    )


def _lift_completion_menu_floats(container: object) -> list[Float]:
    """Move completion menus out of the fixed input frame.

    prompt_toolkit creates completion menus as cursor-relative floats inside the
    prompt input container. Fractal fixes that prompt to the bottom of the
    screen, so leaving the menu there clips it to the input frame. Keeping the
    floats cursor-relative but attaching them to the full-screen root lets slash
    command menus open upward into the message area.
    """
    lifted: list[Float] = []
    _collect_completion_menu_floats(container, lifted)
    return lifted


def _collect_completion_menu_floats(container: object, lifted: list[Float]) -> None:
    floats = getattr(container, "floats", None)
    if isinstance(floats, list):
        kept: list[Float] = []
        for float_ in floats:
            content = getattr(float_, "content", None)
            if isinstance(content, (CompletionsMenu, MultiColumnCompletionsMenu)):
                lifted.append(float_)
            else:
                kept.append(float_)
                _collect_completion_menu_floats(content, lifted)
        floats[:] = kept

    for attr in ("children",):
        children = getattr(container, attr, None)
        if children is not None:
            for child in children:
                _collect_completion_menu_floats(child, lifted)

    for attr in ("body", "content", "alternative_content"):
        child = getattr(container, attr, None)
        if child is not None:
            _collect_completion_menu_floats(child, lifted)


def _format_token_count(tokens: int) -> str:
    if tokens < 1000:
        return str(tokens)
    if tokens < 1_000_000:
        return f"{tokens / 1000:.1f}k"
    return f"{tokens / 1_000_000:.2f}M"


def _nerd_font_enabled() -> bool:
    value = os.environ.get("FRACTAL_NERD_FONT", os.environ.get("NERD_FONT", ""))
    return value.lower() in {"1", "true", "yes", "on"}


def _prompt_icon() -> str:
    return NERD_FONT_PROMPT_ICON if _nerd_font_enabled() else PROMPT_ICON


def _context_level_bar(tokens: int) -> str:
    filled = min(
        CONTEXT_LEVEL_SEGMENTS,
        (max(tokens, 0) + CONTEXT_LEVEL_SEGMENT_TOKENS - 1) // CONTEXT_LEVEL_SEGMENT_TOKENS,
    )
    empty = CONTEXT_LEVEL_SEGMENTS - filled
    return f"{'▰' * filled}{'▱' * empty}"


class SessionLike(Protocol):
    @property
    def summary_model(self) -> SessionSummary: ...


class FractalRuntimeLike(Protocol):
    workspace_path: Path

    @property
    def session_id(self) -> str: ...

    @property
    def session(self) -> SessionLike: ...

    def resume(self, session_id: str) -> None: ...

    def new_session(self) -> None: ...

    @property
    def provider_label(self) -> str: ...

    @property
    def model_label(self) -> str: ...

    @property
    def sub_model_label(self) -> str: ...

    def apply_provider_selection(
        self, selection: object, *, sub_model: str | None = None
    ) -> None: ...

    async def submit(self, user_message: str, **kwargs: object) -> FractalResult: ...


class _PrintedTurnStatus:
    def __init__(self, console: Console) -> None:
        self.console = console
        self.started = False

    def start(self) -> None:
        if not self.started:
            self.console.print(Text("running RLM... (Ctrl-C to interrupt)", style="dim"))
            self.started = True

    def update(self, value: str) -> None:
        self.console.print(value)

    def stop(self) -> None:
        return


class TerminalFractalApp:
    """Terminal-native Fractal interface using the user's normal scrollback."""

    def __init__(
        self,
        runtime: FractalRuntimeLike,
        *,
        console: Console | None = None,
        input_stream: TextIO | None = None,
        prompt_session: PromptSession[str] | None = None,
        verbose_iterations: bool = False,
        banner: str | None = None,
        update_notice: str | None = None,
        config_stdin: TextIO | None = None,
        config_stdout: TextIO | None = None,
        config_stderr: TextIO | None = None,
    ) -> None:
        self.runtime = runtime
        self.console = console or Console()
        self.input_stream = input_stream
        self.verbose_iterations = verbose_iterations
        self.banner = banner
        self.update_notice = update_notice
        self.config_stdin = config_stdin or input_stream or sys.stdin
        self.config_stdout = config_stdout or getattr(self.console, "file", sys.stdout)
        self.config_stderr = config_stderr or getattr(self.console, "file", sys.stderr)
        self.prompt_session = prompt_session or _FixedFramePromptSession(
            style=PROMPT_STYLE,
            completer=SlashCommandCompleter(),
            # Only auto-complete while typing a slash command. prompt_toolkit
            # reserves eight menu rows by default, which can make short
            # terminals fail with "Window too small" as soon as "/" opens the
            # command menu inside our fixed header/input frame.
            complete_while_typing=Condition(self._typing_slash_command),
            reserve_space_for_menu=SLASH_COMPLETION_MENU_ROWS,
            key_bindings=slash_command_key_bindings(),
            multiline=True,
            prompt_continuation=_prompt_continuation,
            frame_factory=self._fixed_frame_container,
            erase_when_done=False,
        )
        self._fixed_command_lines: list[str] = []
        self._rendered_turn_ids: set[str] = set()
        self._pending_turn_ids: set[str] = set()
        self._user_message_rendered_turn_ids: set[str] = set()
        self._sigint_mode = "prompt"
        self._active_submit_task: asyncio.Task[FractalResult] | None = None
        self._turn_interrupt_requested = False
        self._active_status: Any | None = None
        self._last_turn_live_iteration_count = 0
        self._context_estimate_cache: (
            tuple[ContextEstimateCacheKey, int | None] | None
        ) = None

    async def run(self) -> None:
        previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        try:
            fixed_frame = self.input_stream is None and self.console.is_terminal
            if not fixed_frame:
                if self.banner:
                    self.console.print(Text(self.banner, style="bold #8b5cf6"))
                    self.console.print()
                self.render_header()
            self.render_new_turns()

            while True:
                self._sigint_mode = "prompt"
                self._active_submit_task = None
                self._turn_interrupt_requested = False

                message = await self.read_message()
                if message is None or message in {"/exit", "/quit"}:
                    return
                if not message:
                    continue
                if await self.handle_slash_command(message):
                    self._sigint_mode = "prompt"
                    continue

                self._sigint_mode = "turn"
                await self._execute_turn(message)
        finally:
            self._reset_live_scroll_region()
            signal.signal(signal.SIGINT, previous_sigint_handler)

    async def _execute_turn(self, message: str) -> None:
        result = await self.run_turn(message)
        if result is None:
            return
        self.console.print(render_turn_footer(result))
        if result.changed_files:
            self.console.print(render_changed_files(result.changed_files))
        if result.trace is not None and self._last_turn_live_iteration_count == 0:
            self.console.print(
                render_trace_summary(
                    result.trace,
                    verbose=self.verbose_iterations,
                )
            )
        self.render_new_turns()

    async def run_turn(self, message: str) -> FractalResult | None:
        def mark_pending() -> None:
            self.render_latest_user_message()

        loop = asyncio.get_running_loop()

        def show_runtime_event(event: FractalRuntimeEvent) -> None:
            loop.call_soon_threadsafe(self._show_runtime_event_status, event)

        live_iteration_events_seen = 0

        def show_iteration_event(event: FractalIterationEvent) -> None:
            nonlocal live_iteration_events_seen
            live_iteration_events_seen += 1
            loop.call_soon_threadsafe(self._show_iteration_event_status, event)

        status = self._turn_status()
        status.start()
        self._active_status = status
        status_running = True
        self._last_turn_live_iteration_count = 0
        if self._turn_interrupt_requested:
            self._show_interrupting_status()

        def stop_status() -> None:
            nonlocal status_running
            if status_running:
                status.stop()
                status_running = False

        submit_task = asyncio.create_task(
            self.runtime.submit(
                message,
                on_pending=mark_pending,
                on_runtime_event=show_runtime_event,
                on_iteration_event=show_iteration_event,
                interrupt_requested=lambda: self._turn_interrupt_requested,
            )
        )
        self._active_submit_task = submit_task
        try:
            result = await submit_task
        except asyncio.CancelledError:
            if not self._turn_interrupt_requested:
                raise
            stop_status()
            self.render_new_turns()
            return None
        except Exception:
            stop_status()
            self.console.print(Text("✗ failed", style="red"))
            self.render_new_turns()
            return None
        finally:
            self._last_turn_live_iteration_count = live_iteration_events_seen
            self._active_submit_task = None
            self._active_status = None
            self._sigint_mode = "prompt"
            stop_status()
        return result

    def _handle_sigint(self, signum: int, frame: object) -> None:
        if self._sigint_mode != "turn":
            # A second Ctrl-C can arrive after the interrupted turn has already
            # returned control to the prompt. Raising from the process signal
            # handler escapes prompt_toolkit/asyncio and crashes the CLI.
            return
        self._turn_interrupt_requested = True
        self._show_interrupting_status()
        task = self._active_submit_task
        if task is not None and not task.done():
            task.cancel()

    def _typing_slash_command(self) -> bool:
        buffer = getattr(self.prompt_session, "default_buffer", None)
        if buffer is None:
            return False
        return buffer.document.text.startswith("/")

    def _fixed_frame_container(
        self,
        *,
        message_area: object,
        input_body: object,
        floats: list[Float] | None = None,
        show_transcript: bool = False,
    ) -> FloatContainer:
        header = Window(
            content=FormattedTextControl(self._render_fixed_header),
            height=Dimension.exact(3),
            style="class:fixed-header",
        )
        if show_transcript:
            message_area = self._fixed_transcript_window()
        fixed_frame = HSplit(
            [
                header,
                message_area,
                _rounded_input_frame(input_body, self._input_frame_title_fragments),
            ]
        )
        return FloatContainer(fixed_frame, floats or [])

    def _fixed_transcript_window(self) -> Window:
        return Window(
            content=FormattedTextControl(self._fixed_transcript_fragments),
            height=Dimension(preferred=0, weight=1),
            wrap_lines=False,
        )

    def _fixed_transcript_fragments(self) -> list[tuple[str, str]]:
        return [("", "\n".join(self._fixed_transcript_lines()))]

    def _fixed_transcript_lines(self) -> list[str]:
        visible_rows = max(self.console.height - FIXED_INPUT_ROWS - 3, 1)
        width = max(self.console.width - 2, 20)
        lines: list[str] = []
        try:
            turns = self.runtime.session.summary_model.turns
        except Exception:
            turns = []
        for turn in turns:
            lines.extend(
                self._wrap_transcript_text(
                    f"{USER_MESSAGE_LABEL} {turn.user.message}",
                    width,
                )
            )
            if turn.agent is None:
                lines.append("  …")
            else:
                response = turn.agent.response.strip() or f"[{turn.agent.status}]"
                lines.extend(self._wrap_transcript_text(response, width))
            lines.append("")
        lines.extend(self._fixed_command_lines)
        return lines[-visible_rows:]

    def _wrap_transcript_text(self, text: str, width: int) -> list[str]:
        wrapped: list[str] = []
        for raw_line in str(text).splitlines() or [""]:
            wrapped.extend(
                textwrap.wrap(
                    raw_line,
                    width=width,
                    replace_whitespace=False,
                    drop_whitespace=False,
                )
                or [""]
            )
        return wrapped

    def _remember_fixed_output(self, *renderables: object) -> None:
        if not self._uses_live_fixed_frame():
            return
        output = StringIO()
        capture = Console(
            file=output,
            force_terminal=False,
            color_system=None,
            width=self.console.width,
            legacy_windows=False,
        )
        capture.print(*renderables)
        lines = output.getvalue().rstrip("\n").splitlines()
        if lines:
            self._fixed_command_lines.extend(lines)
            self._fixed_command_lines = self._fixed_command_lines[-200:]

    def _print_command_output(self, *renderables: object) -> None:
        self._remember_fixed_output(*renderables)
        self.console.print(*renderables)

    def _setup_menu_frame_container(self, menu_container: object) -> FloatContainer:
        message_area = HSplit(
            [
                menu_container,
                Window(height=Dimension(preferred=0, weight=1)),
            ],
            height=Dimension(preferred=0, weight=1),
        )
        prompt_line = Window(
            content=FormattedTextControl(self._fixed_prompt_placeholder_fragments),
            height=Dimension.exact(1),
            dont_extend_height=True,
        )
        return self._fixed_frame_container(
            message_area=message_area,
            input_body=prompt_line,
        )

    def _fixed_prompt_placeholder_fragments(self) -> list[tuple[str, str]]:
        return self._input_prompt_fragments()

    def _input_prompt_fragments(self) -> list[tuple[str, str]]:
        return [("class:prompt", self._prompt_icon()), ("", " ")]

    def _prompt_icon(self) -> str:
        return _prompt_icon()

    def _input_frame_title_fragments(self) -> list[tuple[str, str]]:
        tokens = self._next_context_tokens()
        if tokens is None:
            return [("class:fixed-input.title", " ctx -- ")]
        return [
            ("class:fixed-input.title", " ctx "),
            ("class:bottom-toolbar.value", _context_level_bar(tokens)),
            ("class:fixed-input.title", f" ~{_format_token_count(tokens)} "),
        ]

    def _next_context_tokens(self) -> int | None:
        try:
            key = context_estimate_cache_key(self.runtime)
        except Exception:
            return None
        if self._context_estimate_cache is not None:
            cached_key, cached_tokens = self._context_estimate_cache
            if cached_key == key:
                return cached_tokens
        try:
            tokens = estimate_next_context_tokens(self.runtime)
        except Exception:
            tokens = None
        self._context_estimate_cache = (key, tokens)
        return tokens

    def _show_interrupting_status(self) -> None:
        status = self._active_status
        if status is None:
            return
        status.update(INTERRUPTING_STATUS)

    def _turn_status(self) -> Any:
        if self._uses_live_fixed_frame():
            return _PrintedTurnStatus(self.console)
        return self.console.status(RUNNING_STATUS, spinner="dots")

    def _uses_live_fixed_frame(self) -> bool:
        return self.input_stream is None and self.console.is_terminal

    def _show_runtime_event_status(self, event: FractalRuntimeEvent) -> None:
        if self._turn_interrupt_requested:
            return
        self.console.print(render_runtime_event_log(event))

    def _show_iteration_event_status(self, event: FractalIterationEvent) -> None:
        if self._turn_interrupt_requested:
            return
        self.console.print(
            render_iteration_event_log(
                event,
                verbose=self.verbose_iterations,
            )
        )

    def _render_fixed_header(self) -> list[tuple[str, str]]:
        try:
            return self._fixed_header_fragments()
        except Exception:
            return [("class:fixed-header.title", " Fractal\n"), ("", "\n\n")]

    def _fixed_header_fragments(self) -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = [
            ("class:fixed-header.title", " Fractal"),
            ("", " | "),
            ("class:fixed-header.label", str(self.runtime.workspace_path)),
            ("", " | "),
            ("class:fixed-header.label", "session "),
            ("class:fixed-header.value", self.runtime.session_id),
            ("", "\n"),
        ]
        model_label = getattr(self.runtime, "model_label", None)
        if model_label is None:
            lm = getattr(self.runtime, "lm", None)
            model_label = str(lm) if lm is not None else None
        verbose_state = "on" if self.verbose_iterations else "off"
        if model_label:
            sub_label = getattr(self.runtime, "sub_model_label", None) or model_label
            fragments.extend(
                [
                    ("class:fixed-header.label", " model "),
                    ("class:fixed-header.value", str(model_label)),
                    ("class:fixed-header.label", " | sub "),
                    ("class:fixed-header.value", str(sub_label)),
                    ("class:fixed-header.label", " | verbose "),
                    ("class:fixed-header.value", verbose_state),
                ]
            )
        else:
            fragments.extend(
                [
                    ("class:fixed-header.label", " verbose "),
                    ("class:fixed-header.value", verbose_state),
                ]
            )
        fragments.append(("", "\n"))
        help_text = " Type /help for commands, /exit to quit. Alt+Enter inserts a newline."
        if self.update_notice:
            help_text = f"{help_text}  {self.update_notice}"
        fragments.append(("class:fixed-header.help", help_text))
        return fragments

    def render_header(self) -> None:
        self.console.print(
            Text.assemble(
                ("Fractal", "bold"),
                " | ",
                (str(self.runtime.workspace_path), "dim"),
                " | session ",
                (self.runtime.session_id, "cyan"),
            )
        )
        model_label = getattr(self.runtime, "model_label", None)
        if model_label is None:
            lm = getattr(self.runtime, "lm", None)
            model_label = str(lm) if lm is not None else None
        verbose_state = "on" if self.verbose_iterations else "off"
        if model_label:
            sub_label = getattr(self.runtime, "sub_model_label", None) or model_label
            self.console.print(
                Text.assemble(
                    ("model ", "dim"),
                    (model_label, "dim cyan"),
                    (" | sub ", "dim"),
                    (str(sub_label), "dim cyan"),
                    (" | verbose ", "dim"),
                    (verbose_state, "dim cyan"),
                )
            )
        else:
            self.console.print(
                Text.assemble(
                    ("verbose ", "dim"),
                    (verbose_state, "dim cyan"),
                )
            )
        self.console.print(
            Text(
                "Type /help for commands, /exit to quit. "
                "Alt+Enter inserts a newline.",
                style="dim",
            )
        )
        if self.update_notice:
            self.console.print(Text(self.update_notice, style="yellow"))

    async def handle_slash_command(self, message: str) -> bool:
        command, _, rest = message.partition(" ")
        rest = rest.strip()
        if command == "/resume":
            await self._handle_resume(rest)
            return True
        if command == "/help":
            self._handle_help()
            return True
        if command == "/sessions":
            await self._handle_sessions()
            return True
        if command == "/new":
            self._handle_new_session()
            return True
        if command == "/usage":
            self._handle_usage()
            return True
        if command in {"/provider", "/providers"}:
            return await self.handle_provider_command(rest)
        if command == "/model":
            return await self.handle_model_command(rest)
        if command == "/verbose":
            return self.handle_verbose_command(rest)
        if _looks_like_slash_command(message):
            self._print_command_output(
                Text(f"unknown command: {command} (try /help)", style="yellow")
            )
            return True
        return False

    async def _handle_resume(self, session_id: str) -> None:
        if not session_id:
            if self._uses_live_fixed_frame():
                await self._select_and_resume_session(
                    title="Resume session",
                    text="Choose a session to resume.",
                )
            else:
                self._print_sessions_table()
            return
        self._resume_session(session_id)

    def _resume_session(self, session_id: str) -> None:
        try:
            self.runtime.resume(session_id)
        except FileNotFoundError as exc:
            self._print_command_output(Text(str(exc), style="red"))
            return
        self._reset_rendered_state()
        self._print_command_output(
            Text(f"resumed session {self.runtime.session_id}", style="dim")
        )
        self.render_new_turns()

    def _handle_help(self) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(no_wrap=True)
        table.add_column()
        for command, description in SLASH_COMMANDS.items():
            table.add_row(Text(command, style="cyan"), Text(description, style="dim"))
        self._print_command_output(table)

    async def _handle_sessions(self) -> None:
        if self._uses_live_fixed_frame():
            await self._select_and_resume_session(
                title="Sessions",
                text="Choose a session to resume, or Esc to keep the current session.",
            )
            return
        self._print_sessions_table()

    def _print_sessions_table(self) -> None:
        sessions = list_sessions(self.runtime.workspace_path)
        if not sessions:
            self._print_command_output(
                Text("No stored sessions in this workspace.", style="dim")
            )
            return
        table = Table.grid(padding=(0, 2))
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True, justify="right")
        table.add_column(overflow="ellipsis", max_width=48)
        for info in sessions:
            marker = " (current)" if info.session_id == self.runtime.session_id else ""
            table.add_row(
                Text.assemble((info.session_id, "cyan"), (marker, "dim")),
                Text(f"{info.turn_count} turns", style="dim"),
                Text(info.first_message or "(empty)", style="dim"),
            )
        self._print_command_output(table)
        self._print_command_output(
            Text("Resume one with /resume <session-id>.", style="dim")
        )

    async def _select_and_resume_session(self, *, title: str, text: str) -> None:
        from fractal.onboarding import (
            InlineMenuChrome,
            MenuChoice,
            SetupInputError,
            _choose_from_menu_async,
        )

        sessions = list_sessions(self.runtime.workspace_path)
        if not sessions:
            self._print_command_output(
                Text("No stored sessions in this workspace.", style="dim")
            )
            return
        choices = [
            MenuChoice(
                value=info.session_id,
                label=info.session_id,
                detail=_session_choice_detail(info),
            )
            for info in sessions
        ]
        default = next(
            (
                info.session_id
                for info in sessions
                if info.session_id == self.runtime.session_id
            ),
            sessions[0].session_id,
        )
        try:
            session_id = await _choose_from_menu_async(
                title=title,
                text=text,
                choices=choices,
                default=default,
                menu_chrome=self._inline_menu_chrome(InlineMenuChrome),
            )
        except SetupInputError:
            self._print_command_output(Text("session selection canceled", style="dim"))
            return
        self._resume_session(session_id)

    def _handle_new_session(self) -> None:
        self.runtime.new_session()
        self._reset_rendered_state()
        self._print_command_output(
            Text(f"started new session {self.runtime.session_id}", style="dim")
        )

    def _handle_usage(self) -> None:
        totals = summarize_usage(self.runtime.session.summary_model)
        self._print_command_output(render_usage_report(totals))

    def _reset_rendered_state(self) -> None:
        self._rendered_turn_ids.clear()
        self._pending_turn_ids.clear()
        self._user_message_rendered_turn_ids.clear()

    async def handle_provider_command(self, rest: str) -> bool:
        if rest.strip():
            self._print_command_output(Text("usage: /provider", style="yellow"))
            return True

        if await self.run_provider_setup():
            self._print_command_output(
                Text(
                    "Provider updated for this session and saved as the default.",
                    style="dim",
                )
            )
            self._warn_project_override(
                ("active_provider", "active_model", "active_sub_model")
            )
        return True

    async def handle_model_command(self, rest: str) -> bool:
        if rest.strip():
            self._print_command_output(Text("usage: /model", style="yellow"))
            return True

        if await self.run_model_setup():
            self._print_command_output(
                Text(
                    f"Model updated to {self.runtime.model_label} "
                    f"(sub {self.runtime.sub_model_label}) for this session.",
                    style="dim",
                )
            )
            self._warn_project_override(("active_model", "active_sub_model"))
        return True

    def _warn_project_override(self, keys: tuple[str, ...]) -> None:
        """Warn when a project config will mask a change saved globally."""
        from fractal.config import load_project_config, project_config_path

        try:
            project = load_project_config(self.runtime.workspace_path)
        except Exception:
            return
        if project is None:
            return
        overridden = [key for key in keys if getattr(project, key, None) is not None]
        if not overridden:
            return
        path = project_config_path(self.runtime.workspace_path)
        self._print_command_output(
            Text(
                f"note: {path} overrides {', '.join(overridden)}; the saved "
                "default applies now but the project value wins on next launch.",
                style="yellow",
            )
        )

    def handle_verbose_command(self, rest: str) -> bool:
        mode = rest.strip().lower()
        if mode in {"", "toggle"}:
            self.verbose_iterations = not self.verbose_iterations
        elif mode == "on":
            self.verbose_iterations = True
        elif mode == "off":
            self.verbose_iterations = False
        else:
            self._print_command_output(
                Text("usage: /verbose [on|off]", style="yellow")
            )
            return True
        state = "on" if self.verbose_iterations else "off"
        self._print_command_output(
            Text(f"verbose iteration output {state}", style="dim")
        )
        self._print_command_output(
            Text(
                "applies to this session only; persist with "
                "`fractal config set defaults.verbose "
                f"{'true' if self.verbose_iterations else 'false'}`",
                style="dim",
            )
        )
        return True

    async def run_provider_setup(self) -> bool:
        from fractal.config import FractalConfigError, write_config
        from fractal.onboarding import (
            InlineMenuChrome,
            SetupInputError,
            async_prompt_for_config,
        )
        from fractal.providers import ProviderError
        from fractal.runtime_lms import selection_from_config, sub_selection_from_config

        try:
            existing = _existing_config()
            config = await async_prompt_for_config(
                stdin=self.config_stdin,
                stdout=self.config_stdout,
                existing=existing,
                menu_chrome=self._inline_menu_chrome(InlineMenuChrome),
            )
            selection = selection_from_config(config)
            sub_selection = sub_selection_from_config(config)
            self.runtime.apply_provider_selection(
                selection,
                sub_selection=sub_selection,
            )
            path = write_config(config)
        except (FractalConfigError, ProviderError, SetupInputError, ValueError) as exc:
            print(f"fractal provider setup: {exc}", file=self.config_stderr)
            print(
                "No config was written. Fix the issue, then run "
                "`/provider` again.",
                file=self.config_stderr,
            )
            return False

        print(f"Fractal config written to {path}", file=self.config_stdout)
        return True

    async def run_model_setup(self) -> bool:
        from fractal.config import FractalConfigError, load_config, write_config
        from fractal.onboarding import (
            InlineMenuChrome,
            SetupInputError,
            async_prompt_for_model,
            async_prompt_for_sub_model,
        )
        from fractal.providers import ProviderError, get_provider
        from fractal.runtime_lms import selection_from_config, sub_selection_from_config

        try:
            result = load_config()
            if result.config is None:
                raise SetupInputError("no config found; run `/provider` first")
            provider = get_provider(result.config.active_provider)
            model = await async_prompt_for_model(
                provider=provider,
                stdin=self.config_stdin,
                stdout=self.config_stdout,
                menu_chrome=self._inline_menu_chrome(InlineMenuChrome),
            )
            # The sub-model may live on its own provider; /model changes the
            # models only, /provider changes the providers.
            sub_provider_id = (
                result.config.active_sub_provider or result.config.active_provider
            )
            sub_model = await async_prompt_for_sub_model(
                provider=get_provider(sub_provider_id),
                main_model=model,
                stdin=self.config_stdin,
                stdout=self.config_stdout,
                current=result.config.active_sub_model,
                allow_same=result.config.active_sub_provider is None,
                menu_chrome=self._inline_menu_chrome(InlineMenuChrome),
            )
            config = result.config.model_copy(
                update={"active_model": model, "active_sub_model": sub_model}
            )
            selection = selection_from_config(config, path=result.path)
            sub_selection = sub_selection_from_config(config, path=result.path)
            self.runtime.apply_provider_selection(
                selection, sub_selection=sub_selection
            )
            path = write_config(config, path=result.path)
        except (FractalConfigError, ProviderError, SetupInputError, ValueError) as exc:
            print(f"fractal model setup: {exc}", file=self.config_stderr)
            print(
                "No config was written. Fix the issue, then run `/model` again.",
                file=self.config_stderr,
            )
            return False

        print(f"Fractal config written to {path}", file=self.config_stdout)
        return True

    def _inline_menu_chrome(self, chrome_type: object) -> object | None:
        if self.input_stream is not None or not self.console.is_terminal:
            return None
        self._reset_live_scroll_region()
        return chrome_type(frame=self._setup_menu_frame_container, style=PROMPT_STYLE)

    def render_new_turns(self) -> None:
        for turn in self.runtime.session.summary_model.turns:
            if turn.turn_id in self._rendered_turn_ids:
                continue
            if turn.agent is None:
                if turn.turn_id not in self._pending_turn_ids:
                    if turn.turn_id not in self._user_message_rendered_turn_ids:
                        self.render_turn(turn, pending=True)
                    self._pending_turn_ids.add(turn.turn_id)
                continue
            if turn.turn_id in self._pending_turn_ids:
                self.console.print(Rule(style="dim"))
                self.console.print(render_agent_response(turn))
                self._pending_turn_ids.remove(turn.turn_id)
            elif turn.turn_id in self._user_message_rendered_turn_ids:
                self.console.print(Rule(style="dim"))
                self.console.print(render_agent_response(turn))
            else:
                self.render_turn(turn)
            self._rendered_turn_ids.add(turn.turn_id)

    def render_turn(self, turn: SummaryTurn, *, pending: bool = False) -> None:
        self.console.print(render_user_message(turn.user.message))
        self.console.print(Rule(style="dim"))
        self.console.print(render_agent_response(turn, pending=pending))

    def render_latest_user_message(self) -> None:
        if self.runtime.session.summary_model.turns:
            turn = self.runtime.session.summary_model.turns[-1]
            if turn.turn_id not in self._user_message_rendered_turn_ids:
                self.console.print(render_user_message(turn.user.message))
                self._user_message_rendered_turn_ids.add(turn.turn_id)

    async def read_message(self) -> str | None:
        self.console.print()
        if self.input_stream is None:
            self._reset_live_scroll_region()
            try:
                message = await self.prompt_session.prompt_async(
                    self._input_prompt_fragments(),
                    handle_sigint=False,
                    wrap_lines=True,
                )
            except (EOFError, KeyboardInterrupt):
                self._reset_live_scroll_region()
                self.console.print()
                return None
            message = message.strip()
            if _will_submit_turn(message):
                self._pin_live_input_frame(body=RUNNING_FRAME_TEXT)
                self._sigint_mode = "turn"
            else:
                self._reset_live_scroll_region()
            return message

        try:
            message = await asyncio.to_thread(self._readline)
        except EOFError:
            self.console.print()
            return None
        message = message.strip()
        if _will_submit_turn(message):
            self._sigint_mode = "turn"
        return message

    def _readline(self) -> str:
        assert self.input_stream is not None
        self.console.print(render_prompt_label(), end="")
        line = self.input_stream.readline()
        if line == "":
            raise EOFError
        return line

    def _pin_live_input_frame(self, *, body: str | None = None) -> None:
        if self.input_stream is not None or not self.console.is_terminal:
            return
        height = max(self.console.height, FIXED_INPUT_ROWS + 1)
        width = max(self.console.width, 12)
        rows = self._static_input_frame_rows(width, body=body)
        stream = getattr(self.console, "file", sys.stdout)
        stream.write("\x1b[?25l\x1b[r")
        top_row = height - len(rows) + 1
        for index, row in enumerate(rows):
            stream.write(f"\x1b[{top_row + index};1H\x1b[2K{row}")
        scroll_bottom = max(top_row - 1, 1)
        stream.write(f"\x1b[1;{scroll_bottom}r")
        stream.write(f"\x1b[{scroll_bottom};1H")
        stream.flush()

    def _reset_live_scroll_region(self) -> None:
        if self.input_stream is not None or not self.console.is_terminal:
            return
        stream = getattr(self.console, "file", sys.stdout)
        stream.write("\x1b[?25h\x1b[r")
        stream.flush()

    def _static_input_frame_rows(
        self,
        width: int,
        *,
        body: str | None = None,
    ) -> list[str]:
        title = self._input_frame_title_text()
        inner_width = max(width - 2, 1)
        display_title = title[-inner_width:]
        top_fill = "─" * max(inner_width - len(display_title), 0)
        prompt = f"{self._prompt_icon()} " if body is None else body
        middle_body = prompt[:inner_width].ljust(inner_width)
        return [
            f"╭{top_fill}{display_title}╮",
            f"│{middle_body}│",
            f"╰{'─' * inner_width}╯",
        ]

    def _input_frame_title_text(self) -> str:
        tokens = self._next_context_tokens()
        if tokens is None:
            return " ctx -- "
        return f" ctx {_context_level_bar(tokens)} ~{_format_token_count(tokens)} "


def render_summary(summary: SessionSummary) -> Group:
    rendered: list[object] = []
    for index, turn in enumerate(summary.turns):
        if index > 0:
            rendered.append(Rule(style="dim"))
        rendered.append(render_user_message(turn.user.message))
        rendered.append(Rule(style="dim"))
        rendered.append(render_agent_response(turn))
    return Group(*rendered)


def render_trace_summary(trace: RunTrace, *, verbose: bool = False) -> Group:
    rendered: list[object] = []
    if trace.steps:
        rendered.append("")
    for index, step in enumerate(trace.steps):
        if index > 0:
            rendered.append("")
        rendered.append(
            render_trace_step(
                step,
                max_iterations=trace.max_iterations,
                verbose=verbose,
            )
        )
    if not rendered:
        rendered.append(Text("No RLM iteration trace captured.", style="dim italic"))
    return Group(*rendered)


def render_iteration_event_log(
    event: FractalIterationEvent,
    *,
    verbose: bool = False,
) -> Group:
    return Group(
        "",
        render_trace_step(
            event.step,
            max_iterations=event.max_iterations,
            verbose=verbose,
        ),
    )


def render_trace_step(
    step: IterationStep,
    *,
    max_iterations: int,
    verbose: bool = False,
) -> Group:
    code = step.code
    output = step.untruncated_output or step.output
    model_output = step.output
    reasoning = step.reasoning.strip()
    iteration = step.iteration
    status = "error" if step.error else "ok"

    text = Text()
    text.append(
        f"RLM turn {iteration}/{max_iterations} ",
        style="bold bright_black",
    )
    text.append(f"({status})", style="red" if status == "error" else "dim")
    rendered: list[object] = [text]
    if reasoning:
        rendered.append(render_reasoning(reasoning))
    rendered.append(
        Padding(
            Text(
                f"python: {_line_count(code)} lines\noutput: {len(output)} chars",
                style="dim",
            ),
            (0, 0, 0, 2),
        )
    )
    if verbose:
        rendered.append(render_trace_detail("code:", code, syntax="python"))
        rendered.append(render_trace_detail("output:", model_output))
    return Group(*rendered)


def render_trace_detail(label: str, body: str, *, syntax: str | None = None) -> Group:
    if body:
        content: Text | Syntax = (
            Syntax(
                body,
                syntax,
                background_color="default",
                line_numbers=False,
                word_wrap=True,
            )
            if syntax is not None
            else Text(body, style="dim")
        )
    else:
        content = Text("(empty)", style="dim italic")
    return Group(
        Padding(Text(label, style="dim italic"), (0, 0, 0, 2)),
        Padding(content, (0, 0, 0, 4)),
    )


def render_reasoning(reasoning: str) -> Padding:
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(no_wrap=True)
    table.add_column(ratio=1)
    table.add_row(
        Text("reasoning:", style="dim italic"),
        Text(reasoning, style="dim italic"),
    )
    return Padding(table, (0, 0, 0, 2))


def render_runtime_event_log(event: FractalRuntimeEvent) -> Text:
    style = RUNTIME_EVENT_STYLES.get(event.kind, "cyan")
    text = Text.assemble(
        ("  ", "dim"),
        (event.message, style),
    )
    return text


def render_user_message(message: str) -> Group:
    return Group(
        "",
        Text.assemble(render_user_message_label(), (message, "bold")),
    )


def render_user_message_label() -> Text:
    return Text.assemble((USER_MESSAGE_LABEL, "bold #8b5cf6"), (" ", "bright_black"))


def render_prompt_label() -> Text:
    return Text.assemble((_prompt_icon(), "bold #8b5cf6"), " ")


def _session_choice_detail(info: object) -> str:
    turn_count = getattr(info, "turn_count", 0)
    unit = "turn" if turn_count == 1 else "turns"
    first_message = " ".join(str(getattr(info, "first_message", "") or "(empty)").split())
    if len(first_message) > 72:
        first_message = f"{first_message[:69]}..."
    return f"{turn_count} {unit} · {first_message}"


def _existing_config() -> object | None:
    from fractal.config import FractalConfigError, load_config

    try:
        return load_config().config
    except FractalConfigError:
        return None


def _will_submit_turn(message: str) -> bool:
    if not message:
        return False
    return not _looks_like_slash_command(message)


def _looks_like_slash_command(message: str) -> bool:
    command, _, _ = message.partition(" ")
    if command in SLASH_COMMANDS:
        return True
    # A leading "/word" reads as a command attempt; absolute paths and other
    # slash-containing prompts fall through to the agent.
    return bool(re.fullmatch(r"/[A-Za-z][A-Za-z0-9_-]*", command))


def render_agent_message(turn: SummaryTurn, *, pending: bool = False) -> object:
    if pending or turn.agent is None:
        return Text("Running...", style="italic dim")
    elif turn.agent.status == "failed":
        return Text(turn.agent.error or "Turn failed.", style="red")
    elif turn.agent.status == "interrupted":
        return Text(turn.agent.error or "Turn interrupted by user.", style="yellow")
    elif turn.agent.status == "max_iterations":
        # PredictRLM's fallback can contain useful work, but the agent did not
        # explicitly SUBMIT it. Make that state visible in scrollback.
        response: Text | Markdown
        if turn.agent.response:
            response = FractalMarkdown(turn.agent.response)
        else:
            response = Text("No fallback response.", style="dim")
        body = Group(
            Text("Reached max iterations; showing fallback response.", style="yellow"),
            "",
            response,
        )
        return body
    else:
        return FractalMarkdown(turn.agent.response)


def render_agent_response(turn: SummaryTurn, *, pending: bool = False) -> Padding:
    return Padding(render_agent_message(turn, pending=pending), (0, 0, 0, 2))


def render_turn_footer(result: FractalResult) -> Text:
    if result.trace is not None and result.trace.status == "max_iterations":
        footer = Text("! max iterations", style="yellow")
    else:
        footer = Text("✓ complete")
    usage = turn_usage_from_trace(result.trace)
    if usage is None:
        return footer
    parts: list[str] = []
    if usage.iterations:
        unit = "iteration" if usage.iterations == 1 else "iterations"
        parts.append(f"{usage.iterations} {unit}")
    if usage.duration_ms:
        parts.append(_format_duration(usage.duration_ms))
    if usage.input_tokens or usage.output_tokens:
        parts.append(
            f"{_format_tokens(usage.input_tokens)} in / "
            f"{_format_tokens(usage.output_tokens)} out"
        )
    if usage.context_tokens:
        parts.append(f"{_format_tokens(usage.context_tokens)} ctx")
    for part in parts:
        footer.append(" · ", style="dim")
        footer.append(part, style="dim")
    return footer


def render_changed_files(changed_files: list[str]) -> Text:
    text = Text("  changed: ", style="dim")
    text.append(", ".join(changed_files), style="yellow")
    return text


def render_usage_report(totals: TurnUsage) -> Group:
    if not (totals.input_tokens or totals.output_tokens or totals.iterations):
        return Group(Text("No recorded usage for this session yet.", style="dim"))
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column(justify="right")
    table.add_row(Text("input tokens", style="dim"), Text(f"{totals.input_tokens:,}"))
    table.add_row(Text("output tokens", style="dim"), Text(f"{totals.output_tokens:,}"))
    if totals.context_tokens:
        table.add_row(
            Text("current context", style="dim"),
            Text(f"~{totals.context_tokens:,} tokens"),
        )
    table.add_row(Text("iterations", style="dim"), Text(f"{totals.iterations:,}"))
    table.add_row(
        Text("agent time", style="dim"), Text(_format_duration(totals.duration_ms))
    )
    table.add_row(Text("cost", style="dim"), Text(f"${totals.cost:.4f}"))
    return Group(table)


def _format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _format_duration(duration_ms: int) -> str:
    if duration_ms < 1_000:
        return f"{duration_ms}ms"
    seconds = duration_ms / 1_000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m {seconds - minutes * 60:.0f}s"


def _line_count(text: str) -> int:
    return len(text.splitlines()) if text else 0
