"""
MemOS CLI entry point.

Commands:
    chat      — Interactive chat session with hierarchical memory
    memories  — List all stored memories in a Rich table
    stats     — Show memory counts by level
    clear     — Delete all memories (with optional --force)

During a chat session, you can also type:
    memories  — show memory table inline
    stats     — show quick memory count
    quit / exit / q  — end session
"""

from __future__ import annotations

import os
import uuid
from typing import Optional

import typer
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

app = typer.Typer(
    help="MemOS — Hierarchical AI Memory Architecture",
    add_completion=False,
)
console = Console()

LEVEL_LABELS = {
    1: "[yellow]Identity[/yellow]",
    2: "[cyan]Project[/cyan]",
    3: "[magenta]Episodic[/magenta]",
}


# ---------------------------------------------------------------------------
# Shared setup helpers
# ---------------------------------------------------------------------------


def _require_client():
    """Return an Anthropic client or exit with a helpful error."""
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        console.print(
            "[bold red]Error:[/bold red] ANTHROPIC_API_KEY is not set.\n"
            "Run:  [bold]cp .env.example .env[/bold]  then add your key."
        )
        raise typer.Exit(1)
    return Anthropic(api_key=api_key)


def _get_stores():
    """Return (MemoryStore, VectorStore) instances."""
    from memory.store import MemoryStore
    from memory.vector_store import VectorStore

    return MemoryStore(), VectorStore()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def chat() -> None:
    """Start an interactive chat session with hierarchical memory."""
    client = _require_client()
    memory_store, vector_store = _get_stores()

    from agents.graph import build_graph

    graph = build_graph(client, memory_store, vector_store)
    session_id = str(uuid.uuid4())

    counts = memory_store.count_by_level()
    total = sum(counts.values())

    console.print(
        Panel(
            "[bold cyan]MemOS[/bold cyan] — Hierarchical AI Memory\n"
            f"[dim]Session {session_id[:8]}…  |  "
            f"Memories: {total} "
            f"(L1:{counts[1]}  L2:{counts[2]}  L3:{counts[3]})[/dim]\n"
            "[dim]Type  memories  |  stats  |  quit[/dim]",
            border_style="cyan",
        )
    )

    while True:
        try:
            user_input = console.input("\n[bold green]You:[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye.[/dim]")
            break

        if user_input.lower() == "memories":
            _render_memories(memory_store)
            continue

        if user_input.lower() == "stats":
            _render_stats(memory_store)
            continue

        state = {
            "user_message": user_input,
            "session_id": session_id,
            "retrieved_memories": [],
            "retrieval_meta": {},
            "context_block": "",
            "assistant_response": "",
            "new_memories": [],
            "extraction_reasoning": "",
            "conflict_reports": [],
            "stored_count": 0,
            "skipped_count": 0,
            "log": [],
        }

        with console.status("[dim]Thinking…[/dim]", spinner="dots"):
            result = graph.invoke(state)

        console.print(f"\n[bold blue]MemOS:[/bold blue] {result['assistant_response']}")

        # Transparency footer: what was retrieved and stored
        meta = result.get("retrieval_meta", {})
        n_retrieved = len(result.get("retrieved_memories", []))
        n_stored = result.get("stored_count", len(result.get("new_memories", [])))
        n_skipped = result.get("skipped_count", 0)
        conflicts = sum(
            1 for r in result.get("conflict_reports", []) if r.get("has_conflict")
        )
        parts = [
            f"retrieved {n_retrieved} memories",
            f"intent={meta.get('intent', '?')}",
            f"budget={meta.get('budget', '?')}",
            f"stored {n_stored} new",
        ]
        if n_skipped:
            parts.append(f"[dim]skipped {n_skipped} duplicate(s)[/dim]")
        if conflicts:
            parts.append(f"[yellow]{conflicts} conflict(s) resolved[/yellow]")

        console.print(f"[dim]↳ {' · '.join(parts)}[/dim]")


@app.command()
def memories() -> None:
    """List all stored memories in a table."""
    memory_store, _ = _get_stores()
    _render_memories(memory_store)


@app.command()
def stats() -> None:
    """Show memory counts by level."""
    memory_store, _ = _get_stores()
    _render_stats(memory_store)


@app.command()
def clear(
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt."),
) -> None:
    """Delete ALL stored memories from SQLite and ChromaDB."""
    memory_store, vector_store = _get_stores()

    if not force:
        confirmed = typer.confirm(
            "This will permanently delete ALL memories. Continue?", default=False
        )
        if not confirmed:
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit()

    count = memory_store.delete_all()
    vector_store.delete_all()
    console.print(f"[bold red]Deleted {count} memories from SQLite and ChromaDB.[/bold red]")


# ---------------------------------------------------------------------------
# Render helpers (used by both commands and inline chat)
# ---------------------------------------------------------------------------


def _render_memories(memory_store) -> None:
    from memory.store import MemoryStore  # noqa: F401 (type hint only)

    all_mems = memory_store.get_all()
    if not all_mems:
        console.print("[dim]No memories stored yet. Start a chat to build memory.[/dim]")
        return

    table = Table(
        title=f"Stored Memories ({len(all_mems)} total)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("Level", width=10)
    table.add_column("Summary", width=42)
    table.add_column("Imp.", justify="right", width=5)
    table.add_column("Conf.", justify="right", width=5)
    table.add_column("Tags", width=22)

    for m in all_mems:
        label = LEVEL_LABELS.get(int(m.level), str(m.level))
        summary = m.summary[:40] + ("…" if len(m.summary) > 40 else "")
        tags = ", ".join(m.tags[:3])
        table.add_row(
            label,
            summary,
            f"{m.importance:.2f}",
            f"{m.confidence:.2f}",
            tags,
        )

    console.print(table)


def _render_stats(memory_store) -> None:
    counts = memory_store.count_by_level()
    total = sum(counts.values())

    table = Table(title="Memory Statistics", box=box.SIMPLE, show_header=True)
    table.add_column("Level", style="bold", width=8)
    table.add_column("Name", width=10)
    table.add_column("Count", justify="right", width=8)

    table.add_row("1", "Identity", str(counts[1]))
    table.add_row("2", "Project", str(counts[2]))
    table.add_row("3", "Episodic", str(counts[3]))
    table.add_section()
    table.add_row("", "[bold]Total[/bold]", f"[bold]{total}[/bold]")

    console.print(table)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
