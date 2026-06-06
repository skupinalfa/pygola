"""FastAPI server that exposes the governance layer over HTTP.

Typical programmatic use:

    from pygola import GovernanceLayer
    from pygola.server import create_app, ServerConfig

    layer = GovernanceLayer.from_config("policy.yaml")
    app   = create_app(layer, ServerConfig(port=8080))

For the dev convenience server (reads env vars / defaults):

    uvicorn pygola.server:app --reload

Or via the CLI:

    pygola serve --config policy.yaml --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pygola import GovernanceLayer, GovernanceContext
from pygola.config.schema import (
    AuditConfig,
    GovernanceConfig,
    Mode,
    PolicyConfig,
    ProviderConfig,
    SetupConfig,
)
from pygola.conversation import Turn, InMemoryConversationStore


# ---------------------------------------------------------------------------
# Server configuration
# ---------------------------------------------------------------------------

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    sse_keepalive_timeout: float = 25.0


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ProcessRequest(BaseModel):
    text: str
    mode: str = "auto"  # kept for UI compatibility; layer mode is fixed at startup
    conversation_id: str | None = None


class ResumeRequest(BaseModel):
    approved: bool
    edited_input: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize(ctx: GovernanceContext) -> dict[str, Any]:
    result: dict[str, Any] = {
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
    if ctx.conversation_id is not None:
        result["conversation_id"] = ctx.conversation_id
    result["llm_calls"] = ctx.llm_calls
    return result


async def _broadcast(
    sse_queues: list[asyncio.Queue[str]],
    sse_lock: asyncio.Lock,
    event: dict[str, Any],
) -> None:
    payload = json.dumps(event)
    dead: list[asyncio.Queue[str]] = []
    async with sse_lock:
        for q in sse_queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            sse_queues.remove(q)


def _init_state(
    app: FastAPI,
    layer: GovernanceLayer,
) -> None:
    app.state.layer = layer
    app.state.sessions: dict[str, tuple[GovernanceLayer, GovernanceContext]] = {}
    app.state.sessions_lock = threading.Lock()
    app.state.sse_queues: list[asyncio.Queue[str]] = []
    app.state.sse_lock = asyncio.Lock()
    app.state.conv_store = InMemoryConversationStore()


def _attach_routes(fastapi_app: FastAPI, config: ServerConfig) -> None:
    """Register all route handlers on *fastapi_app*. State is read via request.app.state."""

    @fastapi_app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @fastapi_app.get("/events")
    async def sse_events(request: Request) -> StreamingResponse:
        state = request.app.state
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        async with state.sse_lock:
            state.sse_queues.append(q)

        timeout = config.sse_keepalive_timeout

        async def stream():
            try:
                yield 'data: {"type":"connected"}\n\n'
                while True:
                    try:
                        payload = await asyncio.wait_for(q.get(), timeout=timeout)
                        yield f"data: {payload}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                async with state.sse_lock:
                    try:
                        state.sse_queues.remove(q)
                    except ValueError:
                        pass

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @fastapi_app.post("/process")
    async def process(req: ProcessRequest, request: Request) -> dict[str, Any]:
        state = request.app.state
        conv_cfg = state.layer.config.setup.conversation

        if not req.text.strip():
            raise HTTPException(status_code=400, detail="text must not be empty.")

        # --- conversation guard rails ---
        if req.conversation_id is not None:
            if not conv_cfg.enabled:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Conversation tracking is not enabled. "
                        "Set conversation.enabled=true in config."
                    ),
                )
            if not state.conv_store.has_session(req.conversation_id):
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Conversation '{req.conversation_id}' not found or has expired."
                    ),
                )

        # --- load prior history ---
        history: list[dict[str, str]] = []
        conversation_id: str | None = req.conversation_id

        if conv_cfg.enabled and conversation_id and state.conv_store.has_session(conversation_id):
            turns = state.conv_store.get_turns(conversation_id)
            # Trim to configured window (most recent N turns)
            recent = turns[-conv_cfg.max_turns :]
            for turn in recent:
                history.append({"role": "user", "content": turn.user_message})
                history.append({"role": "assistant", "content": turn.assistant_reply})
        elif conv_cfg.enabled and conversation_id is None:
            # Start a new session
            conversation_id = state.conv_store.create_session()

        # --- run governance pipeline ---
        ctx = await asyncio.to_thread(state.layer.handle, req.text, history or None)
        ctx.conversation_id = conversation_id

        # --- persist completed turn ---
        if (
            conv_cfg.enabled
            and conversation_id
            and ctx.decision.value == "allow"
            and ctx.final_output
        ):
            state.conv_store.append_turn(
                conversation_id,
                Turn(
                    user_message=ctx.sanitized_input or ctx.original_input,
                    assistant_reply=ctx.final_output,
                ),
            )

        if ctx.decision.value == "needs_confirm":
            with state.sessions_lock:
                state.sessions[ctx.request_id] = (state.layer, ctx)

        result = _serialize(ctx)
        await _broadcast(
            state.sse_queues,
            state.sse_lock,
            {"type": "processed", "ts": datetime.now(timezone.utc).isoformat(), **result},
        )
        return result

    @fastapi_app.post("/resume/{session_id}")
    async def resume(
        session_id: str, req: ResumeRequest, request: Request
    ) -> dict[str, Any]:
        state = request.app.state
        with state.sessions_lock:
            entry = state.sessions.pop(session_id, None)

        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Session '{session_id}' not found. "
                    "It may have already been resumed or expired."
                ),
            )

        layer, ctx = entry
        result_ctx = await asyncio.to_thread(
            layer.resume, ctx, req.approved, req.edited_input
        )

        if result_ctx.decision.value == "needs_confirm":
            with state.sessions_lock:
                state.sessions[result_ctx.request_id] = (layer, result_ctx)

        result = _serialize(result_ctx)
        await _broadcast(
            state.sse_queues,
            state.sse_lock,
            {
                "type": "resumed",
                "ts": datetime.now(timezone.utc).isoformat(),
                "approved": req.approved,
                **result,
            },
        )
        return result


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

    @fastapi_app.delete("/conversations/{conversation_id}")
    async def delete_conversation(
        conversation_id: str, request: Request
    ) -> dict[str, str]:
        state = request.app.state
        if not state.conv_store.has_session(conversation_id):
            raise HTTPException(
                status_code=404,
                detail=f"Conversation '{conversation_id}' not found.",
            )
        state.conv_store.delete_session(conversation_id)
        return {"deleted": conversation_id}


async def _idle_expiry_loop(app: FastAPI) -> None:
    """Background task that purges sessions idle beyond the configured timeout."""
    while True:
        await asyncio.sleep(60)
        conv_cfg = app.state.layer.config.setup.conversation
        if conv_cfg.enabled:
            app.state.conv_store.expire_idle_sessions(conv_cfg.idle_timeout_seconds)


def create_app(layer: GovernanceLayer, config: ServerConfig) -> FastAPI:
    """Create and return a configured FastAPI application.

    The *layer* is used as-is; its mode is fixed at construction time.
    """

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        _init_state(fastapi_app, layer)
        expiry_task = asyncio.create_task(_idle_expiry_loop(fastapi_app))
        try:
            yield
        finally:
            expiry_task.cancel()

    fastapi_app = FastAPI(title="Governance Layer API", lifespan=lifespan)
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["POST", "GET"],
        allow_headers=["Content-Type"],
    )
    _attach_routes(fastapi_app, config)
    return fastapi_app


# ---------------------------------------------------------------------------
# Module-level convenience app  (uvicorn pygola.server:app)
# Layer is built during lifespan so the import itself never touches env vars.
# ---------------------------------------------------------------------------

def _env_server_config() -> ServerConfig:
    cors_raw = os.environ.get(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    )
    return ServerConfig(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        cors_origins=[o.strip() for o in cors_raw.split(",")],
    )


def _env_layer() -> GovernanceLayer:
    config = GovernanceConfig(
        setup=SetupConfig(
            mode=Mode(os.environ.get("GOVERNANCE_MODE", "auto")),
            audit=AuditConfig(backend="memory"),
            trusted_provider=ProviderConfig(
                kind="anthropic",
                model=os.environ.get("TRUSTED_MODEL", "claude-haiku-4-5"),
                api_key_env="ANTHROPIC_API_KEY",
            ),
            commercial_provider=ProviderConfig(
                kind="anthropic",
                model=os.environ.get("COMMERCIAL_MODEL", "claude-haiku-4-5"),
                api_key_env="ANTHROPIC_API_KEY",
            ),
        ),
        policy=PolicyConfig(),
    )
    return GovernanceLayer(config)


def _build_default_app() -> FastAPI:
    server_config = _env_server_config()

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        _init_state(fastapi_app, _env_layer())
        yield

    fastapi_app = FastAPI(
        title="Governance Layer Test API", lifespan=lifespan
    )
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=server_config.cors_origins,
        allow_methods=["POST", "GET"],
        allow_headers=["Content-Type"],
    )
    _attach_routes(fastapi_app, server_config)
    return fastapi_app


app = _build_default_app()
