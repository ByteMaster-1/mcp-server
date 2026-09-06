import pytest
from mcp_server.core import Dispatcher
def test_initialize():
    dispatch = Dispatcher()
    make_request={
        "jsonrpc":"2.0",
        "id":1,
        "method":"initialize"
    }
    response = dispatch.handle_request(make_request)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert "protocolVersion" in response["result"]
    assert response["result"]["serverInfo"]["name"] == "my-mcp-server"


# testing for not found message 

def test_unknownMethod():
    dispatch = Dispatcher()
    make_request={
        "jsonrpc":"2.0",
        "id":1,
        "method":"unknownMethod"
    }
    response = dispatch.handle_request(make_request)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["error"]["code"] == -32601
    assert response["error"]["message"] == "Method not found"

#test unknown tool 

def test_unknownTool():
    dispatch = Dispatcher()
    make_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "unknownTool",
            "arguments": {"a": 20, "b": 30}
        }
    }
    response = dispatch.handle_request(make_request)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 2
    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "Unknown tool: unknownTool"

# test valid tool 

# 1. The Pytest Way (Data-Driven Testing for valid calls)
@pytest.mark.parametrize("tool_name, args, expected", [
    ("add", {"a": 20, "b": 30}, "50"),
    ("add", {"a": -5, "b": 5}, "0"),
    ("add", {"a": 0, "b": 0}, "0"),
])
def test_all_tools_valid(tool_name, args, expected):
    dispatch = Dispatcher()
    make_request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": args
        }
    }
    response = dispatch.handle_request(make_request)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 3
    assert response["result"]["content"][0]["text"] == expected


# 2. The Dynamic Loop Way (Testing failure modes for ALL registered tools)
def test_all_tools_missing_args():
    dispatch = Dispatcher()
    for tool_name in dispatch.registered_tools.keys():
        make_request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {} # Intentionally passing empty arguments!
            }
        }
        response = dispatch.handle_request(make_request)
        
        # Depending on the tool, missing args might return a default value or crash.
        # But we assert that the server ALWAYS handles it gracefully (no unhandled exceptions!)
        assert "jsonrpc" in response
        assert "id" in response
        assert "result" in response or "error" in response


def test_all_tools_type_crash():
    dispatch = Dispatcher()
    for tool_name in dispatch.registered_tools.keys():
        make_request = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {"a": "bad", "b": 10} # String + Integer will crash the Python handler!
            }
        }
        response = dispatch.handle_request(make_request)
        
        # Because we passed a string and an integer, our python tool throws a TypeError.
        # Our try/except in core.py must catch it and return the internal error code -32603!
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 5
        assert response["error"]["code"] == -32603

def test_tools_list():
    dispatch = Dispatcher()
    make_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list"
    }
    response = dispatch.handle_request(make_request)
    assert response["jsonrpc"] == "2.0"
    assert "tools" in response["result"]
    tool_names = [t["name"] for t in response["result"]["tools"]]
    assert "add" in tool_names