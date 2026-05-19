"""
LangGraph agent graph for MemOS.

Defines the MemOSState TypedDict and the 5-node pipeline:

    retrieve → respond → extract → verify → store

Each node is a pure function that receives the full state and returns
a dict of updated keys. LangGraph merges these updates into the state.

Transparency is a first-class concern: every node appends a log entry
so the caller can see exactly what happened at each step.
"""

from __future__ import annotations

from typing import Any, TypedDict

from anthropic import Anthropic
from langgraph.graph import END, StateGraph

from agents.extractor import ExtractionAgent
from agents.verifier import VerifierAgent
from memory.store import ConflictReport, MemoryItem, MemoryLevel, MemoryStore
from memory.vector_store import VectorStore
from retrieval.router import RetrievalRouter, format_context
from utils import dedupe_by_content, is_near_duplicate_distance

# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------

LEVEL_LABELS = {1: "Identity", 2: "Project", 3: "Episodic"}

RESPOND_SYSTEM_TEMPLATE = """\
You are a helpful AI assistant with persistent long-term memory about this user.

<memory_context>
{memory_context}
</memory_context>

Use the memory context above to personalize your response.
If the context contains relevant facts (skills, background, preferences), use them directly — do not ask the user for information you already have.
Be natural — do not explicitly say "according to my memory" unless it genuinely adds value.
If the context is empty or irrelevant, just respond normally.
"""


class MemOSState(TypedDict):
    user_message: str
    session_id: str
    retrieved_memories: list
    retrieval_meta: dict
    context_block: str
    assistant_response: str
    new_memories: list       # list[dict] — serialised MemoryItems
    extraction_reasoning: str
    conflict_reports: list   # list[dict] — serialised ConflictReports
    stored_count: int
    skipped_count: int
    log: list[str]           # appended by each node for transparency


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph(
    anthropic_client: Anthropic,
    memory_store: MemoryStore,
    vector_store: VectorStore,
) -> Any:
    """
    Compile and return the MemOS LangGraph runnable.

    All agents share the same Anthropic client and storage instances.
    """
    router = RetrievalRouter(anthropic_client, memory_store, vector_store)
    extractor = ExtractionAgent(anthropic_client)
    verifier = VerifierAgent(anthropic_client)

    # ------------------------------------------------------------------
    # Node 1: retrieve
    # ------------------------------------------------------------------

    def retrieve_node(state: MemOSState) -> dict:
        logs = list(state.get("log", []))
        query = state["user_message"]

        memories, meta = router.retrieve(query)
        routing = meta.get("routing", meta)

        logs.append(
            f"[retrieve] routing → intent={routing.get('intent', '?')} "
            f"levels={routing.get('levels', [])} budget={meta.get('budget', '?')} "
            f"| {routing.get('reasoning', '')}"
        )

        # Deduplicate similar memories so 5x "User is Erfan" don't crowd out skills
        retrieved_dicts = [_memory_item_to_dict(m) for m in memories]
        retrieved_dicts = dedupe_by_content(retrieved_dicts)

        context_block = format_context(retrieved_dicts, meta.get("budget", "medium"))

        for item in retrieved_dicts:
            memory_store.update_frequency(item["id"])

        logs.append(f"[retrieve] fetched {len(retrieved_dicts)} memories into context")

        return {
            "retrieved_memories": retrieved_dicts,
            "retrieval_meta": {**routing, "budget": meta.get("budget")},
            "context_block": context_block,
            "log": logs,
        }

    # ------------------------------------------------------------------
    # Node 2: respond
    # ------------------------------------------------------------------

    def respond_node(state: MemOSState) -> dict:
        logs = list(state.get("log", []))

        system_prompt = RESPOND_SYSTEM_TEMPLATE.format(
            memory_context=state["context_block"]
        )

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": state["user_message"]}],
        )

        assistant_response = response.content[0].text
        logs.append(f"[respond] generated {len(assistant_response)} char response")

        return {
            "assistant_response": assistant_response,
            "log": logs,
        }

    # ------------------------------------------------------------------
    # Node 3: extract
    # ------------------------------------------------------------------

    def extract_node(state: MemOSState) -> dict:
        logs = list(state.get("log", []))

        result = extractor.extract(
            state["user_message"], state["assistant_response"]
        )

        logs.append(
            f"[extract] {len(result.memories)} memories extracted | "
            f"{result.reasoning[:80]}"
        )

        return {
            "new_memories": [m.model_dump(mode="json") for m in result.memories],
            "extraction_reasoning": result.reasoning,
            "log": logs,
        }

    # ------------------------------------------------------------------
    # Node 4: verify
    # ------------------------------------------------------------------

    def verify_node(state: MemOSState) -> dict:
        logs = list(state.get("log", []))
        reports: list[dict] = []

        for mem_dict in state.get("new_memories", []):
            new_mem = MemoryItem(**mem_dict)
            existing = memory_store.get_by_level(int(new_mem.level))
            report = verifier.verify(new_mem, existing)
            reports.append(report.model_dump(mode="json"))

            if report.has_conflict:
                logs.append(
                    f"[verify] conflict on '{new_mem.summary[:40]}' "
                    f"→ resolution={report.resolution}"
                )

        total = len(state.get("new_memories", []))
        conflicts = sum(1 for r in reports if r.get("has_conflict"))
        logs.append(f"[verify] checked {total} memories, {conflicts} conflicts found")

        return {
            "conflict_reports": reports,
            "log": logs,
        }

    # ------------------------------------------------------------------
    # Node 5: store
    # ------------------------------------------------------------------

    DECAY_FACTORS: dict[str, float] = {
        "update": 0.5,
        "decay": 0.6,
    }

    def store_node(state: MemOSState) -> dict:
        logs = list(state.get("log", []))

        # Apply confidence decay to memories that were contradicted
        for report_dict in state.get("conflict_reports", []):
            report = ConflictReport(**report_dict)
            if report.has_conflict and report.old_memory_id:
                factor = DECAY_FACTORS.get(report.resolution)
                if factor:
                    memory_store.decay_confidence(report.old_memory_id, factor)
                    logs.append(
                        f"[store] decayed {report.old_memory_id[:8]}… "
                        f"(×{factor}) — {report.resolution}"
                    )

        # Persist all newly extracted memories (dual write: SQLite + ChromaDB)
        stored = 0
        skipped = 0
        for i, mem_dict in enumerate(state.get("new_memories", [])):
            new_mem = MemoryItem(**mem_dict)

            # Skip near-duplicates detected by vector similarity
            similar = vector_store.search(
                new_mem.content,
                n_results=1,
                level=int(new_mem.level),
                min_confidence=0.0,
            )
            if similar and is_near_duplicate_distance(similar[0]["distance"]):
                memory_store.update_frequency(similar[0]["id"])
                skipped += 1
                logs.append(
                    f"[store] skipped duplicate '{new_mem.summary[:40]}' "
                    f"(matches existing {similar[0]['id'][:8]}…)"
                )
                continue

            # Skip if verifier flagged as duplicate of an existing memory
            if i < len(state.get("conflict_reports", [])):
                report = ConflictReport(**state["conflict_reports"][i])
                if report.has_conflict and report.resolution == "duplicate":
                    skipped += 1
                    logs.append(
                        f"[store] skipped duplicate '{new_mem.summary[:40]}' (verifier)"
                    )
                    continue

            memory_store.save(new_mem)
            vector_store.upsert(new_mem)
            stored += 1

        logs.append(f"[store] wrote {stored} memories, skipped {skipped} duplicates")

        return {"log": logs, "stored_count": stored, "skipped_count": skipped}

    # ------------------------------------------------------------------
    # Assemble the graph
    # ------------------------------------------------------------------

    graph = StateGraph(MemOSState)

    graph.add_node("retrieve", retrieve_node)
    graph.add_node("respond", respond_node)
    graph.add_node("extract", extract_node)
    graph.add_node("verify", verify_node)
    graph.add_node("store", store_node)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "respond")
    graph.add_edge("respond", "extract")
    graph.add_edge("extract", "verify")
    graph.add_edge("verify", "store")
    graph.add_edge("store", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_item_to_dict(item: MemoryItem) -> dict:
    """Convert a MemoryItem to a plain dict for state serialisation."""
    return {
        "id": item.id,
        "content": item.content,
        "summary": item.summary,
        "level": int(item.level),
        "importance": item.importance,
        "confidence": item.confidence,
        "tags": item.tags,
        "project": item.project,
    }
