import sys
import json

def send_response(request_id, result):
    """Format and send a JSON-RPC 2.0 response to stdout."""
    response = {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    }
    # MCP requires messages to be separated by newlines
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush() # Flush is critical so the message actually sends immediately

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
            
        message = json.loads(line)
        
        # Check if it's a request (has an 'id' and 'method')
        if "id" in message and "method" in message:
            request_id = message["id"]
            method = message["method"]
            
            # 1. Handle the capability handshake
            if method == "initialize":
                send_response(request_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "my-scratch-server",
                        "version": "0.1.0"
                    }
                })
            
            # 2. Handle the tool discovery request
            elif method == "tools/list":
                send_response(request_id, {
                    "tools": [
                        {
                            "name": "add",
                            "description": "Add two numbers together.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "integer", "description": "The first number"},
                                    "b": {"type": "integer", "description": "The second number"}
                                },
                                "required": ["a", "b"]
                            }
                        }
                    ]
                })

            # 3. Handle the tool execution request
            elif method == "tools/call":
                # The parameters are inside message["params"]
                params = message.get("params", {})
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
                
                if tool_name == "add":
                    # Extract the arguments
                    a = tool_args.get("a", 0)
                    b = tool_args.get("b", 0)
                    
                    # Run our logic
                    answer = a + b
                    
                    # MCP requires the result to be a specific format
                    # with a list of "content" blocks (usually text)
                    send_response(request_id, {
                        "content": [
                            {
                                "type": "text",
                                "text": str(answer) # Result must be a string
                            }
                        ],
                        "isError": False
                    })



if __name__ == "__main__":
    main()
