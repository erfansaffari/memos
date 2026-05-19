"""
mcp_server.py
MemOS MCP Server — exposes memory tools to any MCP-compatible AI client.

Runs as a local stdio MCP server. The client (Claude Desktop, Cursor, etc.)
spawns this process and communicates over stdin/stdout.

Usage:
    python mcp_server.py

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "memos": {
          "command": "python",
          "args": ["/absolute/path/to/memos/mcp_server.py"]
        }
      }
    }

Cursor MCP config (~/.cursor/mcp.json):
    {
      "mcpServers": {
        "memos": {
          "command": "python",
          "args": ["/absolute/path/to/memos/mcp_server.py"]
        }
      }
    }
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Allow imports from the project root when invoked directly
sys.path.insert(0, str(Path(__file__).parent))

load_dotenv()

# ---------------------------------------------------------------------------
# Lazy-initialised singletons — created on first tool call so the server
# starts fast without loading the sentence-transformers model immediately.
# ---------------------------------------------------------------------------

_store = None
_vector_store = None
_client = None
_extractor = None
_verifier = None
_router = None


def _init():
    """Initialise all MemOS components on first use."""
    global _store, _vector_store, _client, _extractor, _verifier, _router

    if _store is not None:
        return

    from anthropic import Anthropic

    from agents.extractor import ExtractionAgent
    from agents.verifier import VerifierAgent
    from memory.store import MemoryStore
    from memory.vector_store import VectorStore
    from retrieval.router import RetrievalRouter

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file or environment."
        )

    _store = MemoryStore()
    _vector_store = VectorStore()
    _client = Anthropic(api_key=api_key)
    _extractor = ExtractionAgent(_client)
    _verifier = VerifierAgent(_client)
    _router = RetrievalRouter(_client, _store, _vector_store)


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "memos",
    instructions=(
        "MemOS persistent memory tools. "
        "Use memos_recall to fetch relevant context before answering. "
        "Use memos_remember to store important facts from the conversation. "
        "Use memos_stats to check how much is stored."
    ),
)


@mcp.tool()
def memos_recall(query: str, budget: str = "medium") -> str:
    """
    Retrieve memories relevant to a query using the hierarchical router.

    Args:
        query:  The question or topic to search memory for.
        budget: Retrieval depth — "shallow" (3), "medium" (8), or "deep" (20 memories).

    Returns:
        Formatted memory context block, plus metadata about what was retrieved.
    """
    _init()

    from retrieval.router import format_context

    memories, meta = _router.retrieve(query)
    context = format_context(memories, meta.get("budget", budget))

    if not memories:
        return "No relevant memories found."

    routing = meta.get("routing", {})
    header = (
        f"[{len(memories)} memories | "
        f"intent={routing.get('intent', '?')} | "
        f"budget={meta.get('budget', '?')} | "
        f"levels={routing.get('levels', [])}]\n\n"
    )
    return header + context


@mcp.tool()
def memos_remember(content: str, source: str = "mcp") -> str:
    """
    Store a piece of information as a persistent memory.

    Runs the content through extraction and verification before writing.
    Deduplication is applied — re-storing the same fact is safe.

    Args:
        content: The information to remember (plain text, any length).
        source:  Where this came from, e.g. "conversation", "note". Stored as tag.

    Returns:
        Summary of what was stored.
    """
    _init()

    from utils import is_near_duplicate_distance

    # Extract structured memories from the raw content
    result = _extractor.extract(content, "")

    if not result.memories:
        return "Nothing worth storing was found in the provided content."

    stored = 0
    skipped = 0
    summaries: list[str] = []

    for mem in result.memories:
        # Tag with source
        if source and source not in mem.tags:
            mem.tags.append(source)

        # Layer 1: vector similarity dedup
        similar = _vector_store.search(
            mem.content, n_results=1, level=int(mem.level), min_confidence=0.0
        )
        if similar and is_near_duplicate_distance(similar[0]["distance"]):
            _store.update_frequency(similar[0]["id"])
            skipped += 1
            continue

        # Layer 2: verifier contradiction check
        existing = _store.get_by_level(int(mem.level))
        report = _verifier.verify(mem, existing)

        if report.has_conflict and report.resolution == "duplicate":
            skipped += 1
            continue

        if report.has_conflict and report.old_memory_id:
            decay_factors = {"update": 0.5, "decay": 0.6}
            factor = decay_factors.get(report.resolution)
            if factor:
                _store.decay_confidence(report.old_memory_id, factor)

        _store.save(mem)
        _vector_store.upsert(mem)
        stored += 1
        summaries.append(f"  [{mem.level.name}] {mem.summary}")

    lines = [f"Stored {stored} memor{'y' if stored == 1 else 'ies'}."]
    if skipped:
        lines.append(f"Skipped {skipped} duplicate(s).")
    if summaries:
        lines.append("What was stored:")
        lines.extend(summaries)
    return "\n".join(lines)


@mcp.tool()
def memos_forget(memory_id: str) -> str:
    """
    Forget a specific memory by setting its confidence to zero.

    The memory remains in the database but will no longer be retrieved.
    Use memos_recall to find the memory ID first.

    Args:
        memory_id: UUID of the memory to forget.

    Returns:
        Confirmation message.
    """
    _init()

    mem = _store.get_by_id(memory_id)
    if mem is None:
        return f"No memory found with ID {memory_id}."

    _store.decay_confidence(memory_id, factor=0.0)
    return f"Forgot: [{mem.level.name}] {mem.summary}"


@mcp.tool()
def memos_stats() -> str:
    """
    Return memory counts by level.

    Returns:
        Summary of how many memories are stored at each level.
    """
    _init()

    s = _store.stats()
    by_level = s["by_level"]
    lines = [
        f"Total memories: {s['total']}",
        f"  Level 1 — Identity : {by_level.get(1, 0)}",
        f"  Level 2 — Project  : {by_level.get(2, 0)}",
        f"  Level 3 — Episodic : {by_level.get(3, 0)}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Print to stderr so it doesn't corrupt stdio MCP messages
    counts_msg = ""
    try:
        _init()
        s = _store.stats()
        counts_msg = (
            f"  Memories: {s['total']} "
            f"(L1:{s['by_level'].get(1,0)}  "
            f"L2:{s['by_level'].get(2,0)}  "
            f"L3:{s['by_level'].get(3,0)})"
        )
    except Exception as e:
        counts_msg = f"  Warning: could not load memory store — {e}"

    print(
        f"MemOS MCP server starting…\n{counts_msg}\n"
        "Waiting for MCP client connection.",
        file=sys.stderr,
    )

    mcp.run(transport="stdio")
