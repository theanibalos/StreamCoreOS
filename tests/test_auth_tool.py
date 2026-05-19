import pytest
from tools.auth.auth_tool import AuthTool

SECRET = "test-secret-key-32chars-long-ok!"


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", SECRET)
    return AuthTool()


def test_hash_password_differs_from_original(tool):
    h = tool.hash_password("password123")
    assert h != "password123"


def test_hash_password_is_salted(tool):
    h1 = tool.hash_password("password123")
    h2 = tool.hash_password("password123")
    assert h1 != h2


def test_verify_password_correct(tool):
    h = tool.hash_password("password123")
    assert tool.verify_password("password123", h) is True


def test_verify_password_wrong(tool):
    h = tool.hash_password("password123")
    assert tool.verify_password("wrong", h) is False


def test_create_token_returns_string(tool):
    token = tool.create_token({"sub": "1"})
    assert isinstance(token, str) and len(token) > 0


def test_decode_token_returns_payload(tool):
    token = tool.create_token({"sub": "1"})
    payload = tool.decode_token(token)
    assert payload["sub"] == "1"


def test_decode_invalid_token_raises(tool):
    with pytest.raises(Exception, match="Invalid token"):
        tool.decode_token("token_invalido")


def test_decode_expired_token_raises(tool):
    token = tool.create_token({"sub": "1"}, expires_delta=-1)
    with pytest.raises(Exception, match="expired"):
        tool.decode_token(token)


def test_validate_token_valid_returns_dict(tool):
    token = tool.create_token({"sub": "1"})
    result = tool.validate_token(token)
    assert result is not None
    assert result["sub"] == "1"


def test_validate_token_invalid_returns_none(tool):
    assert tool.validate_token("basura") is None


def test_decode_token_with_wrong_signature_raises(tool):
    import jwt as _jwt
    from datetime import datetime, timedelta, timezone
    fake_token = _jwt.encode(
        {"sub": "attacker", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "completamente-diferente-secret-key-32",
        algorithm="HS256",
    )
    with pytest.raises(Exception, match="Invalid token"):
        tool.decode_token(fake_token)


def test_create_token_without_expires_delta_has_expiry_claim(tool):
    token = tool.create_token({"sub": "1"})
    import jwt as _jwt
    payload = _jwt.decode(token, options={"verify_signature": False})
    assert "exp" in payload
    assert payload["exp"] > 0


def test_init_without_secret_raises(monkeypatch):
    monkeypatch.delenv("AUTH_SECRET_KEY", raising=False)
    with pytest.raises(EnvironmentError):
        AuthTool()

def test_init_with_short_secret_raises(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "too-short")
    with pytest.raises(ValueError, match="at least 32 characters"):
        AuthTool()
