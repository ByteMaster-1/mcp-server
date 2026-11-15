import json
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mcp_server.core import CLIENT_CAPABILITIES_KEY, PROTOCOL_VERSION_KEY
from mcp_server.transports import sse, streamable

SRC = Path(__file__).resolve().parents[1]

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    },
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


# --- stdio ---


def test_stdio_survives_bad_input():
    lines = [
        "not json",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}),
        json.dumps(INITIALIZE),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(TOOLS_LIST),
    ]
    result = subprocess.run(
        [sys.executable, "-m", "mcp_server.transports.stdio"],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        cwd=SRC,
        timeout=10,
        check=False,  # we assert on returncode ourselves below
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]

    assert result.returncode == 0
    assert responses[0]["error"]["code"] == -32700  # parse error, and the server kept going
    assert responses[1]["error"]["code"] == -32600  # tools/call before initialize
    assert responses[2]["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in responses[3]["result"]  # the notification got no reply
    assert len(responses) == 4


# --- Streamable HTTP ---


@pytest.fixture
def mcp():
    streamable.sessions.clear()
    return TestClient(streamable.app, base_url="http://127.0.0.1:8001")


def start_session(client):
    response = client.post("/mcp", json=INITIALIZE)
    assert response.status_code == 200
    return response.headers["mcp-session-id"]


def test_streamable_handshake(mcp):
    session_id = start_session(mcp)
    headers = {"Mcp-Session-Id": session_id}

    response = mcp.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    assert response.status_code == 202
    assert response.content == b""

    response = mcp.post("/mcp", json=TOOLS_LIST, headers=headers)
    assert response.status_code == 200
    assert "tools" in response.json()["result"]


def test_streamable_requires_a_session(mcp):
    assert mcp.post("/mcp", json=TOOLS_LIST).status_code == 400
    assert mcp.post("/mcp", json=TOOLS_LIST, headers={"Mcp-Session-Id": "nope"}).status_code == 404


def test_streamable_delete_ends_session(mcp):
    headers = {"Mcp-Session-Id": start_session(mcp)}
    assert mcp.delete("/mcp", headers=headers).status_code == 200
    assert mcp.post("/mcp", json=TOOLS_LIST, headers=headers).status_code == 404


def test_streamable_bad_input_gets_a_complete_reply(mcp):
    assert mcp.post("/mcp", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 400

    headers = {"Mcp-Session-Id": start_session(mcp)}
    response = mcp.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": None}, headers=headers
    )
    assert response.json()["error"]["code"] == -32602  # used to cut the connection mid-body


def test_streamable_get_is_not_offered(mcp):
    assert mcp.get("/mcp").status_code == 405


def stateless_headers(method, name=None):
    headers = {"Mcp-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if name:
        headers["Mcp-Name"] = name
    return headers


def stateless_body(method, params=None):
    meta = {PROTOCOL_VERSION_KEY: "2026-07-28", CLIENT_CAPABILITIES_KEY: {}}
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**(params or {}), "_meta": meta}}


def test_streamable_stateless_discover_and_call(mcp):
    response = mcp.post("/mcp", json=stateless_body("server/discover"), headers=stateless_headers("server/discover"))
    assert response.status_code == 200
    assert response.json()["result"]["supportedVersions"] == ["2026-07-28"]

    body = stateless_body("tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}})
    response = mcp.post("/mcp", json=body, headers=stateless_headers("tools/call", "add"))
    assert response.status_code == 200
    assert response.json()["result"]["content"][0]["text"] == "5"
    assert "mcp-session-id" not in response.headers  # no sessions in this era


def test_streamable_stateless_header_mismatch(mcp):
    body = stateless_body("tools/list")
    response = mcp.post("/mcp", json=body, headers=stateless_headers("tools/call"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


def test_streamable_stateless_errors_use_http_status(mcp):
    body = stateless_body("no/such/method")
    response = mcp.post("/mcp", json=body, headers=stateless_headers("no/such/method"))
    assert response.status_code == 404

    response = mcp.post("/mcp", json=TOOLS_LIST, headers={"Mcp-Protocol-Version": "2099-01-01"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32022


# --- Security (both HTTP transports) ---


@pytest.mark.parametrize("app, path", [(streamable.app, "/mcp"), (sse.app, "/messages?session_id=x")])
def test_other_websites_are_rejected(app, path):
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    assert client.post(path, json=TOOLS_LIST, headers={"Origin": "https://evil.example"}).status_code == 403

    # DNS rebinding: the request reaches us, but the Host header gives it away
    rebinding = TestClient(app, base_url="http://evil.example:8000")
    assert rebinding.post(path, json=TOOLS_LIST).status_code == 421


@pytest.mark.parametrize("app, path", [(streamable.app, "/mcp"), (sse.app, "/messages")])
def test_cors_only_allows_local_pages(app, path):
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    preflight = {"Access-Control-Request-Method": "POST"}

    evil = client.options(path, headers={**preflight, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers

    inspector = client.options(path, headers={**preflight, "Origin": "http://localhost:6274"})
    assert inspector.headers["access-control-allow-origin"] == "http://localhost:6274"
    assert "access-control-allow-credentials" not in inspector.headers


# --- HTTP + SSE ---


@pytest.fixture
def sse_session():
    sse.sessions.clear()
    session = sse.SseSession()
    sse.sessions["test"] = session
    return TestClient(sse.app, base_url="http://127.0.0.1:8000"), session


def test_sse_response_goes_to_the_stream(sse_session):
    client, session = sse_session
    response = client.post("/messages?session_id=test", json=INITIALIZE)
    assert response.status_code == 202
    assert session.queue.get_nowait()["result"]["protocolVersion"] == "2025-06-18"


def test_sse_bad_requests(sse_session):
    client, _ = sse_session
    assert client.post("/messages", json=TOOLS_LIST).status_code == 400
    assert client.post("/messages?session_id=nope", json=TOOLS_LIST).status_code == 404
    response = client.post(
        "/messages?session_id=test", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
