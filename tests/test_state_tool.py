import threading
import pytest
from tools.state.state_tool import StateTool


@pytest.fixture
def tool():
    return StateTool()


def test_set_and_get(tool):
    tool.set("x", 42)
    assert tool.get("x") == 42


def test_get_missing_key_returns_default(tool):
    assert tool.get("missing") is None
    assert tool.get("missing", default="fallback") == "fallback"


def test_has(tool):
    assert tool.has("x") is False
    tool.set("x", 1)
    assert tool.has("x") is True


def test_keys(tool):
    tool.set("a", 1)
    tool.set("b", 2)
    assert sorted(tool.keys()) == ["a", "b"]


def test_get_all_is_shallow_copy(tool):
    tool.set("k", [1, 2, 3])
    snapshot = tool.get_all()
    snapshot["new_key"] = 99
    assert tool.has("new_key") is False


def test_increment_from_zero(tool):
    assert tool.increment("counter") == 1
    assert tool.increment("counter") == 2


def test_increment_non_numeric_raises(tool):
    tool.set("s", "text")
    with pytest.raises(ValueError):
        tool.increment("s")


def test_delete(tool):
    tool.set("x", 1)
    tool.delete("x")
    assert tool.has("x") is False


def test_delete_missing_key_no_error(tool):
    tool.delete("nonexistent")


def test_clear(tool):
    tool.set("a", 1)
    tool.set("b", 2)
    tool.clear()
    assert tool.keys() == []


def test_namespace_isolation(tool):
    tool.set("x", 1, namespace="a")
    assert tool.get("x", namespace="b") is None


def test_thread_safety(tool):
    threads = [threading.Thread(target=tool.increment, args=("hits",)) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tool.get("hits") == 50


def test_get_all_shallow_copy_does_not_protect_mutable_values(tool):
    tool.set("k", [1, 2, 3])
    snapshot = tool.get_all()
    snapshot["k"].append(99)           # mutar el valor a través del snapshot
    assert tool.get("k") == [1, 2, 3]  # FALLA hoy — documenta el riesgo real


def test_increment_custom_amount(tool):
    result = tool.increment("counter", amount=5)
    assert result == 5
    result = tool.increment("counter", amount=3)
    assert result == 8


def test_increment_float_amount(tool):
    result = tool.increment("score", amount=1.5)
    assert result == 1.5
