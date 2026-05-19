"""
ChromaDB vector store wrapper for MemOS.

Handles embedding generation (local, via sentence-transformers) and
semantic similarity search with level/confidence metadata filtering.
"""

from __future__ import annotations

from typing import Optional

import chromadb

from memory.store import MEMOS_DIR, MemoryItem

CHROMA_PATH = MEMOS_DIR / "chroma"


class VectorStore:
    """
    Wraps a persistent ChromaDB collection.

    Embeddings are produced locally using all-MiniLM-L6-v2 (384-dim, ~80MB).
    The model is lazy-loaded on first use to avoid slowing CLI startup.
    """

    COLLECTION_NAME = "memories"

    def __init__(self, persist_path: str = str(CHROMA_PATH)) -> None:
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=persist_path)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._model = None

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer("all-MiniLM-L6-v2")
        return self._model

    def embed(self, text: str) -> list[float]:
        return self.model.encode(text).tolist()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(self, item: MemoryItem) -> None:
        """Insert or update a memory's vector representation."""
        embedding = self.embed(item.content)
        self.collection.upsert(
            ids=[item.id],
            embeddings=[embedding],
            documents=[item.content],
            metadatas=[
                {
                    "level": int(item.level),
                    "importance": float(item.importance),
                    "confidence": float(item.confidence),
                    "summary": item.summary,
                    "project": item.project or "",
                    "tags": ",".join(item.tags),
                }
            ],
        )

    def delete_all(self) -> None:
        """Drop and re-create the collection (hard reset)."""
        self.client.delete_collection(self.COLLECTION_NAME)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        n_results: int = 10,
        level: Optional[int] = None,
        min_confidence: float = 0.3,
    ) -> list[dict]:
        """
        Semantic similarity search with optional level and confidence filters.

        Returns a list of dicts with keys: id, content, metadata, distance.
        Returns [] if the collection is empty.
        """
        total = self.collection.count()
        if total == 0:
            return []

        actual_n = min(n_results, total)

        # Build where clause — ChromaDB requires $and for multiple conditions.
        conditions: list[dict] = []
        if level is not None:
            conditions.append({"level": {"$eq": level}})
        if min_confidence > 0.0:
            conditions.append({"confidence": {"$gte": min_confidence}})

        where: Optional[dict] = None
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        query_kwargs: dict = {
            "query_embeddings": [self.embed(query)],
            "n_results": actual_n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where is not None:
            query_kwargs["where"] = where

        try:
            results = self.collection.query(**query_kwargs)
        except Exception:
            return []

        hits: list[dict] = []
        if results.get("ids") and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                hits.append(
                    {
                        "id": doc_id,
                        "content": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i],
                        "distance": results["distances"][0][i],
                    }
                )
        return hits

    def count(self) -> int:
        return self.collection.count()
