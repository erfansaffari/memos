"""
Shared utilities for MemOS.
"""

from __future__ import annotations

import json
import re


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
