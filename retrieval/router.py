"""
Hierarchical Retrieval Router — the core research contribution of MemOS.

v2: Adds a four-intent classification system so the router can distinguish
    "design-reasoning" queries (WHY/HOW something was designed) from plain
    project queries, routing them to Level 3 episodic memories where design
    rationale lives.

Intent types:
  factual          → levels=[1],       budget=shallow   (name, basic facts)
  project          → levels=[1,2],     budget=medium    (what stack, what tools)
  design-reasoning → levels=[1,2,3],   budget=deep      (why/how was X designed)
  episodic         → levels=[2,3],     budget=deep      (bugs, session details)

Critical fix from Experiment 1:
  Both failing queries ("How does the memory hierarchy work?" and
  "Why do we use Claude Haiku?") are design-reasoning queries that were
  previously routed as project/medium, missing the Level 3 memories that
  actually contain the answers.

Budget → max memories per level:
  shallow → 3
  medium  → 8
  deep    → 20

Public API:
  router.classify(query)            → dict  (used by graph.py — backwards compat)
  router.retrieve(query)            → (list[MemoryItem], dict)  (used by experiments)
  format_context(memories, budget)  → str   (module-level helper)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from anthropic import Anthropic

from memory.store import MemoryItem, MemoryLevel
from utils import parse_json

if TYPE_CHECKING:
    from memory.store import MemoryStore
    from memory.vector_store import VectorStore

# ---------------------------------------------------------------------------
# Router prompt — v2 with four intent types
# ---------------------------------------------------------------------------

_ROUTER_PROMPT = """\
You are a memory routing agent. Given a user query, classify its intent and decide how deep into memory to retrieve.

INTENT TYPES:
- "factual": Simple lookup — name, university, language preference, basic facts about the user.
  → levels: [1], budget: "shallow"

- "project": Project-related question — what stack, what tools, what architecture, what is X built with.
  → levels: [1, 2], budget: "medium"

- "design-reasoning": Questions asking WHY or HOW something was designed, decided, or chosen.
  Examples: "Why do we use X instead of Y?", "How does the memory hierarchy work?",
  "What was the reasoning behind X?", "Why did we choose X?", "How does X work in this project?"
  → levels: [1, 2, 3], budget: "deep"

- "episodic": Session-specific details — bugs fixed, experiments run, code decisions made in a specific session.
  Examples: "What bug did we fix?", "What were the results of the experiment?", "What did we decide last session?"
  → levels: [2, 3], budget: "deep"

BUDGET definitions:
- "shallow": fetch up to 3 memories — for simple factual lookups
- "medium":  fetch up to 8 memories — for project-context questions
- "deep":    fetch up to 20 memories — for design reasoning and episodic queries

CRITICAL RULES (apply these before anything else):
- Any question containing "why", "how does", "how did", "what was the reasoning", "how did you decide",
  "why do we use", "why did we choose" → ALWAYS "design-reasoning" intent
- Any question about bugs, experiments, session outcomes → ALWAYS "episodic" intent
- When uncertain between project and design-reasoning → choose design-reasoning
- "shallow" budget is ONLY for questions that are purely about personal identity facts

Examples:
- "What is my name?"                              → factual,          levels=[1],     shallow
- "What is MemOS built with?"                     → project,          levels=[1,2],   medium
- "How does the memory hierarchy work?"           → design-reasoning, levels=[1,2,3], deep
- "Why do we use Haiku instead of Sonnet?"        → design-reasoning, levels=[1,2,3], deep
- "What bug did we fix with ChromaDB?"            → episodic,         levels=[2,3],   deep
- "What were the experiment 1 results?"           → episodic,         levels=[2,3],   deep

Respond ONLY with valid JSON — no prose, no markdown fences:
{
  "intent": "factual|project|design-reasoning|episodic",
  "levels": [1],
  "budget": "shallow",
  "reasoning": "one sentence"
}
"""

BUDGET_MAP: dict[str, int] = {
    "shallow": 3,
    "medium": 8,
    "deep": 20,
}

LEVEL_LABELS: dict[int, str] = {
    1: "Identity",
    2: "Project",
    3: "Episodic",
}


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def format_context(memories: list, budget: str = "medium") -> str:
    """
    Format a list of MemoryItem objects (or dicts) into a readable context block.

    Used by experiments and graph.py to build the <memory_context> system prompt section.
    """
    lines: list[str] = []
    for m in memories:
        if isinstance(m, MemoryItem):
            label = LEVEL_LABELS.get(int(m.level), "Memory")
            content = m.content
        elif isinstance(m, dict):
            label = LEVEL_LABELS.get(int(m.get("level", 0)), "Memory")
            content = m.get("content", "")
        else:
            continue
        lines.append(f"[{label}] {content}")
    return "\n".join(lines) if lines else "(no relevant memories found)"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class RetrievalRouter:
    """
    Classifies user queries into retrieval plans and optionally executes retrieval.

    Two usage modes:
      1. Lightweight (graph.py): RetrievalRouter(client)
         → call classify(query) → get routing dict, do your own vector search

      2. Full pipeline (experiments): RetrievalRouter(client, store, vector_store)
         → call retrieve(query) → get (memories, meta) in one shot
    """

    def __init__(
        self,
        client: Anthropic,
        store: Optional["MemoryStore"] = None,
        vector_store: Optional["VectorStore"] = None,
    ) -> None:
        self.client = client
        self._store = store
        self._vector = vector_store

    # ------------------------------------------------------------------
    # Private: routing
    # ------------------------------------------------------------------

    def _route(self, query: str) -> dict:
        """
        Call Claude Haiku to classify the query intent and return a routing plan.

        Enforces invariants:
          - design-reasoning and episodic always get deep budget + Level 3
          - Level 1 always present
        """
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            system=_ROUTER_PROMPT,
            messages=[{"role": "user", "content": query}],
        )
        text = response.content[0].text.strip()

        try:
            data = parse_json(text)
        except (ValueError, KeyError):
            data = {
                "intent": "project",
                "levels": [1, 2],
                "budget": "medium",
                "reasoning": "JSON parse fallback",
            }

        intent = data.get("intent", "project")
        levels = data.get("levels", [1, 2])
        if not isinstance(levels, list):
            levels = [1, 2]

        # Enforce: design-reasoning and episodic must always fetch deep + Level 3
        if intent in ("design-reasoning", "episodic"):
            data["budget"] = "deep"
            if 3 not in levels:
                levels = sorted(set(levels) | {3})

        # Level 1 always present
        if 1 not in levels:
            levels = [1] + levels

        budget = data.get("budget", "medium")
        if budget not in BUDGET_MAP:
            budget = "medium"

        data["levels"] = levels
        data["budget"] = budget
        data["n"] = BUDGET_MAP[budget]
        return data

    # ------------------------------------------------------------------
    # Private: fetching
    # ------------------------------------------------------------------

    def _fetch(self, query: str, levels: list[int], budget: str) -> list[MemoryItem]:
        """
        Execute vector search per level and return de-duplicated MemoryItems.

        Always prepends top-3 Level 1 identity memories regardless of routing.
        Requires self._store and self._vector to be set.
        """
        if self._store is None or self._vector is None:
            raise RuntimeError(
                "RetrievalRouter._fetch() requires store and vector_store. "
                "Pass them to the constructor: RetrievalRouter(client, store, vector_store)"
            )

        n_per_level = BUDGET_MAP.get(budget, 8)
        memories: list[MemoryItem] = []
        seen_ids: set[str] = set()

        for level in sorted(levels):
            results = self._vector.query(query, level=level, n_results=n_per_level)
            for hit in results:
                if hit["id"] in seen_ids:
                    continue
                rec = self._store.get_by_id(hit["id"])
                if rec and rec.confidence >= 0.3:
                    memories.append(rec)
                    seen_ids.add(hit["id"])

        # Always prepend top-3 Level 1 identity memories
        top_identity = self._store.get_by_level(1, limit=3)
        for rec in top_identity:
            if rec.id not in seen_ids:
                memories.insert(0, rec)
                seen_ids.add(rec.id)

        return memories

    # ------------------------------------------------------------------
    # Public: full pipeline (experiments)
    # ------------------------------------------------------------------

    def retrieve(self, query: str) -> tuple[list[MemoryItem], dict]:
        """
        Route + fetch in one call. Returns (memories, meta).

        meta keys:
          budget      → "shallow" | "medium" | "deep"
          routing     → full routing dict (includes intent, reasoning, levels)
          levels_used → list[int] of levels that were searched
        """
        routing = self._route(query)
        memories = self._fetch(query, routing["levels"], routing["budget"])
        meta = {
            "budget": routing["budget"],
            "routing": routing,
            "levels_used": routing["levels"],
        }
        return memories, meta

    # ------------------------------------------------------------------
    # Public: lightweight classify (backwards compat for graph.py)
    # ------------------------------------------------------------------

    def classify(self, query: str) -> dict:
        """
        Classify a query and return a routing plan dict.

        Used by graph.py which does its own vector fetching.

        Returns:
            {
                "levels": [1, 2],
                "budget": "medium",
                "reasoning": "...",
                "n": 8,
                "intent": "project",
            }
        """
        routing = self._route(query)
        return {
            "levels": routing["levels"],
            "budget": routing["budget"],
            "reasoning": routing.get("reasoning", ""),
            "n": routing["n"],
            "intent": routing.get("intent", ""),
        }
