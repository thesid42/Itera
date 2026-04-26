"""
Itera API Backend — FastAPI + Server-Sent Events.

Exposes the same three-engine pipeline as the TUI over HTTP so the app
can run from the React/Vite frontend alongside (or instead of) the
Textual interface.

Endpoints
---------
GET  /                  → API health and frontend handoff metadata
POST /api/marketplace   → SSE: tokens while LLM thinks, then strategy JSON
POST /api/compile       → SSE: code tokens + simulation attempts + final result

SSE event shape  (every message is):
  data: {"type": "<event>", "data": <payload>}\n\n

Event types:
  status      str          — human-readable stage label
  token       str          — raw LLM token during marketplace thinking
  strategies  list[dict]   — final MarketplaceResult.strategies
  code        str          — streamed code token during compilation
  recompile   str          — streamed token during re-compilation (distinct colour)
  attempt     dict         — SimulationAttempt after each sim run
  result      dict         — final IterationResult once complete
  error       str          — error message
  done        {}           — stream is finished
"""
import asyncio
import json
import logging

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from utils.logger import setup_logging
setup_logging()

from engines.marketplace import run_marketplace
from engines.compiler import compile_strategy
from engines.iteration import run_iteration
from utils.models import Strategy

logger = logging.getLogger(__name__)

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Itera", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sse(event_type: str, data) -> str:
    """Encode one Server-Sent Event frame."""
    return f"data: {json.dumps({'type': event_type, 'data': data})}\n\n"


def _thread_put(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, item):
    """Thread-safe enqueue — called from executor threads."""
    asyncio.run_coroutine_threadsafe(queue.put(item), loop)


async def _drain(queue: asyncio.Queue):
    """Yield SSE frames from the queue until None sentinel arrives."""
    while True:
        item = await queue.get()
        if item is None:
            break
        event_type, data = item
        yield _sse(event_type, data)


# ── Request models ────────────────────────────────────────────────────────────

class GoalRequest(BaseModel):
    goal: str


class CompileRequest(BaseModel):
    strategy: dict   # serialised Strategy (from strategies SSE event)
    goal: str = ""   # original user goal, forwarded to the compiler for context


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """
    API-only root.

    The browser UI is the Vite app in frontend/. In development it proxies
    /api/* to this backend; production should serve frontend/dist separately.
    """
    return JSONResponse(
        {
            "service": "Itera API",
            "status": "ok",
            "frontend": "Run `cd frontend && npm run dev`, then open http://localhost:5173",
            "endpoints": ["/api/marketplace", "/api/compile"],
        }
    )


@app.post("/api/marketplace")
async def marketplace_endpoint(body: GoalRequest):
    """
    Stream marketplace strategy generation.

    Emits 'token' events while Qwen3 is thinking so the UI can show a live
    feed, then a single 'strategies' event with the parsed result.
    """
    goal = body.goal
    logger.info("Web | marketplace | goal=%r", goal)

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_token(t: str):
        _thread_put(loop, queue, ("token", t))

    async def run():
        try:
            result = await loop.run_in_executor(
                None, lambda: run_marketplace(goal, on_token=on_token)
            )
            await queue.put(("strategies", result.model_dump()["strategies"]))
        except Exception as exc:
            logger.error("Web | marketplace error: %s", exc)
            await queue.put(("error", str(exc)))
        finally:
            await queue.put(None)

    async def generate():
        yield _sse("status", "Analysing your experiment goal…")
        asyncio.create_task(run())
        async for frame in _drain(queue):
            yield frame
        yield _sse("done", {})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/compile")
async def compile_endpoint(body: CompileRequest):
    """
    Stream protocol compilation + simulation.

    Emits 'code' tokens during first compile, 'attempt' objects after each
    simulation run, 'recompile' tokens during any re-compilation, and a
    single 'result' event with the final IterationResult.
    """
    strategy = Strategy(**body.strategy)
    goal = body.goal
    logger.info("Web | compile | strategy=%r | goal=%r", strategy.name, goal)

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_code(t: str):
        _thread_put(loop, queue, ("code", t))

    def on_attempt(attempt):
        _thread_put(loop, queue, ("attempt", attempt.model_dump()))

    def on_recompile(t: str):
        _thread_put(loop, queue, ("recompile", t))

    async def run():
        try:
            await queue.put(("status", "Compiling Opentrons protocol…"))
            compiler_result = await loop.run_in_executor(
                None, lambda: compile_strategy(strategy, goal=goal, on_token=on_code)
            )
            await queue.put(("status", "Running simulation loop…"))
            iter_result = await loop.run_in_executor(
                None,
                lambda: run_iteration(
                    compiler_result,
                    goal=goal,
                    on_attempt=on_attempt,
                    on_token=on_recompile,
                ),
            )
            await queue.put(("result", iter_result.model_dump()))
        except Exception as exc:
            logger.error("Web | compile error: %s", exc)
            await queue.put(("error", str(exc)))
        finally:
            await queue.put(None)

    async def generate():
        yield _sse("status", "Starting compilation…")
        asyncio.create_task(run())
        async for frame in _drain(queue):
            yield frame
        yield _sse("done", {})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
