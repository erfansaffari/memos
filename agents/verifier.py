"""
Verifier Agent — detects contradictions between a new memory and existing ones.

Uses Claude Haiku to compare a newly extracted memory against all existing
memories at the same level. When a contradiction is found, the agent returns
a resolution strategy that drives confidence decay rather than hard deletion.

Resolution strategies:
- "update":     New memory supersedes the old (clear factual update). Decay old by 0.5.
- "decay":      Old memory is less reliable but may still be partially valid. Decay by 0.6.
- "keep_both":  Both can coexist (e.g., different time periods, not mutually exclusive).
- "none":       No conflict detected.

This models epistemic uncertainty: we never discard knowledge, we just reduce
our confidence in it — allowing rollback if the new memory turns out wrong.
"""

from __future__ import annotations

from anthropic import Anthropic

from memory.store import ConflictReport, MemoryItem
from utils import parse_json

VERIFIER_SYSTEM_PROMPT = """\
You are a memory verifier agent for MemOS, a hierarchical AI memory system.

Given a NEW memory and a list of EXISTING memories at the same level, detect
factual contradictions — cases where the new memory directly conflicts with an
existing one on the same factual claim.

What counts as a contradiction:
- "User uses React" vs "User switched to Svelte" (technology swap)
- "User is studying at Waterloo" vs "User graduated from Waterloo" (status change)
- "Project uses SQLite" vs "Project migrated to PostgreSQL" (architecture change)

What does NOT count as a contradiction:
- Different topics entirely
- Style or preference differences that can coexist
- More specific detail about the same fact (not contradictory — just more detail)
- Temporal statements that can both be historically true

Resolutions:
- "update":    New memory clearly supersedes old (definitive factual update)
- "decay":     Old memory becomes less reliable but may still hold partial truth
- "keep_both": Both statements can be simultaneously valid
- "none":      No contradiction detected

Respond ONLY with valid JSON — no prose, no markdown:
{
  "has_conflict": true,
  "old_memory_id": "first-8-chars-of-uuid-or-null",
  "resolution": "update",
  "explanation": "brief one-line explanation"
}
"""


class VerifierAgent:
    """Checks a new memory for contradictions against existing same-level memories."""

    MODEL = "claude-haiku-4-5-20251001"

    def __init__(self, client: Anthropic) -> None:
        self.client = client

    def verify(
        self,
        new_memory: MemoryItem,
        existing_memories: list[MemoryItem],
    ) -> ConflictReport:
        """
        Compare new_memory against existing_memories at the same level.

        Returns a ConflictReport describing whether a contradiction exists
        and what resolution to apply.
        """
        if not existing_memories:
            return ConflictReport(
                has_conflict=False,
                resolution="none",
                explanation="No existing memories at this level to check against.",
            )

        existing_text = "\n".join(
            f"[{m.id[:8]}] {m.content}" for m in existing_memories
        )

        user_content = (
            f"NEW MEMORY:\n{new_memory.content}\n\n"
            f"EXISTING MEMORIES (level {int(new_memory.level)}):\n{existing_text}"
        )

        response = self.client.messages.create(
            model=self.MODEL,
            max_tokens=512,
            system=VERIFIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )

        text = response.content[0].text.strip()

        try:
            data = parse_json(text)
        except (ValueError, KeyError):
            return ConflictReport(
                has_conflict=False,
                resolution="none",
                explanation="JSON parse error — no conflict assumed.",
            )

        # Resolve short ID prefix back to a full UUID
        short_id = data.get("old_memory_id")
        full_id: str | None = None
        if short_id:
            for m in existing_memories:
                if m.id.startswith(short_id) or m.id == short_id:
                    full_id = m.id
                    break

        return ConflictReport(
            has_conflict=bool(data.get("has_conflict", False)),
            old_memory_id=full_id,
            new_memory=new_memory,
            resolution=data.get("resolution", "none"),
            explanation=data.get("explanation", ""),
        )
