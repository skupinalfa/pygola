"""FastAPI server that wraps the governance_layer package for the test UI.

Run from the project root:
    uvicorn server.api_server:app --reload

All processed requests are broadcast to connected SSE clients at /events
so the browser UI can display live output without terminal inspection.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; set env vars manually before starting

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel as ApiModel
except ImportError as exc:
    raise SystemExit(
        "FastAPI is not installed. Run: pip install -r server/requirements.txt"
    ) from exc

from pygola import GovernanceLayer, GovernanceConfig, GovernanceContext
from pygola.config.schema import AuditConfig, Mode, PolicyConfig, SetupConfig, ProviderConfig

# ---------------------------------------------------------------------------
# Startup: build one GovernanceLayer per mode so providers are initialised
# once and their SDK clients are reused across requests.
# ---------------------------------------------------------------------------

# Keyed by the mode string ("auto" / "confirm") populated during lifespan startup.
_layers: dict[str, GovernanceLayer] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for mode_val in ("auto", "confirm"):
        config = GovernanceConfig(
            setup=SetupConfig(
                mode=Mode(mode_val),
                audit=AuditConfig(backend="memory"),
                trusted_provider=ProviderConfig(
                    kind="anthropic",
                    model="claude-haiku-4-5",
                    api_key_env="ANTHROPIC_API_KEY",
                ),
                commercial_provider=ProviderConfig(
                    kind="anthropic",
                    model="claude-haiku-4-5",
                    api_key_env="ANTHROPIC_API_KEY",
                ),
            ),
            policy=PolicyConfig(),
        )
        _layers[mode_val] = GovernanceLayer(config)
    yield


app = FastAPI(title="Governance Layer Test API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)

# In-memory store for paused CONFIRM sessions: session_id -> (layer, context)
_sessions: dict[str, tuple[GovernanceLayer, GovernanceContext]] = {}
_sessions_lock = threading.Lock()

# SSE: one asyncio.Queue per connected browser client
_sse_queues: list[asyncio.Queue[str]] = []
_sse_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# SSE broadcast helpers
# ---------------------------------------------------------------------------

async def _broadcast(event: dict[str, Any]) -> None:
    """Push a JSON event to every connected SSE client."""
    payload = json.dumps(event)
    dead: list[asyncio.Queue[str]] = []
    async with _sse_lock:
        for q in _sse_queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ProcessRequest(ApiModel):
    text: str
    mode: str = "auto"


class ResumeRequest(ApiModel):
    approved: bool
    edited_input: str | None = None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize(ctx: GovernanceContext) -> dict[str, Any]:
    return {
        "session_id": ctx.request_id,
        "decision": ctx.decision.value,
        "original_input": ctx.original_input,
        "sanitized_input": ctx.sanitized_input,
        "final_output": ctx.final_output,
        "entities": [
            {"type": e.entity_type, "placeholder": e.placeholder}
            for e in ctx.entities
        ],
        "block_reasons": ctx.block_reasons,
        "pipeline": [
            {"stage": r.stage_name, "timestamp": r.timestamp, "summary": r.summary}
            for r in ctx.history
        ],
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/events")
async def sse_events() -> StreamingResponse:
    """Server-Sent Events stream — the browser UI subscribes here for live output."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    async with _sse_lock:
        _sse_queues.append(q)

    async def stream():
        try:
            # Announce connection
            yield 'data: {"type":"connected"}\n\n'
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Keep the connection alive with a comment line
                    yield ": keepalive\n\n"
        finally:
            async with _sse_lock:
                try:
                    _sse_queues.remove(q)
                except ValueError:
                    pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/process")
async def process(req: ProcessRequest) -> dict[str, Any]:
    try:
        mode = Mode(req.mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown mode '{req.mode}'. Use 'auto' or 'confirm'.",
        )

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty.")

    layer = _layers[req.mode]
    ctx = await asyncio.to_thread(layer.handle, req.text)

    if ctx.decision.value == "needs_confirm":
        with _sessions_lock:
            _sessions[ctx.request_id] = (layer, ctx)

    result = _serialize(ctx)
    await _broadcast({"type": "processed", "ts": _ts(), **result})
    return result


@app.post("/resume/{session_id}")
async def resume(session_id: str, req: ResumeRequest) -> dict[str, Any]:
    with _sessions_lock:
        entry = _sessions.pop(session_id, None)

    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found. It may have already been resumed or expired.",
        )

    layer, ctx = entry
    result_ctx = await asyncio.to_thread(layer.resume, ctx, req.approved, req.edited_input)

    if result_ctx.decision.value == "needs_confirm":
        with _sessions_lock:
            _sessions[result_ctx.request_id] = (layer, result_ctx)

    result = _serialize(result_ctx)
    await _broadcast({"type": "resumed", "ts": _ts(), "approved": req.approved, **result})
    return result
