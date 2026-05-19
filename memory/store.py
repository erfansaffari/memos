"""
SQLite metadata store and Pydantic data models for MemOS.

All memory metadata lives here. ChromaDB holds the embeddings;
SQLite holds structured fields enabling confidence filtering, level queries,
and importance sorting that vector DBs cannot do efficiently.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import JSON

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MEMOS_DIR = Path.home() / ".memos"
MEMOS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = MEMOS_DIR / "memos.db"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MemoryLevel(IntEnum):
    IDENTITY = 1   # Stable user facts
    PROJECT = 2    # Ongoing projects & technical decisions
    EPISODIC = 3   # Session-specific details, code, experiments


# ---------------------------------------------------------------------------
# Pydantic schemas (shared across the whole system)
# ---------------------------------------------------------------------------


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    level: MemoryLevel
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_model: str = "unknown"
    project: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    frequency: int = 0


class ExtractionResult(BaseModel):
    memories: list[MemoryItem]
    reasoning: str


class ConflictReport(BaseModel):
    has_conflict: bool
    old_memory_id: Optional[str] = None
    new_memory: Optional[MemoryItem] = None
    resolution: str = "none"  # "update" | "decay" | "keep_both" | "none"
    explanation: str


# ---------------------------------------------------------------------------
# SQLAlchemy ORM
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class MemoryRecord(Base):
    __tablename__ = "memories"

    id = Column(String, primary_key=True)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    tags = Column(JSON, default=list)
    level = Column(Integer, nullable=False)
    importance = Column(Float, default=0.5)
    confidence = Column(Float, default=1.0)
    source_model = Column(String, default="unknown")
    project = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_used = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    frequency = Column(Integer, default=0)


class ImportLog(Base):
    """One row per imported chat-history file, keyed by SHA-256 hash."""

    __tablename__ = "import_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    file_hash = Column(String, nullable=False, unique=True)
    platform = Column(String, nullable=False)
    imported_at = Column(DateTime, nullable=False)
    file_name = Column(String, nullable=True)
    turns_processed = Column(Integer, default=0)
    memories_added = Column(Integer, default=0)
    memories_skipped_duplicate = Column(Integer, default=0)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MemoryStore:
    """SQLite-backed structured metadata store."""

    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        self.engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, item: MemoryItem) -> None:
        """Insert or replace a memory record."""
        with self.SessionLocal() as session:
            record = MemoryRecord(
                id=item.id,
                content=item.content,
                summary=item.summary,
                tags=item.tags,
                level=int(item.level),
                importance=item.importance,
                confidence=item.confidence,
                source_model=item.source_model,
                project=item.project,
                created_at=item.created_at,
                last_used=item.last_used,
                frequency=item.frequency,
            )
            session.merge(record)
            session.commit()

    def decay_confidence(self, memory_id: str, factor: float) -> None:
        """Multiply a memory's confidence by factor (never below 0)."""
        with self.SessionLocal() as session:
            record = session.get(MemoryRecord, memory_id)
            if record:
                record.confidence = max(0.0, record.confidence * factor)
                session.commit()

    def update_frequency(self, memory_id: str) -> None:
        """Increment retrieval frequency and update last_used timestamp."""
        with self.SessionLocal() as session:
            record = session.get(MemoryRecord, memory_id)
            if record:
                record.frequency = (record.frequency or 0) + 1
                record.last_used = datetime.now(timezone.utc)
                session.commit()

    def delete_all(self) -> int:
        """Delete all memories and return count deleted."""
        with self.SessionLocal() as session:
            count = session.query(MemoryRecord).count()
            session.query(MemoryRecord).delete()
            session.commit()
            return count

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def upsert(self, item: MemoryItem) -> None:
        """Alias for save() — insert or replace a memory record."""
        self.save(item)

    def get_by_id(self, memory_id: str) -> Optional[MemoryItem]:
        """Look up a single memory by its UUID. Returns None if not found."""
        with self.SessionLocal() as session:
            record = session.get(MemoryRecord, memory_id)
            return self._to_item(record) if record else None

    def get_by_level(
        self,
        level: int,
        min_confidence: float = 0.3,
        limit: Optional[int] = None,
    ) -> list[MemoryItem]:
        """Get memories at a given level above a confidence threshold."""
        with self.SessionLocal() as session:
            q = (
                session.query(MemoryRecord)
                .filter(
                    MemoryRecord.level == int(level),
                    MemoryRecord.confidence >= min_confidence,
                )
                .order_by(MemoryRecord.importance.desc())
            )
            if limit is not None:
                q = q.limit(limit)
            return [self._to_item(r) for r in q.all()]

    def get_all(self, min_confidence: float = 0.0) -> list[MemoryItem]:
        """Get all memories ordered by level then importance descending."""
        with self.SessionLocal() as session:
            records = (
                session.query(MemoryRecord)
                .filter(MemoryRecord.confidence >= min_confidence)
                .order_by(MemoryRecord.level, MemoryRecord.importance.desc())
                .all()
            )
            return [self._to_item(r) for r in records]

    def count_by_level(self) -> dict[int, int]:
        """Return {level: count} for levels 1, 2, 3."""
        with self.SessionLocal() as session:
            return {
                lvl: session.query(MemoryRecord)
                .filter_by(level=lvl)
                .count()
                for lvl in (1, 2, 3)
            }

    def stats(self) -> dict:
        """Return summary statistics: total count and per-level breakdown."""
        by_level = self.count_by_level()
        return {
            "by_level": by_level,
            "total": sum(by_level.values()),
        }

    # ------------------------------------------------------------------
    # Import log
    # ------------------------------------------------------------------

    def import_already_processed(self, file_hash: str) -> bool:
        """Return True if a file with this SHA-256 hash was already imported."""
        with self.SessionLocal() as session:
            return (
                session.query(ImportLog)
                .filter_by(file_hash=file_hash)
                .count()
            ) > 0

    def log_import(
        self,
        file_hash: str,
        platform: str,
        file_name: str,
        turns: int,
        added: int,
        skipped: int,
    ) -> None:
        """Write a single import log entry."""
        with self.SessionLocal() as session:
            entry = ImportLog(
                id=str(uuid.uuid4()),
                file_hash=file_hash,
                platform=platform,
                imported_at=datetime.now(timezone.utc),
                file_name=file_name,
                turns_processed=turns,
                memories_added=added,
                memories_skipped_duplicate=skipped,
            )
            session.merge(entry)
            session.commit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _to_item(self, record: MemoryRecord) -> MemoryItem:
        return MemoryItem(
            id=record.id,
            content=record.content,
            summary=record.summary,
            tags=record.tags or [],
            level=MemoryLevel(record.level),
            importance=float(record.importance or 0.5),
            confidence=float(record.confidence or 1.0),
            source_model=record.source_model or "unknown",
            project=record.project,
            created_at=record.created_at or datetime.now(timezone.utc),
            last_used=record.last_used or datetime.now(timezone.utc),
            frequency=int(record.frequency or 0),
        )
