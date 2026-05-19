import pytest
from tools.logger.logger_tool import LoggerTool


@pytest.fixture
def tool():
    return LoggerTool()


def test_info_does_not_raise(tool, capsys):
    tool.info("hello")
    out = capsys.readouterr().out
    assert "hello" in out


def test_error_does_not_raise(tool, capsys):
    tool.error("boom")
    out = capsys.readouterr().out
    assert "boom" in out


def test_warning_does_not_raise(tool, capsys):
    tool.warning("watch out")
    out = capsys.readouterr().out
    assert "watch out" in out


def test_sink_receives_all_args(tool):
    received = []
    tool.add_sink(lambda level, msg, ts, identity: received.append((level, msg, ts, identity)))
    tool.info("test message")
    assert len(received) == 1
    level, msg, ts, identity = received[0]
    assert msg == "test message"
    assert ts != ""


def test_sink_receives_info_level(tool):
    received = []
    tool.add_sink(lambda level, msg, ts, identity: received.append(level))
    tool.info("x")
    assert received[0] == "INFO"


def test_sink_exception_is_contained(tool):
    def bad_sink(level, msg, ts, identity):
        raise RuntimeError("sink failure")

    tool.add_sink(bad_sink)
    tool.info("should not crash")


def test_duplicate_sink_called_once(tool):
    calls = []
    cb = lambda level, msg, ts, identity: calls.append(1)
    tool.add_sink(cb)
    tool.add_sink(cb)
    tool.info("msg")
    assert len(calls) == 1


def test_multiple_sinks_all_called(tool):
    a, b = [], []
    tool.add_sink(lambda l, m, t, i: a.append(1))
    tool.add_sink(lambda l, m, t, i: b.append(1))
    tool.info("broadcast")
    assert len(a) == 1
    assert len(b) == 1


def test_sink_timestamp_is_iso_format(tool):
    received = []
    tool.add_sink(lambda level, msg, ts, identity: received.append(ts))
    tool.info("msg")
    ts = received[0]
    from datetime import datetime
    parsed = datetime.fromisoformat(ts)
    assert parsed.year >= 2024


def test_sink_receives_warn_level(tool):
    received = []
    tool.add_sink(lambda level, msg, ts, identity: received.append(level))
    tool.warning("x")
    assert received[0] == "WARN"


def test_sink_receives_error_level(tool):
    received = []
    tool.add_sink(lambda level, msg, ts, identity: received.append(level))
    tool.error("boom")
    assert received[0] == "ERROR"
