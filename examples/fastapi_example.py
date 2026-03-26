#!/usr/bin/env python3
"""
Example of using ClaudeSDKClient with FastAPI.

This example demonstrates the CORRECT way to use ClaudeSDKClient in
FastAPI/Starlette applications, where each request runs in a different
async task.
"""

from claude_agent_sdk import ClaudeSDKClient, TaskContextError

# Install FastAPI: pip install fastapi uvicorn
# Run: uvicorn fastapi_example:app --reload

try:
    from fastapi import FastAPI
except ImportError:
    print("FastAPI not installed. Install with: pip install fastapi uvicorn")
    exit(1)

app = FastAPI()


# WRONG - Do not do this! Will raise TaskContextError:
#
# client = ClaudeSDKClient()
#
# @app.on_event("startup")
# async def startup():
#     await client.connect()  # Task A (startup)
#
# @app.post("/query")
# async def endpoint(prompt: str):
#     # This is Task B (request handler) - will raise TaskContextError!
#     async for msg in client.receive_messages():
#         yield msg


# CORRECT - Create client per request:
@app.post("/query")
async def query_endpoint(prompt: str):
    """Handle query request in the request's task context."""
    async with ClaudeSDKClient() as client:
        # Connect and use in the same task (the request handler)
        await client.query(prompt)

        results = []
        async for msg in client.receive_messages():
            # Process messages
            results.append(msg)

        return {"results": len(results)}


# Error handling example:
@app.post("/safe-query")
async def safe_query_endpoint(prompt: str):
    """Handle query with proper error handling."""
    try:
        async with ClaudeSDKClient() as client:
            await client.query(prompt)

            results = []
            async for msg in client.receive_messages():
                results.append(msg)

            return {"results": len(results)}

    except TaskContextError as e:
        # This shouldn't happen with the correct pattern above,
        # but demonstrates error handling
        return {
            "error": "Task context error",
            "connect_task": e.connect_task_id,
            "current_task": e.current_task_id,
        }


if __name__ == "__main__":
    import uvicorn

    print("Starting FastAPI server...")
    print("Example endpoints:")
    print("  POST http://localhost:8000/query")
    print("  POST http://localhost:8000/safe-query")
    print("\nTest with:")
    print('  curl -X POST "http://localhost:8000/query" -H "Content-Type: application/json" -d \'{"prompt":"Hello"}\'')
    uvicorn.run(app, host="0.0.0.0", port=8000)
