class Dispatcher:
    """The transport-agnostic core. It doesn't know about stdio or HTTP.
    It just takes a JSON dictionary, processes it, and returns a JSON dictionary."""
    
    def handle_request(self, message: dict) -> dict:
        request_id = message.get("id")
        method = message.get("method")
        
        # 1. Handshake
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "my-mcp-server", "version": "1.0.0"}
                }
            }
            
        # 2. Tool Discovery
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "add",
                            "description": "Add two numbers together.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "integer"},
                                    "b": {"type": "integer"}
                                },
                                "required": ["a", "b"]
                            }
                        }
                    ]
                }
            }
            
        # 3. Tool Execution
        elif method == "tools/call":
            params = message.get("params", {})
            if params.get("name") == "add":
                args = params.get("arguments", {})
                answer = args.get("a", 0) + args.get("b", 0)
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": str(answer)}],
                        "isError": False
                    }
                }
        
        # 4. Error Handling
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"}
        }
