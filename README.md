# Transport-Agnostic MCP Server

[![Unit Tests](https://github.com/ByteMaster-1/mcp-server/actions/workflows/unit-tests.yml/badge.svg)](https://github.com/ByteMaster-1/mcp-server/actions/workflows/unit-tests.yml)
[![Ruff](https://github.com/ByteMaster-1/mcp-server/actions/workflows/ruff.yml/badge.svg)](https://github.com/ByteMaster-1/mcp-server/actions/workflows/ruff.yml)
A comprehensive, learning-focused implementation of the Model Context Protocol (MCP) showcasing the evolution of server transports.

## Architecture
This project is built with a strictly decoupled architecture. The `core.py` (Dispatcher) contains the pure JSON-RPC business logic and is completely unaware of how the data is being transmitted. 

This allows the exact same tools to be exposed over three completely different network transports.

## Protocol Versions
The core speaks two eras of MCP, on every transport:
* **Handshake era** (`2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`): the client must send `initialize` first. The server negotiates the version (it echoes the client's version if supported, otherwise offers `2025-11-25`) and rejects other requests until the handshake is done. `ping` is always allowed.
* **Stateless era** (`2026-07-28`): no handshake. Every request carries its protocol version and client capabilities in `params._meta`, and `server/discover` tells the client what the server supports.

The first request on a connection decides its era. Tool failures and invalid arguments come back as results with `isError: true` so the model can read them and retry.

### 1. STDIO Transport (`transports/stdio.py`)
The original, standard MCP transport. Reads one JSON message per line from stdin (`for line in sys.stdin`) and writes replies to stdout with manual `sys.stdout.flush()`. Logs go to stderr, because stdout is the protocol channel. A malformed line gets a parse error reply instead of crashing the server.

### 2. HTTP + SSE Transport (`transports/sse.py`)
The v1 web transport. 
* Uses FastAPI and `sse_starlette`.
* Requires two endpoints (`GET /sse` for receiving, `POST /messages` for sending).
* Manages state via `session_id` mapping to an `asyncio.Queue` plus the session's handshake state.
* `POST /messages` answers `202 Accepted` immediately; the reply arrives on the SSE stream. Tools run on a worker thread so a slow tool can't freeze other clients.
* Implements a 15-second ping loop to prevent firewall timeouts.

### 3. Streamable HTTP (`transports/streamable.py`)
The v2 unified transport.
* A single `POST /mcp` endpoint. Quick answers are plain JSON; long-running tools switch to a chunked `StreamingResponse` that sends whitespace keep-alives until the result is ready.
* Handshake era: `initialize` returns an `Mcp-Session-Id` header that the client sends on every later request (`DELETE /mcp` ends the session). Sessions are optional in the spec; this server uses them to enforce the handshake.
* Stateless era: no session at all. The `Mcp-Protocol-Version`, `Mcp-Method` and `Mcp-Name` headers must match the JSON body.
* Notifications get `202 Accepted` with an empty body.

### Security
Both HTTP transports only accept requests whose `Host` is local and whose `Origin` (when present) is a page served from this machine. That blocks other websites and DNS-rebinding attacks from driving the server.

## How to Run

1. **Activate the virtual environment & install dependencies:**
```bash
source .venv/bin/activate
pip install -r requirements.txt       # just to run the server
pip install -r requirements-dev.txt   # to also run the tests and ruff
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

## Running the Tests
From the repository root (`pyproject.toml` puts `src` on the import path):
```bash
pytest
```

Lint and format checks, the same ones CI runs:
```bash
ruff check src
ruff format --check src
```
