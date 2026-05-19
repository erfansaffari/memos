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
