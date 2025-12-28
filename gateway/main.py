import os

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

app = FastAPI()

BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://llama-backend:8080").rstrip("/")


@app.get("/v1/models")
async def list_models() -> Response:
    async with httpx.AsyncClient(base_url=BACKEND_BASE_URL) as client:
        r = await client.get("/v1/models")
        return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    payload = await request.json()

    async def stream():
        async with httpx.AsyncClient(base_url=BACKEND_BASE_URL, timeout=None) as client:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as r:
                r.raise_for_status()
                try:
                    async for chunk in r.aiter_raw():
                        if await request.is_disconnected():
                            await r.aclose()
                            return
                        if chunk:
                            yield chunk
                finally:
                    await r.aclose()

    if payload.get("stream") is True:
        headers = {"x-accel-buffering": "no"}
        return StreamingResponse(
            stream(),
            status_code=200,
            media_type="text/event-stream",
            headers=headers,
        )

    async with httpx.AsyncClient(base_url=BACKEND_BASE_URL) as client:
        r = await client.post("/v1/chat/completions", json=payload)
        return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


