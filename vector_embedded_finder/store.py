"""ChromaDB vector store interface."""

from __future__ import annotations

import chromadb

from . import config


_client: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None


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
) -> dict:
    coll = _get_collection()
    kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": min(n_results, coll.count()) if coll.count() > 0 else 1,
        "include": ["metadatas", "documents", "distances"],
    }
    if where:
        kwargs["where"] = where
    if coll.count() == 0:
        return {"ids": [[]], "metadatas": [[]], "documents": [[]], "distances": [[]]}
    return coll.query(**kwargs)


def exists(doc_id: str) -> bool:
    coll = _get_collection()
    result = coll.get(ids=[doc_id])
    return len(result["ids"]) > 0


def delete(doc_id: str) -> None:
    coll = _get_collection()
    coll.delete(ids=[doc_id])


def count() -> int:
    return _get_collection().count()


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
