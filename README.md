# MemOS

A persistent memory layer for AI models that remembers context across sessions and retrieves it based on what the current query actually needs.

## Why I built this

I kept running into the same problem: every time I switched between Claude, ChatGPT, or Gemini — which happens a lot when new models drop — all the context from previous sessions was gone. There's no convenient way to carry memory across platforms, so I started thinking about building something that sits independently of any specific model and owns the memory layer itself.

The interesting design question turned out to be: how much memory should you actually inject into a prompt? Dumping everything is wasteful and noisy. So I designed a three-level hierarchy where stable identity facts are always included, project context loads when relevant, and specific session details only come in when the query actually needs them. Then I ran experiments to see if that actually worked better than just doing a flat vector search.

---

## How it works

```
User message
     ↓
[retrieve] → [respond] → [extract] → [verify] → [store]
     ↑                                                |
     └────────────── next session ───────────────────┘
```

Five LangGraph nodes run on every turn:

1. **retrieve** — router classifies the query intent, picks which memory levels to fetch and how many tokens to spend
2. **respond** — Claude Sonnet answers with the retrieved memory injected into the system prompt
3. **extract** — Claude Haiku pulls structured memories out of the conversation turn
4. **verify** — checks new memories against existing ones for contradictions
5. **store** — applies confidence decay on contradicted memories, writes to SQLite + ChromaDB

### Memory hierarchy

| Level | Name | What it stores | Always fetched? |
|-------|------|---------------|-----------------|
| 1 | Identity | Name, preferences, language, long-term goals | Yes |
| 2 | Project | Active projects, stack decisions, architecture | If query is project-related |
| 3 | Episodic | Session details, bugs fixed, experiment results | Only when query needs depth |

### Retrieval router

The router (Claude Haiku) classifies each query into one of four intent types and assigns a token budget:

| Intent | Example query | Levels | Budget |
|--------|--------------|--------|--------|
| factual | "What's my name?" | [1] | shallow (3 memories) |
| project | "What stack does MemOS use?" | [1, 2] | medium (8 memories) |
| design-reasoning | "Why did we use Haiku for the router?" | [1, 2, 3] | deep (20 memories) |
| episodic | "What bug did we fix in ChromaDB?" | [2, 3] | deep (20 memories) |

Design-reasoning queries were the failure case I found in Experiment 1 — they were being misrouted to shallow/medium when they needed deep episodic context. Adding the explicit intent class fixed it.

### Confidence decay

Every memory has a confidence score (0–1). When the verifier finds a contradiction it decays the old memory's confidence rather than deleting it. Memories below 0.3 stop being retrieved but stay in the database. This felt more honest than hard deletes — you're modeling uncertainty, not pretending old information never existed.

---

## Setup

```bash
git clone https://github.com/erfansaffari/memos
cd memos
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# add your ANTHROPIC_API_KEY (and optionally WANDB_API_KEY for experiments)

python main.py chat
```

## CLI

```bash
python main.py chat       # chat with persistent memory
python main.py memories   # see everything stored
python main.py stats      # memory count by level
python main.py clear      # wipe all memories
python main.py import --file export.json --platform claude   # import chat history
```

During chat: type `memories`, `stats`, or `quit`.

---

## MCP Server

MemOS ships an MCP (Model Context Protocol) server that exposes your memory to any MCP-compatible AI client — Claude Desktop, Cursor, or any other tool that speaks MCP.

### Tools exposed

| Tool | What it does |
|------|-------------|
| `memos_recall` | Retrieve memories relevant to a query (query, optional budget) |
| `memos_remember` | Store a new piece of information (runs extraction + dedup) |
| `memos_forget` | Zero-out a memory's confidence by ID |
| `memos_stats` | Return memory counts by level |

### Starting the server

```bash
python mcp_server.py
```

The server runs over stdio — your MCP client spawns the process and communicates through stdin/stdout.

### Claude Desktop

Add this to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memos": {
      "command": "python",
      "args": ["/absolute/path/to/memos/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop. You'll see `memos_recall`, `memos_remember`, `memos_forget`, and `memos_stats` available as tools.

### Cursor

Add this to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "memos": {
      "command": "python",
      "args": ["/absolute/path/to/memos/mcp_server.py"]
    }
  }
}
```

Agents in Cursor can then call `memos_recall` before answering questions about your work.

---

## Importing chat history

If you've been using Claude, ChatGPT, or Gemini and want to seed MemOS with existing conversations, the importer can process exported JSON files and extract memories with deduplication.

### Supported formats

| Platform | How to export |
|----------|--------------|
| Claude | claude.ai → Settings → Export Data → download the JSON |
| ChatGPT | chat.openai.com → Settings → Data Export → conversations.json |
| Gemini | Google Takeout → Gemini Apps Activity → extract the JSON |

### Running the import

```bash
# Import a Claude export
python main.py import --file ~/Downloads/claude_export.json --platform claude

# ChatGPT
python main.py import --file ~/Downloads/conversations.json --platform chatgpt

# Gemini
python main.py import --file ~/Downloads/gemini_export.json --platform gemini

# Dry-run: see what would be imported without writing anything
python main.py import --file export.json --platform claude --dry-run
```

The importer uses two-layer deduplication:
1. **SHA-256 hash** — the same file can't be imported twice
2. **Cosine similarity** — any extracted memory with similarity > 0.92 to an existing one is skipped

Each turn in the export goes through the same extraction + verification pipeline as the live chat, so memory quality is consistent.

---

## Experiments

### Experiment 1: flat vs hierarchical retrieval

Does the hierarchical router actually outperform flat vector search?

Seeded 14 memories across all 3 levels, ran 10 queries through both approaches, and had Claude Haiku judge retrieval relevance (0–10).

**Results:**

| | Hierarchical | Flat |
|--|--|--|
| Mean score | 7.90 / 10 | 9.20 / 10 |
| Wins | 1 | 2 |
| Ties | 7 | 7 |

Hierarchical underperformed on 2 queries — both were "why/how" design questions that needed Level 3 episodic context but the router was sending them to Level 1/2 only. Everything else tied or the hierarchical approach won.

[View W&B run](https://wandb.ai/erfansaffari0-university-of-waterloo/memos)

```bash
python experiments/exp01_retrieval_comparison.py
```

### Experiment 2: intent-aware router fix

Added a `design-reasoning` intent class to the router with a hard rule: any query containing "why", "how does", or "what was the reasoning" always routes to Level 3 with a deep budget.

**Results:**

| Query | Exp 1 | Exp 2 | Change |
|-------|-------|-------|--------|
| "How does the memory hierarchy work?" | 0.0 | 9.0 | +9.0 |
| "Why do we use Haiku for the router?" | 2.0 | 9.0 | +7.0 |
| Mean (all 10 queries) | 7.90 | 9.20 | +1.30 |

The two failure cases from Exp 1 are fixed. Mean hierarchical score now matches flat retrieval (9.20) while still fetching fewer memories on simple queries.

[View W&B run](https://wandb.ai/erfansaffari0-university-of-waterloo/memos/runs/i62ou2vv)

```bash
python experiments/exp02_router_intent_fix.py
```

---

## Stack

| | |
|--|--|
| Orchestration | LangGraph |
| LLM (responses) | Claude Sonnet |
| LLM (agents) | Claude Haiku |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 (local) |
| Vector store | ChromaDB |
| Metadata | SQLite via SQLAlchemy |
| Evaluation | Weights & Biases |
| CLI | Typer + Rich |

Data lives locally at `~/.memos/` — no external database.

---

## Project structure

```
memos/
├── agents/
│   ├── extractor.py      # pulls structured memories from conversation turns
│   ├── verifier.py       # contradiction detection + confidence decay
│   └── graph.py          # LangGraph pipeline definition
├── memory/
│   ├── store.py          # SQLite store + Pydantic schemas + ImportLog
│   └── vector_store.py   # ChromaDB wrapper
├── retrieval/
│   └── router.py         # intent classification + hierarchical retrieval
├── importers/
│   └── importer.py       # chat history parsers (Claude, ChatGPT, Gemini)
├── experiments/
│   ├── exp01_retrieval_comparison.py
│   └── exp02_router_intent_fix.py
├── docs/
│   └── architecture.md   # rough architecture notes
├── mcp_server.py         # MCP server (stdio transport)
├── main.py               # CLI entry point
├── utils.py              # shared helpers (logging, formatting)
└── requirements.txt
```
