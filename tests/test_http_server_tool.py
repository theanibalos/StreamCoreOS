import pytest
from httpx import AsyncClient, ASGITransport
from tools.http_server.http_server_tool import HttpServerTool, HttpContext

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
def tool():
    t = HttpServerTool()
    # No necesitamos setup() completo ni arrancar uvicorn para probar la app de FastAPI
    return t

@pytest.fixture
async def client(tool):
    async with AsyncClient(transport=ASGITransport(app=tool.app), base_url="http://test") as ac:
        yield ac

async def test_data_merging_intent(tool, client):
    """
    La intención del Gateway es ser transparente y combinar todos los datos
    de entrada (path, query, body) en un único diccionario 'data'.
    """
    received_data = {}

    async def handler(data, context):
        received_data.update(data)
        return {"success": True}

    tool.add_endpoint("/test/{id}", "POST", handler)
    tool._register_all_endpoints()

    await client.post("/test/42?query_param=val", json={"body_param": "data"})

    assert received_data.get("id") == "42"
    assert received_data.get("query_param") == "val"
    assert received_data.get("body_param") == "data"

async def test_auth_injection_intent(tool, client):
    """
    La intención de seguridad es que si hay un validador, este inyecte
    su resultado en data['_auth'] de forma automática.
    """
    async def mock_validator(token):
        if token == "valid-token":
            return {"user_id": 123}
        return None

    async def handler(data, context):
        return {"success": True, "data": {"user": data.get("_auth")}}

    tool.add_endpoint("/secure", "GET", handler, auth_validator=mock_validator)
    tool._register_all_endpoints()

    # Intento sin token
    resp = await client.get("/secure")
    assert resp.status_code == 401
    assert resp.json()["success"] is False

    # Intento con token válido
    resp = await client.get("/secure", headers={"Authorization": "Bearer valid-token"})
    assert resp.status_code == 200
    assert resp.json()["data"]["user"]["user_id"] == 123

async def test_http_context_manipulation_intent(tool, client):
    """
    La intención de HttpContext es permitir que el plugin controle
    la respuesta (status, headers, cookies) sin acoplarse a FastAPI.
    """
    async def handler(data, context: HttpContext):
        context.set_status(201)
        context.set_header("X-Custom", "Value")
        context.set_cookie("test_cookie", "yum")
        return {"success": True}

    tool.add_endpoint("/context", "GET", handler)
    tool._register_all_endpoints()

    resp = await client.get("/context")
    assert resp.status_code == 201
    assert resp.headers["X-Custom"] == "Value"
    assert "test_cookie=yum" in resp.headers.get("set-cookie", "")

async def test_binary_response_intent(tool, client):
    """
    La intención es que el plugin pueda devolver datos crudos (ej. imágenes)
    saltándose el sobre de JSON estándar.
    """
    async def handler(data, context: HttpContext):
        context.set_binary_response(b"raw-data", media_type="text/plain")
        return {"success": True} # Será ignorado por el tool

    tool.add_endpoint("/binary", "GET", handler)
    tool._register_all_endpoints()

    resp = await client.get("/binary")
    assert resp.status_code == 200
    assert resp.content == b"raw-data"
    assert "text/plain" in resp.headers["content-type"]

async def test_unhandled_exception_safety_intent(tool, client):
    """
    La intención de resiliencia es que fallos en el plugin no rompan el server
    y devuelvan un error 500 consistente al cliente.
    """
    async def handler(data, context):
        raise RuntimeError("Oops!")

    tool.add_endpoint("/fail", "GET", handler)
    tool._register_all_endpoints()

    resp = await client.get("/fail")
    assert resp.status_code == 500
    assert resp.json()["success"] is False
    assert "Internal server error" in resp.json()["error"]
