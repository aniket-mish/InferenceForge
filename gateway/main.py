import os
import time
import asyncio

import httpx
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

app = FastAPI()

BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://llama-backend:8080").rstrip("/")
GATEWAY_MAX_CONCURRENCY = int(os.environ.get("GATEWAY_MAX_CONCURRENCY", "0"))

REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total gateway requests.",
    labelnames=["endpoint", "method", "status", "stream"],
)
REQUEST_DURATION_SECONDS = Histogram(
    "gateway_request_duration_seconds",
    "End-to-end gateway request duration in seconds.",
    labelnames=["endpoint", "method", "status", "stream"],
    buckets=(0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0),
)
INFLIGHT_REQUESTS = Gauge(
    "gateway_inflight_requests",
    "Number of inflight gateway requests.",
    labelnames=["endpoint"],
)
CANCELS_TOTAL = Counter(
    "gateway_cancels_total",
    "Total client disconnect cancellations observed by the gateway.",
    labelnames=["endpoint"],
)
QUEUE_WAIT_SECONDS = Histogram(
    "gateway_queue_wait_seconds",
    "Time spent waiting for gateway admission capacity.",
    labelnames=["endpoint"],
    buckets=(0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0),
)
STREAM_TTFT_SECONDS = Histogram(
    "gateway_stream_ttft_seconds",
    "Time to first streamed bytes forwarded to the client.",
    labelnames=["endpoint"],
    buckets=(0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0),
)

_CHAT_SEMAPHORE = None
if GATEWAY_MAX_CONCURRENCY > 0:
    _CHAT_SEMAPHORE = asyncio.Semaphore(GATEWAY_MAX_CONCURRENCY)


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/models")
async def list_models() -> Response:
    endpoint = "models"
    method = "GET"
    stream = "false"
    INFLIGHT_REQUESTS.labels(endpoint=endpoint).inc()
    status = "500"
    start = time.perf_counter()
    async with httpx.AsyncClient(base_url=BACKEND_BASE_URL) as client:
        try:
            r = await client.get("/v1/models")
            status = str(r.status_code)
            return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
        finally:
            duration = time.perf_counter() - start
            REQUESTS_TOTAL.labels(endpoint=endpoint, method=method, status=status, stream=stream).inc()
            REQUEST_DURATION_SECONDS.labels(endpoint=endpoint, method=method, status=status, stream=stream).observe(
                duration
            )
            INFLIGHT_REQUESTS.labels(endpoint=endpoint).dec()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    endpoint = "chat_completions"
    payload = await request.json()
    request_start = time.perf_counter()
    INFLIGHT_REQUESTS.labels(endpoint=endpoint).inc()

    acquired = False
    if _CHAT_SEMAPHORE is not None:
        queue_start = time.perf_counter()
        await _CHAT_SEMAPHORE.acquire()
        acquired = True
        QUEUE_WAIT_SECONDS.labels(endpoint=endpoint).observe(time.perf_counter() - queue_start)
    else:
        QUEUE_WAIT_SECONDS.labels(endpoint=endpoint).observe(0.0)

    if payload.get("stream") is True:
        async def stream():
            status = "500"
            ttft_observed = False
            try:
                async with httpx.AsyncClient(base_url=BACKEND_BASE_URL, timeout=None) as client:
                    async with client.stream("POST", "/v1/chat/completions", json=payload) as r:
                        status = str(r.status_code)
                        r.raise_for_status()
                        try:
                            async for chunk in r.aiter_raw():
                                if await request.is_disconnected():
                                    status = "499"
                                    CANCELS_TOTAL.labels(endpoint=endpoint).inc()
                                    return
                                if chunk:
                                    if not ttft_observed:
                                        STREAM_TTFT_SECONDS.labels(endpoint=endpoint).observe(
                                            time.perf_counter() - request_start
                                        )
                                        ttft_observed = True
                                    yield chunk
                        except asyncio.CancelledError:
                            status = "499"
                            CANCELS_TOTAL.labels(endpoint=endpoint).inc()
                            raise
            finally:
                duration = time.perf_counter() - request_start
                REQUESTS_TOTAL.labels(endpoint=endpoint, method="POST", status=status, stream="true").inc()
                REQUEST_DURATION_SECONDS.labels(
                    endpoint=endpoint, method="POST", status=status, stream="true"
                ).observe(duration)
                INFLIGHT_REQUESTS.labels(endpoint=endpoint).dec()
                if acquired and _CHAT_SEMAPHORE is not None:
                    _CHAT_SEMAPHORE.release()

        headers = {"x-accel-buffering": "no"}
        return StreamingResponse(
            stream(),
            status_code=200,
            media_type="text/event-stream",
            headers=headers,
        )

    status = "500"
    async with httpx.AsyncClient(base_url=BACKEND_BASE_URL) as client:
        try:
            r = await client.post("/v1/chat/completions", json=payload)
            status = str(r.status_code)
            return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
        finally:
            duration = time.perf_counter() - request_start
            REQUESTS_TOTAL.labels(endpoint=endpoint, method="POST", status=status, stream="false").inc()
            REQUEST_DURATION_SECONDS.labels(
                endpoint=endpoint, method="POST", status=status, stream="false"
            ).observe(duration)
            INFLIGHT_REQUESTS.labels(endpoint=endpoint).dec()
            if acquired and _CHAT_SEMAPHORE is not None:
                _CHAT_SEMAPHORE.release()


