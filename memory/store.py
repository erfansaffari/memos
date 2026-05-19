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

    def get_by_level(
        self, level: int, min_confidence: float = 0.3
    ) -> list[MemoryItem]:
        """Get all memories at a given level above a confidence threshold."""
        with self.SessionLocal() as session:
            records = (
                session.query(MemoryRecord)
                .filter(
                    MemoryRecord.level == level,
                    MemoryRecord.confidence >= min_confidence,
                )
                .order_by(MemoryRecord.importance.desc())
                .all()
            )
            return [self._to_item(r) for r in records]

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
