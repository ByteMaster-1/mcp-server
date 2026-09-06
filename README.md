# Transport-Agnostic MCP Server

[![Python CI](https://github.com/ByteMaster-1/mcp-project/actions/workflows/python-app.yml/badge.svg)](https://github.com/ByteMaster-1/mcp-project/actions/workflows/python-app.yml)
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

1. **Activate the virtual environment & install dependencies:**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

2. **Run a Transport:**
Because this is a Python package, run the transports as modules from the `src/` directory:

```bash
cd src

# 1. To run STDIO (Standard Input/Output)
python -m mcp_server.transports.stdio

# 2. To run HTTP+SSE on port 8000
python -m mcp_server.transports.sse

# 3. To run Streamable HTTP on port 8001
python -m mcp_server.transports.streamable
```

## Testing with the MCP Inspector
To test the web transports interactively, run the official MCP Inspector from the `src/` directory:
```bash
cd src
npx @modelcontextprotocol/inspector@latest
```
When it opens, put `http://127.0.0.1:8000/sse` as the URL for the SSE transport, or `http://127.0.0.1:8001/mcp` for Streamable.
