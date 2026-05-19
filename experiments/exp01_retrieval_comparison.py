"""
Experiment 01 — Flat vs Hierarchical Retrieval Comparison

Research question:
    Does hierarchical memory retrieval — where context is fetched progressively
    at increasing levels of detail — produce more relevant and token-efficient
    AI context than flat vector search?

Method:
    1. Seed the memory store with 14 diverse memories across all 3 levels.
    2. Define 10 test queries ranging from simple identity questions
       to deep technical / historical queries.
    3. For each query, run BOTH retrieval strategies:
         - Hierarchical: router classifies query → level-filtered vector search
         - Flat:         simple top-N vector search across all memories, no level awareness
    4. Judge relevance of each retrieved context with Claude Haiku (0–10 score).
    5. Log all results to Weights & Biases (optional; skipped if WANDB_API_KEY unset).

Metrics logged per query:
    - hierarchical_score    (0–10)
    - flat_score            (0–10)
    - score_delta           (hierarchical - flat)
    - hierarchical_n        (number of memories retrieved)
    - flat_n                (number of memories retrieved)
    - hierarchical_efficiency  (score / n)
    - flat_efficiency          (score / n)
    - routing_levels        (e.g. [1, 2])
    - routing_budget        (e.g. "medium")

Run:
    python experiments/exp01_retrieval_comparison.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

from utils import parse_json  # noqa: E402 — must come after sys.path insert


# ---------------------------------------------------------------------------
# Seed data — 14 diverse memories across levels 1, 2, 3
# ---------------------------------------------------------------------------

SEED_MEMORIES_RAW = [
    # Level 1 — Identity (4)
    {
        "content": "My name is Erfan Saffari. I am a CS student at the University of Waterloo.",
        "summary": "User is Erfan Saffari, CS student at Waterloo.",
        "tags": ["identity", "name", "university"],
        "level": 1,
        "importance": 0.95,
        "project": None,
    },
    {
        "content": "I primarily code in Python and prefer clean, well-typed code with type hints.",
        "summary": "User prefers Python with type hints.",
        "tags": ["python", "preferences", "coding"],
        "level": 1,
        "importance": 0.85,
        "project": None,
    },
    {
        "content": "I am applying to WAT.ai, a machine learning research group at the University of Waterloo.",
        "summary": "User is applying to WAT.ai ML research group.",
        "tags": ["wat.ai", "ml", "research", "application"],
        "level": 1,
        "importance": 0.90,
        "project": None,
    },
    {
        "content": "I prefer working in the terminal and use VS Code as my primary editor with Vim keybindings.",
        "summary": "User uses VS Code with Vim keybindings in terminal.",
        "tags": ["editor", "vscode", "vim", "terminal"],
        "level": 1,
        "importance": 0.70,
        "project": None,
    },
    # Level 2 — Project (5)
    {
        "content": "MemOS is built using LangGraph for agent orchestration and the Anthropic Claude API for LLM inference.",
        "summary": "MemOS uses LangGraph and Anthropic Claude API.",
        "tags": ["memos", "langgraph", "anthropic", "architecture"],
        "level": 2,
        "importance": 0.90,
        "project": "memos",
    },
    {
        "content": "The vector store for MemOS uses ChromaDB with sentence-transformers all-MiniLM-L6-v2 embeddings (384-dim, local).",
        "summary": "MemOS uses ChromaDB with all-MiniLM-L6-v2 embeddings.",
        "tags": ["chromadb", "embeddings", "sentence-transformers", "vector-store"],
        "level": 2,
        "importance": 0.88,
        "project": "memos",
    },
    {
        "content": "MemOS uses SQLAlchemy with SQLite for structured memory metadata — enabling confidence filtering and level-based queries.",
        "summary": "MemOS uses SQLite via SQLAlchemy for metadata.",
        "tags": ["sqlite", "sqlalchemy", "metadata", "database"],
        "level": 2,
        "importance": 0.85,
        "project": "memos",
    },
    {
        "content": "The MemOS memory hierarchy has 3 levels: Level 1 (Identity), Level 2 (Project), Level 3 (Episodic). The router selects levels dynamically per query.",
        "summary": "MemOS has a 3-level memory hierarchy with dynamic routing.",
        "tags": ["hierarchy", "levels", "routing", "memos"],
        "level": 2,
        "importance": 0.92,
        "project": "memos",
    },
    {
        "content": "The MemOS CLI is built with Typer and Rich for clean terminal UX with tables and styled output.",
        "summary": "MemOS CLI uses Typer and Rich.",
        "tags": ["cli", "typer", "rich", "terminal"],
        "level": 2,
        "importance": 0.72,
        "project": "memos",
    },
    # Level 3 — Episodic (5)
    {
        "content": "Session 2026-05-15: Decided to use all-MiniLM-L6-v2 after benchmarking against all-mpnet-base-v2. The smaller model was fast enough on CPU and saved ~200MB.",
        "summary": "Chose all-MiniLM-L6-v2 over mpnet after benchmarking.",
        "tags": ["embedding-model", "benchmarking", "decision", "session-2026-05-15"],
        "level": 3,
        "importance": 0.80,
        "project": "memos",
    },
    {
        "content": "Session 2026-05-16: Fixed a bug where ChromaDB was not persisting between sessions. Root cause: using EphemeralClient instead of PersistentClient.",
        "summary": "Fixed ChromaDB persistence bug — use PersistentClient.",
        "tags": ["bug-fix", "chromadb", "persistence", "session-2026-05-16"],
        "level": 3,
        "importance": 0.85,
        "project": "memos",
    },
    {
        "content": "Session 2026-05-17: Ran a preliminary retrieval experiment. Hierarchical retrieval scored 2.3 points higher on average than flat retrieval across 5 test queries.",
        "summary": "Preliminary experiment: hierarchical +2.3 over flat.",
        "tags": ["experiment", "retrieval", "results", "session-2026-05-17"],
        "level": 3,
        "importance": 0.88,
        "project": "memos",
    },
    {
        "content": "Session 2026-05-18: Decided the verifier agent should apply confidence decay rather than hard deletion for contradicted memories, to model epistemic uncertainty.",
        "summary": "Verifier uses confidence decay, not hard delete.",
        "tags": ["verifier", "confidence", "decay", "design-decision"],
        "level": 3,
        "importance": 0.87,
        "project": "memos",
    },
    {
        "content": "Session 2026-05-18: The retrieval router uses claude-haiku-4-5 for classification to minimise latency and cost. Claude Sonnet is reserved for user-facing responses only.",
        "summary": "Router uses Haiku for cost; Sonnet for user responses only.",
        "tags": ["router", "haiku", "sonnet", "cost", "design"],
        "level": 3,
        "importance": 0.83,
        "project": "memos",
    },
]

# ---------------------------------------------------------------------------
# Test queries — 10 queries spanning all complexity levels
# ---------------------------------------------------------------------------

TEST_QUERIES = [
    {
        "id": "q01",
        "query": "What is my name?",
        "expected_level": 1,
        "description": "Simple identity — should need only Level 1",
    },
    {
        "id": "q02",
        "query": "What university do I go to?",
        "expected_level": 1,
        "description": "Simple identity — should need only Level 1",
    },
    {
        "id": "q03",
        "query": "What programming language do I prefer?",
        "expected_level": 1,
        "description": "Preference question — Level 1",
    },
    {
        "id": "q04",
        "query": "What is MemOS built with? What are its main technologies?",
        "expected_level": 2,
        "description": "Project architecture — Level 2",
    },
    {
        "id": "q05",
        "query": "What embedding model does MemOS use for vector search?",
        "expected_level": 2,
        "description": "Specific project decision — Level 2/3",
    },
    {
        "id": "q06",
        "query": "How does the memory hierarchy work in MemOS?",
        "expected_level": 2,
        "description": "Project design — Level 2",
    },
    {
        "id": "q07",
        "query": "What bug did we fix with ChromaDB and what was the root cause?",
        "expected_level": 3,
        "description": "Session-specific bug fix — Level 3",
    },
    {
        "id": "q08",
        "query": "What did we decide about how to handle memory contradictions?",
        "expected_level": 3,
        "description": "Past design decision — Level 3",
    },
    {
        "id": "q09",
        "query": "What were the results of the retrieval comparison experiment?",
        "expected_level": 3,
        "description": "Past experiment results — Level 3",
    },
    {
        "id": "q10",
        "query": "Why do we use Claude Haiku for the router instead of Sonnet?",
        "expected_level": 3,
        "description": "Historical reasoning — Level 3",
    },
]

# ---------------------------------------------------------------------------
# Relevance judge
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """\
You are a relevance evaluator for a memory retrieval system.

Given a query and a retrieved memory context, score how relevant and useful
the context is for answering the query.

Score 0–10:
- 0:  Completely irrelevant — context contains nothing useful for the query
- 3:  Weakly relevant — touches the topic but lacks the specific answer
- 5:  Moderately relevant — partially answers the query
- 7:  Highly relevant — context directly answers the query
- 10: Perfect — context contains exactly what is needed, nothing extraneous

Consider both relevance (does it contain the answer?) and efficiency
(is the context clean, without irrelevant noise?).

Respond ONLY with valid JSON — no prose:
{"score": 7, "reasoning": "one-line explanation"}
"""


def judge_relevance(query: str, context: str, client) -> tuple[float, str]:
    """Score the relevance of a retrieved context for a query using Claude Haiku."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Query: {query}\n\nMemory Context:\n{context}",
            }
        ],
    )
    text = response.content[0].text.strip()
    try:
        data = parse_json(text)
        return float(data.get("score", 0)), data.get("reasoning", "")
    except (ValueError, KeyError):
        return 0.0, f"JSON parse error — raw: {text[:80]}"


# ---------------------------------------------------------------------------
# Retrieval strategies
# ---------------------------------------------------------------------------


def hierarchical_retrieve(query: str, router, vector_store, memory_store) -> tuple[list[str], dict]:
    """Level-aware retrieval guided by the router."""
    from retrieval.router import BUDGET_MAP

    routing = router.classify(query)
    levels: list[int] = routing["levels"]
    n: int = routing["n"]

    # Always include top-3 Level 1 memories
    level1 = memory_store.get_by_level(1)[:3]
    retrieved_ids: set[str] = {m.id for m in level1}
    context_lines: list[str] = [
        f"[Identity] {m.content}" for m in level1
    ]

    for level in levels:
        if level == 1:
            continue
        label = {2: "Project", 3: "Episodic"}.get(level, f"Level{level}")
        hits = vector_store.search(query, n_results=n, level=level)
        for hit in hits:
            if hit["id"] not in retrieved_ids:
                retrieved_ids.add(hit["id"])
                context_lines.append(f"[{label}] {hit['content']}")

    return context_lines, routing


def flat_retrieve(query: str, vector_store, n: int = 10) -> list[str]:
    """Flat top-N retrieval — no level awareness."""
    hits = vector_store.search(query, n_results=n, min_confidence=0.0)
    return [hit["content"] for hit in hits]


# ---------------------------------------------------------------------------
# Main experiment runner
# ---------------------------------------------------------------------------


def run_experiment() -> None:
    from anthropic import Anthropic
    from memory.store import MemoryItem, MemoryLevel, MemoryStore
    from memory.vector_store import VectorStore
    from retrieval.router import RetrievalRouter

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Exiting.")
        sys.exit(1)

    client = Anthropic(api_key=api_key)

    # Use isolated temp directories so the experiment never pollutes the real DB
    tmp_dir = Path(tempfile.mkdtemp(prefix="memos_exp01_"))
    print(f"\n{'='*60}")
    print("MemOS — Experiment 01: Flat vs Hierarchical Retrieval")
    print(f"{'='*60}")
    print(f"Temp store: {tmp_dir}")

    memory_store = MemoryStore(db_path=str(tmp_dir / "memos.db"))
    vector_store = VectorStore(persist_path=str(tmp_dir / "chroma"))
    router = RetrievalRouter(client)

    # ------------------------------------------------------------------
    # Step 1: Seed memories
    # ------------------------------------------------------------------
    print("\n[1/3] Seeding 14 memories across levels 1, 2, 3…")
    for raw in SEED_MEMORIES_RAW:
        item = MemoryItem(
            id=str(uuid.uuid4()),
            content=raw["content"],
            summary=raw["summary"],
            tags=raw["tags"],
            level=MemoryLevel(raw["level"]),
            importance=raw["importance"],
            confidence=1.0,
            source_model="seed",
            project=raw.get("project"),
            created_at=datetime.now(timezone.utc),
            last_used=datetime.now(timezone.utc),
            frequency=0,
        )
        memory_store.save(item)
        vector_store.upsert(item)

    counts = memory_store.count_by_level()
    print(f"   Seeded: L1={counts[1]}  L2={counts[2]}  L3={counts[3]}")

    # ------------------------------------------------------------------
    # Step 2: Run retrieval comparison on all 10 queries
    # ------------------------------------------------------------------
    print("\n[2/3] Running retrieval comparison on 10 queries…\n")

    results = []
    for test in TEST_QUERIES:
        qid = test["id"]
        query = test["query"]
        desc = test["description"]

        # Hierarchical
        hier_lines, routing = hierarchical_retrieve(query, router, vector_store, memory_store)
        hier_context = "\n".join(hier_lines) if hier_lines else "(empty)"
        hier_score, hier_reason = judge_relevance(query, hier_context, client)

        # Flat — use same n as the router's budget for fair comparison
        flat_n = routing["n"]
        flat_lines = flat_retrieve(query, vector_store, n=flat_n)
        flat_context = "\n".join(flat_lines) if flat_lines else "(empty)"
        flat_score, flat_reason = judge_relevance(query, flat_context, client)

        delta = hier_score - flat_score
        hier_n = len(hier_lines)
        flat_n_actual = len(flat_lines)
        hier_eff = hier_score / max(hier_n, 1)
        flat_eff = flat_score / max(flat_n_actual, 1)

        result = {
            "id": qid,
            "query": query,
            "description": desc,
            "expected_level": test["expected_level"],
            "routing_levels": routing["levels"],
            "routing_budget": routing["budget"],
            "routing_reasoning": routing.get("reasoning", ""),
            "hierarchical_score": hier_score,
            "hierarchical_n": hier_n,
            "hierarchical_efficiency": round(hier_eff, 3),
            "hierarchical_reasoning": hier_reason,
            "flat_score": flat_score,
            "flat_n": flat_n_actual,
            "flat_efficiency": round(flat_eff, 3),
            "flat_reasoning": flat_reason,
            "score_delta": round(delta, 2),
        }
        results.append(result)

        # Console output per query
        delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
        delta_color = "✓" if delta > 0 else ("✗" if delta < 0 else "=")
        print(
            f"  {qid}  [{delta_color} {delta_str:>5}]  "
            f"Hier={hier_score:.1f}({hier_n}mem)  "
            f"Flat={flat_score:.1f}({flat_n_actual}mem)  "
            f"  {desc}"
        )

    # ------------------------------------------------------------------
    # Step 3: Summarise & log to W&B
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")

    mean_hier = sum(r["hierarchical_score"] for r in results) / len(results)
    mean_flat = sum(r["flat_score"] for r in results) / len(results)
    mean_delta = sum(r["score_delta"] for r in results) / len(results)
    mean_hier_eff = sum(r["hierarchical_efficiency"] for r in results) / len(results)
    mean_flat_eff = sum(r["flat_efficiency"] for r in results) / len(results)
    hier_wins = sum(1 for r in results if r["score_delta"] > 0)
    flat_wins = sum(1 for r in results if r["score_delta"] < 0)
    ties = sum(1 for r in results if r["score_delta"] == 0)

    print(f"  Mean Hierarchical Score : {mean_hier:.2f}/10")
    print(f"  Mean Flat Score         : {mean_flat:.2f}/10")
    print(f"  Mean Score Delta        : {mean_delta:+.2f} (hierarchical − flat)")
    print(f"  Mean Hierarchical Eff.  : {mean_hier_eff:.3f} (score/memory)")
    print(f"  Mean Flat Efficiency    : {mean_flat_eff:.3f} (score/memory)")
    print(f"  Wins: Hierarchical={hier_wins}  Flat={flat_wins}  Ties={ties}")
    print(f"{'='*60}\n")

    # Per-query breakdown table
    print(f"  {'ID':<5} {'Query':<45} {'Hier':>5} {'Flat':>5} {'Delta':>6}")
    print(f"  {'-'*5} {'-'*45} {'-'*5} {'-'*5} {'-'*6}")
    for r in results:
        delta_str = f"{r['score_delta']:+.1f}"
        print(
            f"  {r['id']:<5} {r['query'][:43]:<45} "
            f"{r['hierarchical_score']:>5.1f} {r['flat_score']:>5.1f} {delta_str:>6}"
        )
    print()

    # ------------------------------------------------------------------
    # Optional: W&B logging
    # ------------------------------------------------------------------
    wandb_key = os.environ.get("WANDB_API_KEY", "").strip()
    wandb_project = os.environ.get("WANDB_PROJECT", "memos")

    if wandb_key:
        try:
            import wandb  # type: ignore

            run = wandb.init(
                project=wandb_project,
                name="exp01-flat-vs-hierarchical",
                config={
                    "n_queries": len(TEST_QUERIES),
                    "n_seed_memories": len(SEED_MEMORIES_RAW),
                    "embedding_model": "all-MiniLM-L6-v2",
                    "judge_model": "claude-haiku-4-5-20251001",
                },
                tags=["exp01", "retrieval-comparison"],
            )

            # Summary metrics
            wandb.log(
                {
                    "mean_hierarchical_score": mean_hier,
                    "mean_flat_score": mean_flat,
                    "mean_score_delta": mean_delta,
                    "mean_hierarchical_efficiency": mean_hier_eff,
                    "mean_flat_efficiency": mean_flat_eff,
                    "hierarchical_wins": hier_wins,
                    "flat_wins": flat_wins,
                    "ties": ties,
                }
            )

            # Per-query table
            columns = [
                "id", "query", "expected_level", "routing_budget",
                "hierarchical_score", "flat_score", "score_delta",
                "hierarchical_n", "flat_n",
                "hierarchical_efficiency", "flat_efficiency",
            ]
            data_rows = [[r[c] for c in columns] for r in results]
            table = wandb.Table(columns=columns, data=data_rows)
            wandb.log({"per_query_results": table})

            # Bar chart: score comparison
            for r in results:
                wandb.log(
                    {
                        f"query/{r['id']}/hierarchical_score": r["hierarchical_score"],
                        f"query/{r['id']}/flat_score": r["flat_score"],
                        f"query/{r['id']}/score_delta": r["score_delta"],
                    }
                )

            run.finish()
            print(f"W&B run logged: {run.url}")
        except ImportError:
            print("wandb not installed — skipping W&B logging.")
        except Exception as exc:
            print(f"W&B logging failed: {exc}")
    else:
        print("WANDB_API_KEY not set — skipping W&B logging.")
        print("Set it in .env to enable experiment tracking.\n")

    # Save results locally as JSON regardless of W&B
    out_path = Path(__file__).parent / "exp01_results.json"
    with open(out_path, "w") as f:
        json.dump(
            {
                "run_at": datetime.now(timezone.utc).isoformat(),
                "summary": {
                    "mean_hierarchical_score": mean_hier,
                    "mean_flat_score": mean_flat,
                    "mean_score_delta": mean_delta,
                    "mean_hierarchical_efficiency": mean_hier_eff,
                    "mean_flat_efficiency": mean_flat_eff,
                    "hierarchical_wins": hier_wins,
                    "flat_wins": flat_wins,
                    "ties": ties,
                },
                "queries": results,
            },
            f,
            indent=2,
        )
    print(f"Results saved to {out_path}\n")


if __name__ == "__main__":
    run_experiment()
