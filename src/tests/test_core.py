import pytest
from mcp_server.core import (
    CLIENT_CAPABILITIES_KEY,
    HANDSHAKE_VERSIONS,
    PROTOCOL_VERSION_KEY,
    SERVER_INFO_KEY,
    STATELESS,
    Dispatcher,
    Session,
)

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

# The 2026-07-28 per-request envelope
ENVELOPE = {PROTOCOL_VERSION_KEY: "2026-07-28", CLIENT_CAPABILITIES_KEY: {}}


def initialized():
    """A dispatcher plus a session that has finished the initialize handshake."""
    dispatch = Dispatcher()
    session = Session()
    dispatch.handle_request(INITIALIZE, session)
    return dispatch, session


def call_tool(name, arguments, request_id=3):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


# --- Handshake ---

def test_initialize():
    dispatch = Dispatcher()
    response = dispatch.handle_request(INITIALIZE, Session())

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 0
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert response["result"]["serverInfo"]["name"] == "my-mcp-server"


@pytest.mark.parametrize("requested, expected", [
    ("2024-11-05", "2024-11-05"),            # supported: echo it back
    ("2025-11-25", "2025-11-25"),
    ("1999-01-01", HANDSHAKE_VERSIONS[-1]),  # unknown: offer our newest
    ("2026-07-28", HANDSHAKE_VERSIONS[-1]),  # stateless-era version can't be negotiated via initialize
])
def test_initialize_negotiates_version(requested, expected):
    message = {**INITIALIZE, "params": {**INITIALIZE["params"], "protocolVersion": requested}}
    response = Dispatcher().handle_request(message, Session())
    assert response["result"]["protocolVersion"] == expected


def test_initialize_needs_params():
    response = Dispatcher().handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, Session())
    assert response["error"]["code"] == -32602


def test_initialize_twice_is_rejected():
    dispatch, session = initialized()
    response = dispatch.handle_request(INITIALIZE, session)
    assert response["error"]["code"] == -32600


def test_requests_before_initialize_are_rejected():
    response = Dispatcher().handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, Session())
    assert response["error"]["code"] == -32600
    assert "not initialized" in response["error"]["message"]


def test_ping_works_before_initialize():
    response = Dispatcher().handle_request({"jsonrpc": "2.0", "id": 1, "method": "ping"}, Session())
    assert response["result"] == {}


def test_initialized_notification():
    dispatch, session = initialized()
    response = dispatch.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    assert response is None  # notifications never get a reply
    assert session.initialized


# testing for not found message

def test_unknownMethod():
    dispatch, session = initialized()
    make_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "unknownMethod"
    }
    response = dispatch.handle_request(make_request, session)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["error"]["code"] == -32601
    assert response["error"]["message"] == "Method not found"


# --- Malformed messages: always an error reply, never an exception ---

@pytest.mark.parametrize("message, code", [
    ([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], -32600),           # batch
    ({"jsonrpc": "2.0", "id": 1}, -32600),                               # no method
    ({"id": 1, "method": "ping"}, -32600),                               # no jsonrpc
    ({"jsonrpc": "2.0", "id": None, "method": "ping"}, -32600),          # null id
    ({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}, -32602),
    ({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1, 2]}, -32602),
    (call_tool(["not", "a", "string"], {}), -32602),
    (call_tool("add", "not an object"), -32602),
])
def test_malformed_messages(message, code):
    dispatch, session = initialized()
    response = dispatch.handle_request(message, session)
    assert response["error"]["code"] == code


def test_unexpected_bug_becomes_internal_error(monkeypatch):
    dispatch, session = initialized()

    def broken():
        raise RuntimeError("bug in list_tools")

    monkeypatch.setattr(dispatch, "list_tools", broken)
    response = dispatch.handle_request({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}, session)
    assert response["error"] == {"code": -32603, "message": "Internal error"}


# --- Tools ---

def test_unknownTool():
    dispatch, session = initialized()
    response = dispatch.handle_request(call_tool("unknownTool", {"a": 20, "b": 30}, request_id=2), session)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 2
    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "Unknown tool: unknownTool"


# 1. The Pytest Way (Data-Driven Testing for valid calls)
@pytest.mark.parametrize("tool_name, args, expected", [
    ("add", {"a": 20, "b": 30}, "50"),
    ("add", {"a": -5, "b": 5}, "0"),
    ("add", {"a": 0, "b": 0}, "0"),
])
def test_all_tools_valid(tool_name, args, expected):
    dispatch, session = initialized()
    response = dispatch.handle_request(call_tool(tool_name, args), session)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 3
    assert response["result"]["isError"] is False
    assert response["result"]["content"][0]["text"] == expected


# Bad arguments are a *tool* error (isError: true), so the model can read it and retry
@pytest.mark.parametrize("args", [
    {"a": "foo", "b": "bar"},  # used to return "foobar"
    {"a": "5", "b": 1},        # strings are not integers
    {"a": 1.5, "b": 1},
    {"a": True, "b": 1},
    {"a": 5},                  # b is required; used to silently default to 0
])
def test_add_rejects_invalid_arguments(args):
    dispatch, session = initialized()
    response = dispatch.handle_request(call_tool("add", args), session)
    assert response["result"]["isError"] is True
    assert "Invalid arguments" in response["result"]["content"][0]["text"]


# 2. The Dynamic Loop Way (Testing failure modes for ALL registered tools)
def test_all_tools_enforce_required_args():
    dispatch, session = initialized()
    for tool_name, tool in dispatch.registered_tools.items():
        if not tool["input_model"].model_json_schema().get("required"):
            continue
        response = dispatch.handle_request(call_tool(tool_name, {}), session)
        assert response["result"]["isError"] is True, tool_name


def test_tool_crash_is_a_tool_error():
    dispatch, session = initialized()

    def crash(args):
        raise ValueError("boom")

    dispatch.registered_tools["add"]["handler"] = crash
    response = dispatch.handle_request(call_tool("add", {"a": 1, "b": 2}), session)
    assert response["result"]["isError"] is True
    assert "boom" in response["result"]["content"][0]["text"]


def test_tools_list():
    dispatch, session = initialized()
    response = dispatch.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, session)
    assert response["jsonrpc"] == "2.0"
    tools = {t["name"]: t for t in response["result"]["tools"]}
    assert "add" in tools
    assert tools["add"]["inputSchema"]["type"] == "object"
    assert tools["add"]["inputSchema"]["required"] == ["a", "b"]


# --- Stateless era (2026-07-28) ---

def stateless(method, params=None, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {**(params or {}), "_meta": ENVELOPE},
    }


def test_discover_needs_no_handshake():
    response = Dispatcher().handle_request(stateless("server/discover"), Session())
    result = response["result"]
    assert result["supportedVersions"] == ["2026-07-28"]
    assert result["capabilities"] == {"tools": {}}
    assert result["resultType"] == "complete"
    assert result["_meta"][SERVER_INFO_KEY]["name"] == "my-mcp-server"


def test_stateless_tool_call_needs_no_handshake():
    params = {"name": "add", "arguments": {"a": 2, "b": 3}}
    response = Dispatcher().handle_request(stateless("tools/call", params), Session())
    assert response["result"]["content"][0]["text"] == "5"
    assert response["result"]["resultType"] == "complete"


def test_stateless_unsupported_version():
    message = stateless("tools/list")
    message["params"]["_meta"] = {**ENVELOPE, PROTOCOL_VERSION_KEY: "2099-01-01"}
    response = Dispatcher().handle_request(message, Session())
    assert response["error"]["code"] == -32022
    assert response["error"]["data"] == {"supported": ["2026-07-28"], "requested": "2099-01-01"}


def test_stateless_envelope_needs_capabilities():
    message = stateless("tools/list")
    message["params"]["_meta"] = {PROTOCOL_VERSION_KEY: "2026-07-28"}
    response = Dispatcher().handle_request(message, Session())
    assert response["error"]["code"] == -32602


def test_stateless_session_rejects_requests_without_envelope():
    response = Dispatcher().handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, Session(era=STATELESS)
    )
    assert response["error"]["code"] == -32602


def test_ping_does_not_exist_in_stateless_era():
    response = Dispatcher().handle_request(stateless("ping"), Session())
    assert response["error"]["code"] == -32601


def test_discover_does_not_exist_in_handshake_era():
    response = Dispatcher().handle_request({"jsonrpc": "2.0", "id": 1, "method": "server/discover"}, Session())
    assert response["error"]["code"] == -32601


def test_era_is_fixed_by_the_first_request():
    # A stateless session refuses initialize...
    dispatch, session = Dispatcher(), Session()
    dispatch.handle_request(stateless("server/discover"), session)
    response = dispatch.handle_request(INITIALIZE, session)
    assert response["error"]["code"] == -32022

    # ...and a handshake session refuses envelope requests
    dispatch, session = initialized()
    response = dispatch.handle_request(stateless("tools/list"), session)
    assert response["error"]["code"] == -32600
