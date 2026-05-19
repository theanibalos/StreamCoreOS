import pytest
from tools.config.config_tool import ConfigTool


@pytest.fixture
def tool():
    return ConfigTool()


def test_get_existing_key(tool, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "hello")
    assert tool.get("TEST_KEY") == "hello"


def test_get_missing_key_returns_none(tool, monkeypatch):
    monkeypatch.delenv("TEST_MISSING_KEY", raising=False)
    assert tool.get("TEST_MISSING_KEY") is None


def test_get_missing_key_with_default(tool, monkeypatch):
    monkeypatch.delenv("TEST_MISSING_KEY", raising=False)
    assert tool.get("TEST_MISSING_KEY", default="fallback") == "fallback"


def test_get_required_missing_raises(tool, monkeypatch):
    monkeypatch.delenv("TEST_MISSING_KEY", raising=False)
    with pytest.raises(EnvironmentError):
        tool.get("TEST_MISSING_KEY", required=True)


def test_get_required_existing_returns_value(tool, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "value")
    assert tool.get("TEST_KEY", required=True) == "value"


def test_require_all_present(tool, monkeypatch):
    monkeypatch.setenv("REQ_A", "1")
    monkeypatch.setenv("REQ_B", "2")
    tool.require("REQ_A", "REQ_B")


def test_require_one_missing_raises(tool, monkeypatch):
    monkeypatch.setenv("REQ_A", "1")
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(EnvironmentError, match="MISSING_VAR"):
        tool.require("REQ_A", "MISSING_VAR")


def test_require_all_missing_mentions_all(tool, monkeypatch):
    monkeypatch.delenv("MISS_X", raising=False)
    monkeypatch.delenv("MISS_Y", raising=False)
    monkeypatch.delenv("MISS_Z", raising=False)
    with pytest.raises(EnvironmentError) as exc_info:
        tool.require("MISS_X", "MISS_Y", "MISS_Z")
    msg = str(exc_info.value)
    assert "MISS_X" in msg
    assert "MISS_Y" in msg
    assert "MISS_Z" in msg


def test_get_required_empty_string_does_not_raise(tool, monkeypatch):
    # Documenta el comportamiento actual: get(required=True) con KEY="" retorna ""
    # sin lanzar, porque la guardia es `value is None` — no detecta strings vacíos.
    monkeypatch.setenv("EMPTY_KEY", "")
    result = tool.get("EMPTY_KEY", required=True)
    assert result == ""


def test_require_empty_string_raises(tool, monkeypatch):
    # require() usa `not os.environ.get(k)` — string vacío es falsy, por lo que SÍ lanza.
    # Inconsistencia documentada: get(required=True) con "" pasa, require() con "" falla.
    monkeypatch.setenv("EMPTY_KEY", "")
    with pytest.raises(EnvironmentError):
        tool.require("EMPTY_KEY")
