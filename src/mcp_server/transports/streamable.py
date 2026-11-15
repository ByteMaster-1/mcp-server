import asyncio
import json
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..core import (
    HANDSHAKE_VERSIONS,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION_KEY,
    STATELESS,
    STATELESS_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
    Dispatcher,
    Session,
    error_response,
    is_stateless_request,
)
from .security import LOCAL_ORIGIN_REGEX, check_host_and_origin

logging.basicConfig(level=logging.INFO)

app = FastAPI()
dispatcher = Dispatcher()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    # Browsers hide response headers from page scripts unless they're listed here
    expose_headers=["Mcp-Session-Id"],
)

KEEPALIVE_SECONDS = 15.0
HEADER_MISMATCH = -32020  # 2026-07-28: an Mcp-* header disagrees with the JSON body

# 2026-07-28 wants real HTTP status codes for these errors, not just 200
ERROR_HTTP_STATUS = {
    PARSE_ERROR: 400,
    INVALID_REQUEST: 400,
    INVALID_PARAMS: 400,
    HEADER_MISMATCH: 400,
    UNSUPPORTED_PROTOCOL_VERSION: 400,
    METHOD_NOT_FOUND: 404,
}

# Handshake-era sessions, keyed by the Mcp-Session-Id we hand out on initialize
sessions: dict[str, Session] = {}


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """Phase 5: Streamable HTTP
    One POST carries one JSON-RPC message. Quick answers come back as plain JSON.
    If a tool takes a long time, the server streams empty spaces to keep the
    connection alive, then finally streams the JSON-RPC response."""
    if (rejection := check_host_and_origin(request)) is not None:
        return rejection

    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(error_response(None, PARSE_ERROR, "Parse error"), status_code=400)

    all_versions = HANDSHAKE_VERSIONS + STATELESS_VERSIONS
    header_version = request.headers.get("mcp-protocol-version")
    if header_version is not None and header_version not in all_versions:
        data = {"supported": all_versions, "requested": header_version}
        return JSONResponse(
            error_response(None, UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version", data),
            status_code=400,
        )

    if header_version in STATELESS_VERSIONS or is_stateless_request(message):
        return await handle_stateless(request, message)
    return await handle_handshake(request, message)


@app.delete("/mcp")
async def end_session(request: Request):
    """The client is done with its session (handshake era only)."""
    if (rejection := check_host_and_origin(request)) is not None:
        return rejection
    session_id = request.headers.get("mcp-session-id")
    if session_id is None:
        return Response("Missing Mcp-Session-Id header", status_code=400)
    if sessions.pop(session_id, None) is None:
        return Response("Session not found", status_code=404)
    return Response(status_code=200)


async def handle_handshake(request: Request, message):
    """2024-11-05 ... 2025-11-25: `initialize` creates a session, and every later
    request must name it in the Mcp-Session-Id header."""
    if isinstance(message, dict) and message.get("method") == "initialize":
        session = Session()
        response_dict = dispatcher.handle_request(message, session)
        if response_dict is None or "error" in response_dict:
            # A failed initialize doesn't create a session
            return await reply(response_dict)
        session_id = str(uuid.uuid4())
        sessions[session_id] = session
        return JSONResponse(response_dict, headers={"Mcp-Session-Id": session_id})

    session_id = request.headers.get("mcp-session-id")
    if session_id is None:
        return JSONResponse(
            error_response(None, INVALID_REQUEST, "Missing Mcp-Session-Id header: send initialize first"),
            status_code=400,
        )
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse(error_response(None, INVALID_REQUEST, "Session not found"), status_code=404)
    return await run_dispatcher(message, session)


async def handle_stateless(request: Request, message):
    """2026-07-28: every POST stands on its own. No initialize, no session."""
    if (
        is_stateless_request(message)
        and "id" in message
        and (mismatch := check_routing_headers(request, message)) is not None
    ):
        return JSONResponse(error_response(message["id"], HEADER_MISMATCH, mismatch), status_code=400)
    return await run_dispatcher(message, Session(era=STATELESS), status_from_error=True)


def check_routing_headers(request: Request, message: dict) -> str | None:
    """2026-07-28 repeats key body fields in headers, so proxies and load balancers
    can route a request without reading the body. They must agree with the body."""
    params = message["params"]
    if request.headers.get("mcp-protocol-version") != params["_meta"][PROTOCOL_VERSION_KEY]:
        return "Mcp-Protocol-Version header doesn't match params._meta"
    if request.headers.get("mcp-method") != message["method"]:
        return "Mcp-Method header doesn't match the request method"
    if message["method"] == "tools/call" and request.headers.get("mcp-name") != params.get("name"):
        return "Mcp-Name header doesn't match the tool name"
    return None


async def run_dispatcher(message, session: Session, status_from_error: bool = False):
    # 1. Start the dispatcher in a background thread so a slow tool doesn't block other requests
    task = asyncio.create_task(asyncio.to_thread(dispatcher.handle_request, message, session))
    try:
        # 2. Most requests finish quickly: answer with a normal JSON response
        response_dict = await asyncio.wait_for(asyncio.shield(task), timeout=KEEPALIVE_SECONDS)
    except TimeoutError:
        # 3. Still running after 15 seconds: switch to streaming to keep the connection alive
        return StreamingResponse(keepalive_then_result(task), media_type="application/json")
    return await reply(response_dict, status_from_error)


async def reply(response_dict: dict | None, status_from_error: bool = False):
    if response_dict is None:
        # Notifications and client responses: accepted, nothing to send back
        return Response(status_code=202)
    status = 200
    if status_from_error and "error" in response_dict:
        status = ERROR_HTTP_STATUS.get(response_dict["error"]["code"], 200)
    return JSONResponse(response_dict, status_code=status)


async def keepalive_then_result(task):
    # We've already waited KEEPALIVE_SECONDS, so send the first keep-alive right away
    yield " "
    while not task.done():
        try:
            # Wait up to 15 seconds for the tool to finish
            await asyncio.wait_for(asyncio.shield(task), timeout=KEEPALIVE_SECONDS)
        except TimeoutError:
            # 15 seconds passed and the tool is STILL running!
            # Yield an empty space to keep the firewall connection alive.
            # (Whitespace before JSON is still valid JSON.)
            yield " "

    # The tool finished! Yield the actual JSON-RPC response.
    yield json.dumps(task.result())


if __name__ == "__main__":
    import uvicorn

    # We run this on a different port (8001) so you can run it side-by-side with SSE (8000)
    uvicorn.run(app, host="127.0.0.1", port=8001)
