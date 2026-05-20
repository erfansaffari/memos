"""
server/app.py
Local FastAPI server for MemOS Chrome extension.

Run with:
    python main.py server
    # or directly:
    python -m uvicorn server.app:app --host 127.0.0.1 --port 8765 --reload

The Chrome extension connects to http://localhost:8765
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from memory.store import MemoryStore
from memory.vector_store import VectorStore
from agents.extractor import ExtractionAgent
from agents.verifier import VerifierAgent
from retrieval.router import RetrievalRouter, format_context
from server.models import (
    RecallRequest,
    RecallResponse,
    RememberRequest,
    RememberResponse,
    StatsResponse,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Singletons — initialised once at startup, shared across all requests
# ---------------------------------------------------------------------------

_client: anthropic.Anthropic | None = None
_store: MemoryStore | None = None
_vector_store: VectorStore | None = None
_router: RetrievalRouter | None = None
_extractor: ExtractionAgent | None = None
_verifier: VerifierAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _store, _vector_store, _router, _extractor, _verifier

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )

    _client = anthropic.Anthropic(api_key=api_key)
    _store = MemoryStore()
    _vector_store = VectorStore()
    _router = RetrievalRouter(_client, _store, _vector_store)
    _extractor = ExtractionAgent(_client)
    _verifier = VerifierAgent(_client)

    total = _store.stats()["total"]
    print(f"MemOS server ready — {total} memories loaded.")

    yield
    # Nothing to clean up on shutdown


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="MemOS Local Server", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "chrome-extension://*",
        "http://localhost",
        "http://localhost:*",
        "http://127.0.0.1",
        "http://127.0.0.1:*",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    """Extension polls this every 30 s to show the green/red status dot."""
    stats = _store.stats()
    return {"status": "ok", "memories": stats["total"]}


@app.post("/recall", response_model=RecallResponse)
async def recall(req: RecallRequest):
    """
    Return relevant memory context for a user query.
    Called by the extension before the user's message is sent.
    """
    memories, meta = _router.retrieve(req.query)
    context = format_context(memories, meta.get("budget", req.budget))
    return RecallResponse(
        context=context,
        memory_count=len(memories),
        levels_used=meta.get("levels_used", []),
        budget=meta.get("budget", req.budget),
    )


@app.post("/remember", response_model=RememberResponse)
async def remember(req: RememberRequest):
    """
    Extract and store memories from a completed conversation turn.
    Called by the extension after the AI response finishes rendering.
    """
    result = _extractor.extract(req.user_message, req.assistant_response)
    stored = 0
    skipped = 0

    for mem in result.memories:
        # Verifier: check for contradictions against existing memories at same level
        existing = _store.get_by_level(int(mem.level), limit=20)
        report = _verifier.verify(mem, existing)

        if report.has_conflict and report.resolution == "duplicate":
            skipped += 1
            continue

        if report.has_conflict and report.old_memory_id:
            decay_factors = {"update": 0.5, "decay": 0.6}
            factor = decay_factors.get(report.resolution)
            if factor:
                _store.decay_confidence(report.old_memory_id, factor)

        # Dedup: skip if a near-identical vector already exists (cosine distance < 0.08)
        similar = _vector_store.query(mem.content, n_results=1, min_confidence=0.0)
        if similar and similar[0]["distance"] < 0.08:
            skipped += 1
            continue

        _store.upsert(mem)
        _vector_store.upsert(mem)
        stored += 1

    return RememberResponse(
        memories_stored=stored,
        memories_skipped=skipped,
        reasoning=result.reasoning,
    )


@app.get("/stats", response_model=StatsResponse)
async def stats():
    """Return memory counts by level."""
    s = _store.stats()
    return StatsResponse(total=s["total"], by_level=s["by_level"])
