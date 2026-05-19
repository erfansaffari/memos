"""
Extraction Agent — reads a conversation turn and extracts structured memories.

Uses Claude Haiku (cheap, fast) to parse the user + assistant exchange and
produce a list of MemoryItem objects worth persisting for future sessions.

Key design decisions:
- Only extract information useful in a FUTURE session (not ephemeral context).
- Level assignment follows the 3-tier hierarchy strictly.
- Importance scores are conservative: most things are 0.3–0.7.
- Returns ExtractionResult with an empty memories list if nothing is worth storing.
"""

from __future__ import annotations

from anthropic import Anthropic

from memory.store import ExtractionResult, MemoryItem, MemoryLevel
from utils import parse_json

EXTRACTION_SYSTEM_PROMPT = """\
You are a memory extraction agent for MemOS, a hierarchical AI memory system.

Given a conversation turn (user message + assistant response), extract structured
memories that would be useful in a FUTURE session — not just context for this turn.

Memory levels:
- Level 1 (Identity): Stable facts about the user — name, role, preferences, skills.
  Extract SPARINGLY. Only truly stable, reusable facts. Example: "User is a CS student at Waterloo."
- Level 2 (Project): Project decisions, architecture choices, tools selected.
  Extract when a clear decision is made. Example: "MemOS uses ChromaDB for vector storage."
- Level 3 (Episodic): Session-specific details — code snippets, bugs fixed, experiments run, specific outcomes.
  Extract when there is concrete, dated detail worth recalling. Example: "Session 2026-05-18: fixed ChromaDB persistence bug."

For each memory:
- content: the full extractable fact (complete sentence, enough context to stand alone)
- summary: one-sentence summary (≤15 words)
- tags: 2–5 lowercase keywords
- level: 1, 2, or 3
- importance: 0.0–1.0 (be conservative — trivial=0.2, useful=0.5, important=0.7, critical=0.9)
- confidence: 1.0 for freshly stated facts
- source_model: "claude-haiku-4-5-20251001"
- project: project name if Level 2/3, otherwise null

Respond ONLY with valid JSON — no prose, no markdown fences:
{
  "reasoning": "why these memories were extracted",
  "memories": [
    {
      "content": "...",
      "summary": "...",
      "tags": ["tag1", "tag2"],
      "level": 2,
      "importance": 0.7,
      "confidence": 1.0,
      "source_model": "claude-haiku-4-5-20251001",
      "project": "memos"
    }
  ]
}

If nothing is worth storing, return: {"reasoning": "nothing worth extracting", "memories": []}
"""


class ExtractionAgent:
    """Extracts structured MemoryItems from a single conversation turn."""

    MODEL = "claude-haiku-4-5-20251001"

    def __init__(self, client: Anthropic) -> None:
        self.client = client

    def extract(self, user_message: str, assistant_response: str) -> ExtractionResult:
        """
        Parse one conversation turn and return structured memories.

        Args:
            user_message: The user's input.
            assistant_response: The assistant's reply.

        Returns:
            ExtractionResult with zero or more MemoryItems.
        """
        conversation = (
            f"User: {user_message}\n\nAssistant: {assistant_response}"
        )

        response = self.client.messages.create(
            model=self.MODEL,
            max_tokens=2048,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": conversation}],
        )

        text = response.content[0].text.strip()

        try:
            data = parse_json(text)
        except (ValueError, KeyError):
            return ExtractionResult(memories=[], reasoning="JSON parse error — skipping extraction.")

        memories: list[MemoryItem] = []
        for raw in data.get("memories", []):
            try:
                item = MemoryItem(
                    content=raw["content"],
                    summary=raw.get("summary", raw["content"][:80]),
                    tags=raw.get("tags", []),
                    level=MemoryLevel(int(raw.get("level", 3))),
                    importance=float(raw.get("importance", 0.5)),
                    confidence=float(raw.get("confidence", 1.0)),
                    source_model=raw.get("source_model", self.MODEL),
                    project=raw.get("project"),
                )
                memories.append(item)
            except (KeyError, ValueError, TypeError):
                # Malformed entry — skip silently
                continue

        return ExtractionResult(
            memories=memories,
            reasoning=data.get("reasoning", ""),
        )
