import json
from fastapi import FastAPI, Request
from ..core import Dispatcher

app = FastAPI()
dispatcher = Dispatcher()

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """Phase 5: True Streamable HTTP
    The client sends a POST request, and the server returns a StreamingResponse.
    If the tool takes a long time, the server streams empty spaces to keep the 
    connection alive, then finally streams the JSON-RPC response."""
    
    message = await request.json()
    
    async def stream_generator():
        # 1. Start the tool execution in a background thread so it doesn't block our generator
        import asyncio
        task = asyncio.create_task(asyncio.to_thread(dispatcher.handle_request, message))
        
        # 2. While the tool is still running...
        while not task.done():
            try:
                # Wait up to 15 seconds for the tool to finish
                await asyncio.wait_for(asyncio.shield(task), timeout=15.0)
            except asyncio.TimeoutError:
                # 15 seconds passed and the tool is STILL running! 
                # Yield an empty space to keep the firewall connection alive.
                yield " "
                
        # 3. The tool finished! Yield the actual JSON-RPC response.
        response_dict = task.result()
        yield json.dumps(response_dict)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(stream_generator(), media_type="application/json")

if __name__ == "__main__":
    import uvicorn
    # We run this on a different port (8001) so you can run it side-by-side with SSE (8000)
    uvicorn.run(app, host="127.0.0.1", port=8001)
