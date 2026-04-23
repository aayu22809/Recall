"""ChromaDB vector store interface."""

from __future__ import annotations

import chromadb
import logging
import time

from . import config

logger = logging.getLogger(__name__)


_client: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None

# Throttle repeated count()/query() error logs. chromadb can repeatedly fail
# the HNSW compactor backfill and spam the log on every call otherwise.
_last_count_warn_ts: float = 0.0
_last_count_warn_msg: str = ""
_WARN_THROTTLE_S: float = 60.0


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        _collection = _client.get_or_create_collection(
            name=config.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _throttled_warn(label: str, exc: Exception) -> None:
    global _last_count_warn_ts, _last_count_warn_msg
    msg = f"{label}: {exc}"
    now = time.time()
    if msg != _last_count_warn_msg or (now - _last_count_warn_ts) > _WARN_THROTTLE_S:
        logger.warning("%s", msg)
        _last_count_warn_ts = now
        _last_count_warn_msg = msg


def _safe_count(coll: chromadb.Collection) -> int:
    try:
        return int(coll.count())
    except Exception as exc:
        _throttled_warn("Chroma count failed", exc)
        return 0


def add(
    doc_id: str,
    embedding: list[float],
    metadata: dict,
    document: str = "",
) -> None:
    coll = _get_collection()
    coll.upsert(
        ids=[doc_id],
        embeddings=[embedding],
        metadatas=[metadata],
        documents=[document],
    )


def search(
    query_embedding: list[float],
    n_results: int = 5,
    where: dict | None = None,
    where_document: dict | None = None,
) -> dict:
    coll = _get_collection()
    total = _safe_count(coll)
    if total <= 0:
        return {"ids": [[]], "metadatas": [[]], "documents": [[]], "distances": [[]]}
    kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": min(n_results, total),
        "include": ["metadatas", "documents", "distances"],
    }
    if where:
        kwargs["where"] = where
    if where_document:
        kwargs["where_document"] = where_document
    try:
        return coll.query(**kwargs)
    except Exception as exc:
        _throttled_warn("Chroma query failed", exc)
        return {"ids": [[]], "metadatas": [[]], "documents": [[]], "distances": [[]]}


def exists(doc_id: str) -> bool:
    coll = _get_collection()
    result = coll.get(ids=[doc_id])
    return len(result["ids"]) > 0


def delete(doc_id: str) -> None:
    coll = _get_collection()
    coll.delete(ids=[doc_id])


def count() -> int:
    return _safe_count(_get_collection())


def list_all(limit: int = 100, offset: int = 0) -> dict:
    coll = _get_collection()
    return coll.get(
        limit=limit,
        offset=offset,
        include=["metadatas", "documents"],
    )


def update_metadata(doc_id: str, metadata: dict) -> None:
    """Update metadata for an existing entry (no re-embedding)."""
    coll = _get_collection()
    coll.update(ids=[doc_id], metadatas=[metadata])
