import json
import uuid
import asyncio
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from ..core import Dispatcher

logging.basicConfig(level=logging.INFO)

app = FastAPI()
dispatcher = Dispatcher()

# Production Fix 1: Enable CORS so the Inspector (on a different port) can connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In a real app, you'd specify the exact allowed domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dictionary to hold the message queue for each connected client
sessions = {}

@app.get("/sse")
async def sse_endpoint(request: Request):
    """Phase 3: Connection & keepalive
    Client connects here to open the Server-Sent Events stream."""
    
    # Production Fix 2: Generate a unique ID for EVERY new connection
    session_id = str(uuid.uuid4())
    
    message_queue = asyncio.Queue()
    sessions[session_id] = message_queue
    
    async def event_generator():
        try:
            # 1. Send the endpoint event
            yield {
                "event": "endpoint",
                "data": f"http://127.0.0.1:8000/messages?session_id={session_id}"
            }
            
            # 2. Keep the stream open
            while True:
                # Production Fix 3: The Ping Loop!
                # We use wait_for to wait up to 15 seconds for a message
                try:
                    response_dict = await asyncio.wait_for(message_queue.get(), timeout=15.0)
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
            logging.info(f"Client {session_id} disconnected!")
            if session_id in sessions:
                del sessions[session_id]
                
    return EventSourceResponse(event_generator())

@app.post("/messages")
async def messages_endpoint(session_id: str, request: Request):
    """Phase 3: Request Routing"""
    
    message = await request.json()
    
    # Check if this is just a notification (like notifications/initialized)
    # Notifications in JSON-RPC don't have an 'id'. We don't need to reply to them!
    if "id" not in message:
        print(f"Received notification from client: {message.get('method')}")
        return "Accepted"
    
    # Route actual requests through the core dispatcher
    response_dict = dispatcher.handle_request(message)
    
    # Push the response to the correct client's queue
    if session_id in sessions:
        await sessions[session_id].put(response_dict)
        
    return "Accepted"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
