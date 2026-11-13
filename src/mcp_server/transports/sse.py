import json
import uuid
import asyncio
import logging
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sse_starlette.sse import EventSourceResponse
from ..core import PARSE_ERROR, Dispatcher, Session, error_response
from .security import LOCAL_ORIGIN_REGEX, check_host_and_origin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
dispatcher = Dispatcher()

# Production Fix 1: CORS, so a browser page on another port (the Inspector in
# "direct" mode) can connect. Only pages served from this machine are allowed,
# and no credentials (cookies) are involved.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_ORIGIN_REGEX,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class SseSession:
    def __init__(self):
        self.queue = asyncio.Queue()  # responses waiting to go down the SSE stream
        self.state = Session()  # handshake state, managed by the core


# One entry per connected client, keyed by session_id
sessions: dict[str, SseSession] = {}


@app.get("/sse")
async def sse_endpoint(request: Request):
    """Phase 3: Connection & keepalive
    Client connects here to open the Server-Sent Events stream."""
    if (rejection := check_host_and_origin(request)) is not None:
        return rejection

    # Production Fix 2: Generate a unique ID for EVERY new connection
    session_id = str(uuid.uuid4())

    session = SseSession()
    sessions[session_id] = session

    async def event_generator():
        try:
            # 1. Send the endpoint event. It's a relative URL: the client resolves it
            # against the /sse URL it connected to, so any host/port works.
            yield {
                "event": "endpoint",
                "data": f"/messages?session_id={session_id}"
            }

            # 2. Keep the stream open
            while True:
                # Production Fix 3: The Ping Loop!
                # We use wait_for to wait up to 15 seconds for a message
                try:
                    response_dict = await asyncio.wait_for(session.queue.get(), timeout=15.0)
                    yield {
                        "event": "message",
                        "data": json.dumps(response_dict)
                    }
                except asyncio.TimeoutError:
                    # If 15 seconds pass and the queue is empty, we yield a ping event!
                    # This keeps firewalls from killing our idle connection.
                    yield {
                        "comment": "ping" # SSE treats this as a comment, client ignores it, network stays alive!
                    }

        finally:
            # Production Fix 4: Stream Closure Cleanup
            # If the client closes their browser, the generator stops, hitting this finally block.
            logger.info(f"Client {session_id} disconnected!")
            if session_id in sessions:
                del sessions[session_id]

    return EventSourceResponse(event_generator())


@app.post("/messages")
async def messages_endpoint(request: Request, background_tasks: BackgroundTasks, session_id: str | None = None):
    """Phase 3: Request Routing"""
    if (rejection := check_host_and_origin(request)) is not None:
        return rejection

    # Check the session BEFORE doing any work, so a bad session_id gets a clear error
    if session_id is None:
        return Response("Missing session_id", status_code=400)
    session = sessions.get(session_id)
    if session is None:
        return Response("Session not found", status_code=404)

    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(error_response(None, PARSE_ERROR, "Parse error"), status_code=400)

    # Answer the POST straight away. The real reply goes down the SSE stream once
    # the dispatcher is done, so a slow tool doesn't hold this connection open.
    background_tasks.add_task(process_message, session, message)
    return Response("Accepted", status_code=202)


async def process_message(session: SseSession, message) -> None:
    # Run on a worker thread: a slow tool must not freeze the event loop
    # (and with it every other client and every ping).
    response_dict = await asyncio.to_thread(dispatcher.handle_request, message, session.state)

    # Notifications (like notifications/initialized) get no reply
    if response_dict is not None:
        await session.queue.put(response_dict)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
