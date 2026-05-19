"""
Hierarchical Retrieval Router — the core research contribution of MemOS.

The router uses Claude Haiku to classify each user query and determine:
  - which memory levels to search (subset of [1, 2, 3])
  - the retrieval budget ("shallow" / "medium" / "deep")

This is what makes MemOS different from a simple memory chatbot.
Instead of blindly fetching the top-K memories, retrieval is adaptive:
a simple identity question only needs Level 1 (3 memories, cheap),
while a deep technical question triggers Level 3 (20 memories, full context).

Budget → max memories per level:
  shallow → 3   (simple / factual queries)
  medium  → 8   (project-related queries)
  deep    → 20  (complex / historical / technical queries)
"""

from __future__ import annotations

from anthropic import Anthropic

from utils import parse_json

ROUTER_SYSTEM_PROMPT = """\
You are a memory retrieval router for MemOS, a hierarchical AI memory system.

Given a user query, decide which memory levels to retrieve and at what depth.

Memory levels:
- Level 1 (Identity):  Stable facts about the user — name, preferences, occupation, skills
- Level 2 (Project):   Ongoing projects, technical decisions, tools, architecture choices
- Level 3 (Episodic):  Session-specific details, code snippets, bugs fixed, experiments run

Budget options:
- "shallow" → simple/factual queries — only identity info needed (up to 3 memories)
- "medium"  → project-related queries — architecture/tool context needed (up to 8 memories)
- "deep"    → past decisions, historical reasoning, experiments — full context (up to 20 memories)

Level selection rules (BE GENEROUS — when in doubt, include the level):
- Always include Level 1.
- Include Level 2 when the query:
    * Mentions a project by name (e.g. "MemOS", "the project", "this app")
    * Asks about tools, tech stack, architecture, design, or "how does X work"
    * Asks "what is X built with", "what database/model/library does X use"
    * Asks about any technical component or system design decision
- Include Level 3 when the query:
    * Asks "why did we decide/choose/pick X" or "what did we decide about"
    * Asks about past sessions, bugs fixed, experiments run, or their results
    * Uses past tense about technical choices ("why do we use X instead of Y")
    * Asks for reasoning behind an architectural or tool choice

Budget rules:
- "shallow"  → query ONLY about the user's personal identity (name, background, preferences)
- "medium"   → query about project architecture, tools, design patterns
- "deep"     → query about past reasoning, historical decisions, experiment results, specific session details

Examples:
- "What is my name?" → levels=[1], budget="shallow"
- "What is MemOS built with?" → levels=[1,2], budget="medium"
- "How does the memory hierarchy work?" → levels=[1,2], budget="medium"
- "Why do we use ChromaDB instead of Pinecone?" → levels=[1,2,3], budget="deep"
- "What bug did we fix last session?" → levels=[1,3], budget="deep"
- "Why do we use Haiku for agents instead of Sonnet?" → levels=[1,2,3], budget="deep"

Respond ONLY with valid JSON — no prose, no markdown fences:
{"levels": [1, 2], "budget": "medium", "reasoning": "brief one-line explanation"}
"""

BUDGET_MAP: dict[str, int] = {
    "shallow": 3,
    "medium": 8,
    "deep": 20,
}


class RetrievalRouter:
    """Classifies user queries into retrieval plans using Claude Haiku."""

    def __init__(self, client: Anthropic) -> None:
        self.client = client

    def classify(self, query: str) -> dict:
        """
        Classify a query and return a retrieval plan.

        Returns:
            {
                "levels": [1, 2],          # memory levels to search
                "budget": "medium",         # retrieval depth
                "reasoning": "...",         # one-line explanation
                "n": 8,                     # convenience: max memories per level
            }
        """
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=ROUTER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Query: {query}"}],
        )

        text = response.content[0].text.strip()

        try:
            result = parse_json(text)
        except (ValueError, KeyError):
            result = {
                "levels": [1],
                "budget": "shallow",
                "reasoning": "JSON parse fallback — defaulting to Level 1 shallow.",
            }

        # Ensure level 1 is always present
        levels = result.get("levels", [1])
        if not isinstance(levels, list):
            levels = [1]
        if 1 not in levels:
            levels.insert(0, 1)

        budget = result.get("budget", "shallow")
        if budget not in BUDGET_MAP:
            budget = "shallow"

        return {
            "levels": levels,
            "budget": budget,
            "reasoning": result.get("reasoning", ""),
            "n": BUDGET_MAP[budget],
        }
