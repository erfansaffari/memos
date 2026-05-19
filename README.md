# MemOS — Hierarchical AI Memory Architecture

**MemOS** is a research prototype of a hierarchical persistent memory architecture for multi-model AI systems. It gives any AI model a structured long-term memory layer that persists across sessions, detects contradictions, and retrieves context progressively based on query complexity.

## The Core Research Question

> Does hierarchical memory retrieval — where context is fetched progressively at increasing levels of detail — produce more relevant and token-efficient AI context than flat vector search?

## How It Works

```
User Message (CLI)
       ↓
┌─────────────────────────────────────────────────┐
│              LangGraph Agent Graph               │
│                                                  │
│  [retrieve] → [respond] → [extract] → [verify]  │
│                                  ↓               │
│                              [store]             │
└─────────────────────────────────────────────────┘
       ↓
Claude Sonnet response (printed to terminal)
```

### Memory Hierarchy

| Level | Name | Description | Retrieved? |
|-------|------|-------------|------------|
| 1 | **Identity** | Stable facts: name, preferences, skills | Always |
| 2 | **Project** | Active projects, tools, architecture decisions | If project-related |
| 3 | **Episodic** | Session details, code, bugs, experiments | If deep context needed |

The **Retrieval Router** (Claude Haiku) classifies each query and decides which levels to fetch and at what depth (`shallow` / `medium` / `deep`). This makes retrieval adaptive rather than exhaustive.

### Agent Pipeline

1. **retrieve** — Router classifies query → level-specific vector search in ChromaDB
2. **respond** — Claude Sonnet answers with injected `<memory_context>`
3. **extract** — Claude Haiku extracts structured memories from the conversation
4. **verify** — Claude Haiku detects contradictions against existing memories
5. **store** — Applies confidence decay for contradictions, writes to SQLite + ChromaDB

### Confidence & Decay

Every memory has a `confidence` score (0–1). When the verifier detects a contradiction:
- `update`: old memory confidence × 0.5
- `decay`: old memory confidence × 0.6
- `keep_both`: both retained unchanged

Memories below `confidence < 0.3` are filtered from retrieval but kept in the DB — modeling epistemic uncertainty rather than hard deletes.

---

## Setup

```bash
# 1. Clone repository
git clone https://github.com/erfansaffari/memos
cd memos

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate       # Mac/Linux

# 3. Install dependencies
pip install -r requirements.txt
# Note: first install downloads the sentence-transformers model (~80MB)

# 4. Configure API keys
cp .env.example .env
# Edit .env and set:
#   ANTHROPIC_API_KEY=your_key_here
#   WANDB_API_KEY=your_key_here   (optional, for experiments)

# 5. Start chatting
python main.py chat

# 6. Run the flat vs hierarchical retrieval experiment
python experiments/exp01_retrieval_comparison.py
```

---

## CLI Commands

```bash
python main.py chat          # Start interactive chat with memory
python main.py memories      # List all stored memories in a table
python main.py stats         # Show memory counts by level
python main.py clear         # Delete all memories (with confirmation)
python main.py clear --force # Delete without confirmation
```

During chat, you can also type:
- `memories` — show memory table inline
- `stats` — show quick memory count
- `quit` / `exit` / `q` — end session

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| Agent Orchestration | LangGraph |
| Primary LLM | Claude Sonnet (`claude-sonnet-4-6`) |
| Agent LLM | Claude Haiku (`claude-haiku-4-5-20251001`) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim) |
| Vector Store | ChromaDB (local persistent) |
| Metadata Store | SQLite via SQLAlchemy |
| Structured Outputs | Pydantic v2 |
| Evaluation | Weights & Biases |
| CLI | Typer + Rich |

---

## Storage

All data is stored locally:
- `~/.memos/memos.db` — SQLite database (structured metadata)
- `~/.memos/chroma/` — ChromaDB vector store (semantic search)

---

## Project Structure

```
memos/
├── main.py                          # CLI entry point (Typer)
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
│
├── agents/
│   ├── extractor.py                 # Extraction Agent (Claude Haiku)
│   ├── verifier.py                  # Verifier Agent (Claude Haiku)
│   └── graph.py                     # LangGraph graph definition
│
├── memory/
│   ├── store.py                     # SQLite store + Pydantic schemas
│   └── vector_store.py              # ChromaDB wrapper
│
├── retrieval/
│   └── router.py                    # Hierarchical retrieval router
│
├── experiments/
│   └── exp01_retrieval_comparison.py  # Flat vs hierarchical benchmark
│
├── evaluation/                      # (future) evaluation utilities
├── prompts/                         # (future) prompt versioning
├── notebooks/                       # (future) Jupyter analysis
└── docs/                            # (future) architecture diagrams
```

---

## Experiment 1: Flat vs Hierarchical Retrieval

**File:** `experiments/exp01_retrieval_comparison.py`

Seeds 14 diverse memories across all 3 levels, then runs 10 test queries through both:
- **Hierarchical:** router classifies query → level-filtered vector search
- **Flat:** simple top-N vector search, no level awareness

Claude Haiku judges each retrieved context for relevance (0–10). Results are logged to Weights & Biases.

**Expected hypothesis:** Hierarchical retrieval scores higher on complex queries (needs Level 3) and simple queries (Level 1 only, no noise).

---

## Design Principles

1. **Research first, app second.** Every feature exists to answer a research question.
2. **Honest confidence.** Never delete — decay confidence. Models uncertainty, allows rollback.
3. **Cheap agents, expensive responses.** Haiku for all internal calls. Sonnet for user-facing only.
4. **Dual write always.** SQLite + ChromaDB stay in sync.
5. **Transparency.** Every node logs what it did. You can see exactly what was retrieved and why.
6. **Local by default.** Embeddings run locally via sentence-transformers.

---

*MemOS is a research prototype exploring memory architecture design for agentic AI systems.*
