"""
utils.py
Shared helpers used across the MemOS pipeline.

- parse_json: strips markdown fences from LLM JSON responses before parsing
- utcnow: timezone-aware datetime helper (replaces deprecated datetime.utcnow)
- truncate_content: smart truncation for memory content display
- format_log_line: formats node log output for CLI display
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def truncate_content(content: str, max_len: int = 80) -> str:
    if len(content) <= max_len:
        return content
    return content[: max_len - 1] + "…"


def format_log_line(node: str, message: str) -> str:
    return f"[{node}] {message}"


# ChromaDB cosine distance: 0 = identical, 2 = opposite. Below this → near-duplicate.
DUPLICATE_DISTANCE_THRESHOLD = 0.15


def is_near_duplicate_distance(distance: float, threshold: float = DUPLICATE_DISTANCE_THRESHOLD) -> bool:
    return distance < threshold


def is_similar_text(a: str, b: str, jaccard_threshold: float = 0.65) -> bool:
    """Heuristic text similarity for deduplicating retrieved memories in context."""
    a_norm = a.lower().strip()
    b_norm = b.lower().strip()
    if a_norm == b_norm:
        return True
    if a_norm in b_norm or b_norm in a_norm:
        return True
    ta, tb = set(a_norm.split()), set(b_norm.split())
    if not ta or not tb:
        return False
    jaccard = len(ta & tb) / len(ta | tb)
    return jaccard >= jaccard_threshold


def dedupe_by_content(items: list[dict]) -> list[dict]:
    """Remove near-duplicate memories from a retrieved list before building context."""
    kept: list[dict] = []
    for item in items:
        content = item.get("content", "")
        if any(is_similar_text(content, k.get("content", "")) for k in kept):
            continue
        kept.append(item)
    return kept


def parse_json(text: str) -> dict:
    """
    Parse JSON from an LLM response that may be wrapped in markdown code fences.

    Claude models sometimes wrap JSON output in ```json ... ``` even when told not to.
    This function strips those fences before calling json.loads().
    """
    text = text.strip()

    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fenced:
        text = fenced.group(1).strip()

    return json.loads(text)
