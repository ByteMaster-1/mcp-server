def tool_add(args: dict) -> str:
    return str(args.get("a",0)+args.get("b",0))

class Dispatcher:
    """The transport-agnostic core. It doesn't know about stdio or HTTP.
    It just takes a JSON dictionary, processes it, and returns a JSON dictionary."""
    def __init__(self):
        self.registered_tools= {
            "add": {
                "description":"Add two numbers together.",
                "inputSchema":{
                    "type": "object",
                    "properties":{
                        "a": {"type": "integer"},
                        "b": {"type": "integer"}
                    },
                    "required": ["a", "b"]
                },
                "handler":tool_add
            }
        }
    
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
                        "name": name,
                        "description": data["description"],
                        "inputSchema": data["inputSchema"]
                    }
                    for name, data in self.registered_tools.items()
                ]
                }
            }
            
        # 3. Tool Execution
        elif method == "tools/call":
            params = message.get("params", {})
            name = params.get("name")
            args = params.get("arguments",{})
            if name not in self.registered_tools:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
            # 2. Execute the tool with try/except
            try:
                handler_func = self.registered_tools[name]["handler"]
                answer = handler_func(args)
                
                return {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": answer}], "isError": False}
                }
            except Exception as e:
                # 3. Catch crashes cleanly
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(e)}}

                
        
        # 4. Error Handling
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"}
        }
