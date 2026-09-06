# Transport-Agnostic MCP Server

A comprehensive, learning-focused implementation of the Model Context Protocol (MCP) showcasing the evolution of server transports.

## Architecture
This project is built with a strictly decoupled architecture. The `core.py` (Dispatcher) contains the pure JSON-RPC business logic and is completely unaware of how the data is being transmitted. 

This allows the exact same tools to be exposed over three completely different network transports:

### 1. STDIO Transport (`transports/stdio.py`)
The original, standard MCP transport. Runs via standard input/output streams. Requires a blocking `while True` loop and manual `sys.stdout.flush()` management.

### 2. HTTP + SSE Transport (`transports/sse.py`)
The v1 web transport. 
* Uses FastAPI and `sse_starlette`.
* Requires two endpoints (`GET /sse` for receiving, `POST /messages` for sending).
* Manages state via `session_id` mapping to `asyncio.Queue`.
* Implements a 15-second ping loop to prevent firewall timeouts.

### 3. Streamable HTTP (`transports/streamable.py`)
The v2 unified transport.
* A single `POST /mcp` endpoint using HTTP chunked transfer encoding (`StreamingResponse`).
* Eliminates Session IDs and Queues.
* Streams whitespace keep-alive pings over the active POST connection to prevent timeouts on long-running tools.

## How to Run

1. **Activate the virtual environment:**
```bash
source .venv/bin/activate
```

2. **Run a Transport:**
Because this is a Python package, run the transports as modules from the `src/` directory:

```bash
cd src

# To run HTTP+SSE on port 8000
python -m mcp_server.transports.sse

# To run Streamable HTTP on port 8001
python -m mcp_server.transports.streamable
```

## Future Capstone
Designed to be plugged into a LiveKit Voice Agent, allowing real-time AI agents to interact with the underlying tools via the web transports.
