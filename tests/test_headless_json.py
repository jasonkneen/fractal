import json


def test_headless_result_to_json_roundtrips() -> None:
    from fractal.session import HeadlessResult, TurnUsage

    result = HeadlessResult(
        session_id="abc",
        workspace="/tmp/ws",
        status="succeeded",
        response="done",
        changed_files=["a.py", "b.py"],
        usage=TurnUsage(input_tokens=10, output_tokens=5, cost=0.01, iterations=2),
    )
    data = json.loads(result.to_json())

    assert data["session_id"] == "abc"
    assert data["workspace"] == "/tmp/ws"
    assert data["status"] == "succeeded"
    assert data["response"] == "done"
    assert data["changed_files"] == ["a.py", "b.py"]
    assert data["usage"]["input_tokens"] == 10
    assert data["usage"]["iterations"] == 2
    assert data["error"] is None


def test_headless_result_from_turn_without_trace() -> None:
    from fractal.session import headless_result_from_turn

    result = headless_result_from_turn(
        session_id="s",
        workspace="/w",
        response="hi",
        changed_files=[],
        trace=None,
    )

    assert result.status == "succeeded"
    assert result.usage is None
    assert result.response == "hi"
    # serializes cleanly even with no usage
    assert json.loads(result.to_json())["usage"] is None


def test_json_flag_parses() -> None:
    from fractal.cli import build_parser

    args = build_parser().parse_args(["-p", "do it", "--json"])
    assert args.json is True
    assert args.prompt == "do it"
