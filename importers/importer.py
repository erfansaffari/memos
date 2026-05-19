"""
importers/importer.py
Chat-history importer for MemOS.

Supports:
  - Claude (claude.ai JSON export)
  - ChatGPT (conversations.json from OpenAI data export)
  - Gemini (Google Takeout JSON — ZIP extraction is future work; pass the
             extracted .json path directly for now)

Each parser returns a list of turn dicts:
    {"user": str, "assistant": str, "timestamp": str | None}

run_import() orchestrates:
  1. SHA-256 hash → guard against re-processing the same file
  2. Parse turns
  3. For each turn: extract → cosine-sim dedup → verify → store
  4. Log the import result (unless --dry-run)

Deduplication threshold: cosine distance < 0.08  (≈ similarity > 0.92).
ChromaDB returns cosine DISTANCE (0 = identical).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from rich.progress import Progress, SpinnerColumn, TextColumn

from utils import is_near_duplicate_distance

if TYPE_CHECKING:
    from anthropic import Anthropic

    from agents.extractor import ExtractionAgent
    from agents.verifier import VerifierAgent
    from memory.store import MemoryStore
    from memory.vector_store import VectorStore


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_claude(filepath: str) -> list[dict]:
    """
    Parse a Claude.ai chat-export JSON file.

    Claude exports look like:
        [{"name": "...", "conversation": [{"role": "human"|"assistant", "text": "..."}]}, ...]
    or a flat list of messages depending on the export version.

    We pair consecutive human+assistant messages into turns.
    """
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))

    turns: list[dict] = []

    def _pair(messages: list[dict]) -> None:
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = (msg.get("role") or msg.get("sender") or "").lower()
            text = (
                msg.get("text")
                or msg.get("content")
                or _extract_text_blocks(msg.get("content") or [])
            )
            if role in ("human", "user") and i + 1 < len(messages):
                nxt = messages[i + 1]
                nxt_role = (nxt.get("role") or nxt.get("sender") or "").lower()
                nxt_text = (
                    nxt.get("text")
                    or nxt.get("content")
                    or _extract_text_blocks(nxt.get("content") or [])
                )
                if nxt_role in ("assistant", "ai"):
                    turns.append(
                        {
                            "user": str(text or ""),
                            "assistant": str(nxt_text or ""),
                            "timestamp": msg.get("created_at") or msg.get("timestamp"),
                        }
                    )
                    i += 2
                    continue
            i += 1

    if isinstance(data, list) and data and isinstance(data[0], dict):
        if "conversation" in data[0]:
            for conv in data:
                _pair(conv.get("conversation") or conv.get("messages") or [])
        elif "role" in data[0] or "sender" in data[0]:
            _pair(data)
        elif "chat_messages" in data[0]:
            for conv in data:
                _pair(conv.get("chat_messages") or [])

    elif isinstance(data, dict):
        _pair(data.get("messages") or data.get("conversation") or [])

    return turns


def _extract_text_blocks(content) -> str:
    """Pull text out of Claude's content-block lists."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return ""


def parse_chatgpt(filepath: str) -> list[dict]:
    """
    Parse a ChatGPT conversations.json (from OpenAI data export).

    Format:
        [{"title": "...", "mapping": {"<uuid>": {"message": {"role": "user"|"assistant",
                                                             "content": {"parts": [...]}}}}}]
    """
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    turns: list[dict] = []

    if not isinstance(data, list):
        data = [data]

    for conv in data:
        mapping = conv.get("mapping") or {}
        # Build sorted list of messages via parent-child links
        nodes = {k: v for k, v in mapping.items() if v.get("message")}
        # Topological order via parent pointers
        ordered = _topo_sort(nodes)

        user_msg: str | None = None
        user_ts: str | None = None

        for node in ordered:
            msg = node.get("message") or {}
            role = (msg.get("role") or msg.get("author", {}).get("role") or "").lower()
            parts = (msg.get("content") or {}).get("parts") or []
            text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
            ts = msg.get("create_time")
            ts_str = str(ts) if ts else None

            if role == "user":
                user_msg = text
                user_ts = ts_str
            elif role == "assistant" and user_msg is not None:
                turns.append(
                    {"user": user_msg, "assistant": text, "timestamp": user_ts}
                )
                user_msg = None

    return turns


def _topo_sort(nodes: dict) -> list[dict]:
    """Return message nodes in conversation order (parent before child)."""
    # Build child->parent map
    children: dict[str, list[str]] = {}
    roots = []
    for k, v in nodes.items():
        parent = v.get("parent")
        if parent and parent in nodes:
            children.setdefault(parent, []).append(k)
        else:
            roots.append(k)

    ordered = []
    stack = list(roots)
    while stack:
        node_id = stack.pop(0)
        if node_id in nodes:
            ordered.append(nodes[node_id])
            stack = (children.get(node_id) or []) + stack

    return ordered


def parse_gemini(filepath: str) -> list[dict]:
    """
    Parse a Gemini JSON export.

    Google Takeout Gemini format:
        {"conversations": [{"messages": [{"author": "user"|"model", "text": "..."}]}]}

    ZIP extraction is future work — pass the extracted .json directly.
    """
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    turns: list[dict] = []

    convs: list = []
    if isinstance(data, dict):
        convs = data.get("conversations") or data.get("conversation") or [data]
    elif isinstance(data, list):
        convs = data

    for conv in convs:
        messages = conv.get("messages") or conv.get("turns") or []
        user_msg: str | None = None
        user_ts: str | None = None

        for msg in messages:
            author = (msg.get("author") or msg.get("role") or "").lower()
            text = str(msg.get("text") or msg.get("content") or "").strip()
            ts = msg.get("timestamp") or msg.get("create_time")

            if author == "user":
                user_msg = text
                user_ts = str(ts) if ts else None
            elif author in ("model", "assistant") and user_msg is not None:
                turns.append(
                    {"user": user_msg, "assistant": text, "timestamp": user_ts}
                )
                user_msg = None

    return turns


PARSERS = {
    "claude": parse_claude,
    "chatgpt": parse_chatgpt,
    "gemini": parse_gemini,
}


# ---------------------------------------------------------------------------
# Core import runner
# ---------------------------------------------------------------------------


def _sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_import(
    filepath: str,
    platform: str,
    store: "MemoryStore",
    vector_store: "VectorStore",
    extractor: "ExtractionAgent",
    verifier: "VerifierAgent",
    dry_run: bool = False,
) -> dict:
    """
    Import a chat-history file into MemOS memory.

    Returns:
        {"turns": int, "added": int, "skipped": int, "already_imported": bool}
    """
    if platform not in PARSERS:
        raise ValueError(
            f"Unknown platform '{platform}'. Supported: {', '.join(PARSERS)}"
        )

    file_hash = _sha256(filepath)

    # Guard: skip if this exact file was already imported
    if store.import_already_processed(file_hash):
        return {"turns": 0, "added": 0, "skipped": 0, "already_imported": True}

    parser = PARSERS[platform]
    turns = parser(filepath)
    total_turns = len(turns)
    added = 0
    skipped = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task(
            f"Processing {total_turns} turns from {Path(filepath).name}…",
            total=total_turns,
        )

        for turn in turns:
            user_text = (turn.get("user") or "").strip()
            assistant_text = (turn.get("assistant") or "").strip()
            if not user_text and not assistant_text:
                progress.advance(task)
                continue

            combined = f"User: {user_text}\nAssistant: {assistant_text}"
            result = extractor.extract(user_text, assistant_text)

            for mem in result.memories:
                # Layer 1: cosine distance dedup (< 0.08 ≈ sim > 0.92)
                similar = vector_store.search(
                    mem.content,
                    n_results=1,
                    level=int(mem.level),
                    min_confidence=0.0,
                )
                if similar and is_near_duplicate_distance(similar[0]["distance"]):
                    store.update_frequency(similar[0]["id"])
                    skipped += 1
                    continue

                # Layer 2: verifier contradiction/duplicate check
                existing = store.get_by_level(int(mem.level))
                report = verifier.verify(mem, existing)

                if report.has_conflict and report.resolution == "duplicate":
                    skipped += 1
                    continue

                if report.has_conflict and report.old_memory_id:
                    decay_factors = {"update": 0.5, "decay": 0.6}
                    factor = decay_factors.get(report.resolution)
                    if factor:
                        store.decay_confidence(report.old_memory_id, factor)

                if not dry_run:
                    store.save(mem)
                    vector_store.upsert(mem)
                added += 1

            progress.advance(task)

    if not dry_run:
        store.log_import(
            file_hash=file_hash,
            platform=platform,
            file_name=Path(filepath).name,
            turns=total_turns,
            added=added,
            skipped=skipped,
        )

    return {
        "turns": total_turns,
        "added": added,
        "skipped": skipped,
        "already_imported": False,
    }
