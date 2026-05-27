from __future__ import annotations

import os


def test_runtime_event_tracker_records_file_reads_and_writes() -> None:
    from fractal.events import RuntimeEventTracker

    tracker = RuntimeEventTracker()

    opened = tracker.observe({
        "target": "builtins.open",
        "phase": "before",
        "args": ["README.md", "r"],
    })
    tracker.observe({
        "target": "builtins.open",
        "phase": "after",
        "args": ["README.md", "r"],
    })
    write = tracker.observe({
        "target": "pathlib.Path.write_text",
        "phase": "before",
        "args": ["README.md", "updated"],
    })
    tracker.observe({
        "target": "pathlib.Path.write_text",
        "phase": "after",
        "args": ["README.md", "updated"],
    })

    assert opened is not None
    assert opened.message == "opening README.md"
    assert write is not None
    assert write.message == "editing README.md"
    assert tracker.files_read == ["README.md"]
    assert tracker.files_modified == ["README.md"]


def test_runtime_event_tracker_suppresses_nested_path_open_events() -> None:
    from fractal.events import RuntimeEventTracker

    tracker = RuntimeEventTracker()

    events = [
        tracker.observe({
            "target": "pathlib.Path.read_text",
            "phase": "before",
            "args": ["README.md"],
        }),
        tracker.observe({
            "target": "pathlib.Path.open",
            "phase": "before",
            "args": ["README.md", "r"],
        }),
        tracker.observe({
            "target": "pathlib.Path.open",
            "phase": "after",
            "args": ["README.md", "r"],
        }),
        tracker.observe({
            "target": "pathlib.Path.read_text",
            "phase": "after",
            "args": ["README.md"],
        }),
    ]

    assert [event.message for event in events if event is not None] == [
        "reading README.md"
    ]
    assert tracker.files_read == ["README.md"]


def test_runtime_event_tracker_maps_os_file_descriptors() -> None:
    from fractal.events import RuntimeEventTracker

    tracker = RuntimeEventTracker()

    tracker.observe({
        "target": "os.open",
        "phase": "after",
        "args": ["src/app.py", os.O_RDWR],
        "result": 7,
    })
    write = tracker.observe({
        "target": "os.pwrite",
        "phase": "before",
        "args": [7, "data", 0],
    })
    tracker.observe({
        "target": "os.pwrite",
        "phase": "after",
        "args": [7, "data", 0],
    })

    assert write is not None
    assert write.message == "editing src/app.py"
    assert tracker.files_modified == ["src/app.py"]


def test_runtime_event_tracker_records_subprocess_commands() -> None:
    from fractal.events import RuntimeEventTracker

    tracker = RuntimeEventTracker()

    event = tracker.observe({
        "target": "subprocess.run",
        "phase": "before",
        "args": [["uv", "run", "pytest"]],
    })

    assert event is not None
    assert event.message == "running uv run pytest"
    assert tracker.commands_run == ["uv run pytest"]


def test_runtime_event_tracker_suppresses_nested_subprocess_popen_events() -> None:
    from fractal.events import RuntimeEventTracker

    tracker = RuntimeEventTracker()

    events = [
        tracker.observe({
            "target": "subprocess.run",
            "phase": "before",
            "args": [["git", "status", "--short"]],
        }),
        tracker.observe({
            "target": "subprocess.Popen",
            "phase": "before",
            "args": [["git", "status", "--short"]],
        }),
        tracker.observe({
            "target": "subprocess.Popen",
            "phase": "after",
            "args": [["git", "status", "--short"]],
        }),
        tracker.observe({
            "target": "subprocess.run",
            "phase": "after",
            "args": [["git", "status", "--short"]],
        }),
    ]

    assert [event.message for event in events if event is not None] == [
        "running git status --short"
    ]
    assert tracker.commands_run == ["git status --short"]


def test_runtime_event_tracker_surfaces_direct_subprocess_popen_events() -> None:
    from fractal.events import RuntimeEventTracker

    tracker = RuntimeEventTracker()

    started = tracker.observe({
        "target": "subprocess.Popen",
        "phase": "before",
        "args": [["git", "status", "--short"]],
    })
    finished = tracker.observe({
        "target": "subprocess.Popen",
        "phase": "after",
        "args": [["git", "status", "--short"]],
    })

    assert started is not None
    assert started.message == "running git status --short"
    assert finished is None
    assert tracker.commands_run == ["git status --short"]


def test_runtime_event_tracker_surfaces_subprocess_failures() -> None:
    from fractal.events import RuntimeEventTracker

    tracker = RuntimeEventTracker()

    failed = tracker.observe({
        "target": "subprocess.run",
        "phase": "error",
        "args": [["git", "status", "--short"]],
    })

    assert failed is not None
    assert failed.message == "command failed: git status --short"


def test_runtime_event_tracker_truncates_long_command_messages() -> None:
    from fractal.events import MAX_COMMAND_DISPLAY_CHARS, RuntimeEventTracker

    tracker = RuntimeEventTracker()
    long_arg = "x" * 220
    full_command = f"python -c {long_arg} -- src/generated/output.txt"

    event = tracker.observe({
        "target": "subprocess.run",
        "phase": "before",
        "args": [["python", "-c", long_arg, "--", "src/generated/output.txt"]],
    })

    assert event is not None
    assert event.command == full_command
    assert tracker.commands_run == [full_command]
    assert event.message.startswith("running python -c ")
    assert event.message.endswith("src/generated/output.txt")
    assert "..." in event.message
    assert len(event.message) == len("running ") + MAX_COMMAND_DISPLAY_CHARS
