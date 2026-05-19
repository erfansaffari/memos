"""
Experiment 02 — Intent-Aware Router Fix

Research question:
  Does adding a "design-reasoning" intent class to the retrieval router
  fix the two failure cases from Experiment 1 (q06, q10) without
  degrading performance on other query types?

Experiment 1 failures:
  q06  "How does the memory hierarchy work in MemOS?"    Hier=0.0  (router: shallow/L1 only)
  q10  "Why do we use Claude Haiku for the router?"      Hier=2.0  (router: medium/L1+2 only)

Root cause: Both are "design-reasoning" queries that need Level 3 episodic memories
(where design rationale lives) but were routed to Level 1/2 only because the router
had no intent type for WHY/HOW questions.

Fix applied: Added a fourth intent type "design-reasoning" → always routes to
levels=[1,2,3] with budget="deep". Any question containing "why", "how does",
"what was the reasoning", etc. is forced into this class.

Method:
  - Same 14 seed memories as Experiment 1
  - Same 10 test queries as Experiment 1
  - Run ONLY hierarchical retrieval with the new intent-aware router
  - Compare scores against Experiment 1 hierarchical scores
  - Log improvements/regressions per query to W&B

Expected outcome:
  - q06 score: 0.0 → 7.0+
  - q10 score: 2.0 → 7.0+
  - All other queries maintain same or better scores
  - Overall mean hierarchical improves from 7.90 → 9.0+

Run:
    python experiments/exp02_router_intent_fix.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from utils import parse_json  # noqa: E402

# ---------------------------------------------------------------------------
# Seed memories — IDENTICAL to Experiment 1. Do NOT change.
# ---------------------------------------------------------------------------

SEED_MEMORIES = [
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
        "content": "Session 2026-05-18: The retrieval router uses claude-haiku-4-5 for classification to minimise latency and cost. Claude Sonnet is reserved for user-facing responses only. This keeps per-turn cost under $0.01.",
        "summary": "Router uses Haiku for cost; Sonnet for user responses only.",
        "tags": ["router", "haiku", "sonnet", "cost", "design"],
        "level": 3,
        "importance": 0.83,
        "project": "memos",
    },
]

# ---------------------------------------------------------------------------
# Test queries — IDENTICAL to Experiment 1. Do NOT change.
# ---------------------------------------------------------------------------

TEST_QUERIES = [
    ("q01", "What is my name?",                                                 "Simple identity — Level 1"),
    ("q02", "What university do I go to?",                                      "Simple identity — Level 1"),
    ("q03", "What programming language do I prefer?",                           "Preference — Level 1"),
    ("q04", "What is MemOS built with? What are its main technologies?",        "Project architecture — Level 2"),
    ("q05", "What embedding model does MemOS use for vector search?",           "Project decision — Level 2/3"),
    ("q06", "How does the memory hierarchy work in MemOS?",                     "Design-reasoning — FAILURE in Exp 1 (was 0.0)"),
    ("q07", "What bug did we fix with ChromaDB and what was the root cause?",   "Session bug fix — Level 3"),
    ("q08", "What did we decide about how to handle memory contradictions?",    "Past design decision — Level 3"),
    ("q09", "What were the results of the retrieval comparison experiment?",    "Past experiment results — Level 3"),
    ("q10", "Why do we use Claude Haiku for the router instead of Sonnet?",    "Design-reasoning — FAILURE in Exp 1 (was 2.0)"),
]

# Experiment 1 hierarchical scores for delta comparison
EXP1_HIER_SCORES = {
    "q01": 10.0, "q02": 10.0, "q03": 10.0,
    "q04": 10.0, "q05": 10.0, "q06": 0.0,
    "q07": 10.0, "q08": 10.0, "q09": 7.0, "q10": 2.0,
}

# ---------------------------------------------------------------------------
# Relevance judge (same prompt as Experiment 1)
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = """\
You are a relevance evaluator for a memory retrieval system.

Given a USER QUERY and a MEMORY CONTEXT block, rate how relevant and useful
the retrieved memories are for answering the query.

Score 0–10:
- 10: Perfect — all retrieved memories directly answer the query
- 7–9: Good — most memories relevant, minor noise
- 4–6: Mediocre — some relevant, some noise
- 1–3: Poor — mostly irrelevant
- 0: Completely irrelevant

Respond ONLY with valid JSON — no prose, no markdown:
{"score": 8, "reasoning": "one sentence"}
"""


def judge_relevance(client, query: str, context: str) -> tuple[float, str]:
    if not context or context == "(no relevant memories found)":
        return 0.0, "no context retrieved"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        system=_JUDGE_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"QUERY: {query}\n\nMEMORY CONTEXT:\n{context}",
            }
        ],
    )
    text = response.content[0].text.strip()
    try:
        data = parse_json(text)
        return float(data.get("score", 0)), data.get("reasoning", "")
    except (ValueError, KeyError):
        return 0.0, f"parse error — raw: {text[:60]}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_experiment() -> list[dict]:
    from anthropic import Anthropic
    from memory.store import MemoryItem, MemoryLevel, MemoryStore
    from memory.vector_store import VectorStore
    from retrieval.router import RetrievalRouter, format_context

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Exiting.")
        sys.exit(1)

    client = Anthropic(api_key=api_key)

    tmp_dir = Path(tempfile.mkdtemp(prefix="memos_exp02_"))
    print(f"\n{'='*60}")
    print("MemOS — Experiment 02: Intent-Aware Router Fix")
    print(f"{'='*60}")
    print(f"Temp store: {tmp_dir}")

    store = MemoryStore(db_path=str(tmp_dir / "memos.db"))
    vector_store = VectorStore(persist_dir=str(tmp_dir / "chroma"))
    router = RetrievalRouter(client, store, vector_store)

    # ------------------------------------------------------------------
    # Step 1: Seed — identical to Experiment 1
    # ------------------------------------------------------------------
    print("\n[1/3] Seeding 14 memories across levels 1, 2, 3…")
    import uuid

    for raw in SEED_MEMORIES:
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
        store.upsert(item)
        vector_store.upsert(item)

    s = store.stats()
    print(f"   Seeded: L1={s['by_level'][1]}  L2={s['by_level'][2]}  L3={s['by_level'][3]}")

    # ------------------------------------------------------------------
    # Step 2: Run intent-aware hierarchical retrieval on all 10 queries
    # ------------------------------------------------------------------
    print("\n[2/3] Running intent-aware hierarchical retrieval on 10 queries…\n")

    results: list[dict] = []

    for qid, query, description in TEST_QUERIES:
        memories, meta = router.retrieve(query)
        context = format_context(memories, meta.get("budget", "medium"))

        score, reasoning = judge_relevance(client, query, context)

        exp1_score = EXP1_HIER_SCORES[qid]
        delta = score - exp1_score
        intent = meta.get("routing", {}).get("intent", "unknown")
        budget = meta.get("budget", "?")
        levels = meta.get("levels_used", [])

        symbol = "✓" if delta > 0 else ("✗" if delta < 0 else "=")
        print(
            f"  {qid}  [{symbol}  {delta:+.1f}]  "
            f"Exp2={score:.1f}({len(memories)}mem)  "
            f"Exp1={exp1_score:.1f}  "
            f"intent={intent}  budget={budget}  "
            f"  {description}"
        )

        results.append(
            {
                "id": qid,
                "query": query,
                "description": description,
                "exp2_score": score,
                "exp1_hier_score": exp1_score,
                "delta_vs_exp1": round(delta, 2),
                "mem_count": len(memories),
                "intent": intent,
                "budget": budget,
                "levels_used": levels,
                "reasoning": reasoning,
            }
        )

    # ------------------------------------------------------------------
    # Step 3: Summarise
    # ------------------------------------------------------------------
    exp2_mean = sum(r["exp2_score"] for r in results) / len(results)
    exp1_mean = sum(r["exp1_hier_score"] for r in results) / len(results)
    improvements = sum(1 for r in results if r["delta_vs_exp1"] > 0)
    regressions = sum(1 for r in results if r["delta_vs_exp1"] < 0)
    ties = sum(1 for r in results if r["delta_vs_exp1"] == 0)

    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"  Exp2 Mean Score  : {exp2_mean:.2f}/10")
    print(f"  Exp1 Mean Score  : {exp1_mean:.2f}/10  (hierarchical baseline)")
    print(f"  Improvement      : {exp2_mean - exp1_mean:+.2f}")
    print(f"  Improvements={improvements}  Regressions={regressions}  Ties={ties}")
    print(f"{'='*60}")
    print()
    print(f"  {'ID':<5} {'Query':<45} {'Exp2':>5} {'Exp1':>5} {'Delta':>6} {'Intent'}")
    print(f"  {'-'*5} {'-'*45} {'-'*5} {'-'*5} {'-'*6} {'-'*18}")
    for r in results:
        print(
            f"  {r['id']:<5} {r['query'][:43]:<45} "
            f"{r['exp2_score']:>5.1f} {r['exp1_hier_score']:>5.1f} "
            f"{r['delta_vs_exp1']:>+6.1f} {r['intent']}"
        )
    print()

    # ------------------------------------------------------------------
    # Optional W&B logging
    # ------------------------------------------------------------------
    wandb_key = os.environ.get("WANDB_API_KEY", "").strip()
    wandb_project = os.environ.get("WANDB_PROJECT", "memos")

    if wandb_key:
        try:
            import wandb  # type: ignore

            run = wandb.init(
                project=wandb_project,
                name="exp02-intent-aware-router-fix",
                config={
                    "experiment": "intent_aware_router_fix",
                    "fix": "design-reasoning intent class → levels=[1,2,3] budget=deep",
                    "n_queries": len(TEST_QUERIES),
                    "n_seed_memories": len(SEED_MEMORIES),
                    "embedding_model": "all-MiniLM-L6-v2",
                    "judge_model": "claude-haiku-4-5-20251001",
                    "router_model": "claude-haiku-4-5-20251001",
                },
                tags=["exp02", "intent-router", "router-fix"],
            )

            wandb.log(
                {
                    "mean_exp2_score": exp2_mean,
                    "mean_exp1_hier_score": exp1_mean,
                    "mean_improvement": exp2_mean - exp1_mean,
                    "query_improvements": improvements,
                    "query_regressions": regressions,
                    "query_ties": ties,
                }
            )

            columns = [
                "id", "query", "exp2_score", "exp1_hier_score",
                "delta_vs_exp1", "mem_count", "intent", "budget",
            ]
            table = wandb.Table(
                columns=columns,
                data=[[r[c] for c in columns] for r in results],
            )
            wandb.log({"per_query_results": table})

            for r in results:
                wandb.log(
                    {
                        f"query/{r['id']}/exp2_score": r["exp2_score"],
                        f"query/{r['id']}/exp1_score": r["exp1_hier_score"],
                        f"query/{r['id']}/delta": r["delta_vs_exp1"],
                        f"query/{r['id']}/intent": r["intent"],
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

    # Save locally
    out_path = Path(__file__).parent / "exp02_results.json"
    with open(out_path, "w") as f:
        json.dump(
            {
                "experiment": "exp02_intent_aware_router_fix",
                "run_at": datetime.now(timezone.utc).isoformat(),
                "summary": {
                    "mean_exp2_score": exp2_mean,
                    "mean_exp1_hier_score": exp1_mean,
                    "improvement": exp2_mean - exp1_mean,
                    "improvements": improvements,
                    "regressions": regressions,
                    "ties": ties,
                },
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"Results saved to {out_path}\n")
    return results


if __name__ == "__main__":
    run_experiment()
