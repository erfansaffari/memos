# Architecture notes

These are rough notes on design decisions, mostly for my own reference.

## Why three memory levels?

The short answer is that not all memories are equally useful in every conversation.
If someone asks "what's my name", you don't need to pull in session-level bug fixes
from three weeks ago. The three-level split (identity / project / episodic) is a
simple way to enforce that — always give the model a small identity summary, add
project context when it's relevant, and only go deep when the query actually needs it.

The alternative is just doing top-N vector search across everything, which is what
Experiment 1 tested. It works fine for most queries but adds noise for simple ones
and misses things for complex ones because the vector similarity doesn't know
anything about query intent.

## Why SQLite + ChromaDB instead of just one?

ChromaDB is good at "find me the 10 memories most similar to this query."
SQLite is good at "give me all Level 1 memories with confidence > 0.5 sorted by importance."
Neither does both well, so I dual-write to both and use each for what it's good at.

## The router failure mode (Experiment 1)

The original router only had three budget levels: shallow, medium, deep.
The problem was that "why/how" questions about design decisions were being
classified as medium (project-related) when they actually needed deep episodic
context — because the reasoning behind a design decision lives in session memories,
not project-level facts.

Adding a `design-reasoning` intent class with a hard rule solved it.
Any query with "why", "how does", "what was the reasoning" goes straight to deep + Level 3.

## Confidence decay vs hard delete

I went with confidence decay because deleting a memory felt too aggressive.
If the verifier detects that "user uses React" contradicts "user switched to Svelte",
the right move is to lower confidence on the React memory, not delete it.
Maybe the user switches back. Maybe the verifier was wrong. Keeping it with
a low confidence score is more honest about what we actually know.
