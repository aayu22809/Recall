"""Recall storage layer.

sqlite is the durable source of truth. A hot vector index is layered on top of
it for fast dense search, with a best-effort Chroma dual-write during
migration. The public surface preserves the small subset of helpers the rest of
the repo already uses.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import config

logger = logging.getLogger(__name__)

_CONN: sqlite3.Connection | None = None
_LOCK = threading.RLock()
_HOT_INDEX = None
_CACHE_EPOCH = 0


def _bump_cache_epoch() -> None:
    global _CACHE_EPOCH
    _CACHE_EPOCH += 1


def cache_epoch() -> int:
    return _CACHE_EPOCH


def _connect() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        config.ensure_runtime_dirs()
        conn = sqlite3.connect(str(config.SQLITE_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        _init_schema(conn)
        _CONN = conn
    return _CONN


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS manifest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT UNIQUE NOT NULL,
            path TEXT NOT NULL DEFAULT '',
            file_name TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            file_type TEXT NOT NULL DEFAULT '',
            media_category TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL DEFAULT '',
            mtime REAL NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'active',
            description TEXT NOT NULL DEFAULT '',
            file_size INTEGER NOT NULL DEFAULT 0,
            preview TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS manifest_state_idx ON manifest(state);
        CREATE INDEX IF NOT EXISTS manifest_path_idx ON manifest(path);
        CREATE INDEX IF NOT EXISTS manifest_source_idx ON manifest(source);
        CREATE INDEX IF NOT EXISTS manifest_media_idx ON manifest(media_category);

        CREATE TABLE IF NOT EXISTS vectors (
            file_id INTEGER PRIMARY KEY REFERENCES manifest(id) ON DELETE CASCADE,
            embedding_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS enrichment (
            file_id INTEGER PRIMARY KEY REFERENCES manifest(id) ON DELETE CASCADE,
            caption TEXT NOT NULL DEFAULT '',
            ocr_text TEXT NOT NULL DEFAULT '',
            gps_lat REAL,
            gps_lon REAL,
            gps_city TEXT NOT NULL DEFAULT '',
            face_count INTEGER NOT NULL DEFAULT 0,
            exif_date TEXT NOT NULL DEFAULT '',
            exif_camera TEXT NOT NULL DEFAULT ''
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS fts_content USING fts5(
            file_id UNINDEXED,
            path,
            title,
            body,
            metadata_text,
            tokenize='porter unicode61'
        );
        """
    )


def _meta_get(key: str, default: str = "") -> str:
    row = _connect().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row["value"])


def _meta_set(key: str, value: str) -> None:
    _connect().execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    _connect().commit()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0:
        return vec
    return [float(v / norm) for v in vec]


def _cosine_distance(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 1.0
    size = min(len(left), len(right))
    dot = sum(float(left[i]) * float(right[i]) for i in range(size))
    return max(0.0, min(2.0, 1.0 - dot))


@dataclass
class Candidate:
    file_id: int
    doc_id: str
    distance: float
    score: float
    metadata: dict[str, Any]


class _MemoryHotIndex:
    def __init__(self) -> None:
        self._vectors: dict[int, list[float]] = {}
        self._dirty = False
        self.backend = "memory"
        self.path = config.HNSW_DIR / "hot.index"

    def load_from_rows(self, rows: Iterable[tuple[int, list[float]]]) -> None:
        self._vectors = {file_id: vec for file_id, vec in rows}
        self._dirty = False

    def add_or_update(self, file_id: int, embedding: list[float]) -> None:
        self._vectors[file_id] = embedding

    def delete(self, file_id: int) -> None:
        self._vectors.pop(file_id, None)

    def search(self, embedding: list[float], limit: int) -> list[tuple[int, float]]:
        rows = [
            (file_id, _cosine_distance(embedding, candidate))
            for file_id, candidate in self._vectors.items()
        ]
        rows.sort(key=lambda item: item[1])
        return rows[:limit]

    def mark_dirty(self) -> None:
        self._dirty = True

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "path": str(self.path),
            "ready": True,
            "dirty": self._dirty,
            "count": len(self._vectors),
        }


class _HnswHotIndex(_MemoryHotIndex):
    def __init__(self) -> None:
        super().__init__()
        import hnswlib

        self._hnswlib = hnswlib
        self.backend = "hnswlib"
        self.path = config.HNSW_DIR / "hot.bin"
        self._index = None
        self._labels: set[int] = set()

    def load_from_rows(self, rows: Iterable[tuple[int, list[float]]]) -> None:
        tuples = list(rows)
        self._vectors = {file_id: vec for file_id, vec in tuples}
        max_elements = max(len(self._vectors) + 64, 256)
        index = self._hnswlib.Index(space="cosine", dim=config.EMBEDDING_DIMENSIONS)
        index.init_index(
            max_elements=max_elements,
            ef_construction=200,
            M=32,
            allow_replace_deleted=True,
        )
        if tuples:
            embeddings = [vec for _, vec in tuples]
            labels = [file_id for file_id, _ in tuples]
            index.add_items(embeddings, labels)
        index.set_ef(max(50, min(200, len(tuples) + 10)))
        self._index = index
        self._labels = set(self._vectors)
        self._dirty = False
        try:
            config.HNSW_DIR.mkdir(parents=True, exist_ok=True)
            index.save_index(str(self.path))
        except Exception as exc:
            logger.debug("Could not save hnsw index: %s", exc)

    def add_or_update(self, file_id: int, embedding: list[float]) -> None:
        if self._index is None:
            self.load_from_rows([(file_id, embedding)])
            return
        if file_id in self._labels:
            # hnswlib update semantics are awkward with stable labels; keep the
            # in-memory view correct and request a rebuild.
            self._vectors[file_id] = embedding
            self.mark_dirty()
            return
        try:
            self._index.add_items([embedding], [file_id])
            self._labels.add(file_id)
            self._vectors[file_id] = embedding
            self._index.save_index(str(self.path))
        except Exception as exc:
            logger.debug("Incremental hnsw update failed, will rebuild: %s", exc)
            self._vectors[file_id] = embedding
            self.mark_dirty()

    def delete(self, file_id: int) -> None:
        self._vectors.pop(file_id, None)
        if self._index is None:
            return
        if file_id in self._labels:
            try:
                self._index.mark_deleted(file_id)
                self._labels.discard(file_id)
                self._index.save_index(str(self.path))
            except Exception:
                self.mark_dirty()

    def search(self, embedding: list[float], limit: int) -> list[tuple[int, float]]:
        if self._dirty or self._index is None or not self._vectors:
            return super().search(embedding, limit)
        labels, distances = self._index.knn_query(embedding, k=min(limit, len(self._vectors)))
        results: list[tuple[int, float]] = []
        for label, distance in zip(labels[0], distances[0]):
            results.append((int(label), float(distance)))
        return results


def _hot_index():
    global _HOT_INDEX
    if _HOT_INDEX is None:
        try:
            _HOT_INDEX = _HnswHotIndex()
        except Exception as exc:
            logger.info("hnswlib unavailable, using in-memory hot index: %s", exc)
            _HOT_INDEX = _MemoryHotIndex()
    return _HOT_INDEX


def initialize() -> None:
    _connect()
    rebuild_hot_index()


def _active_vector_rows() -> list[tuple[int, list[float]]]:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT m.id, v.embedding_json
        FROM manifest m
        JOIN vectors v ON v.file_id = m.id
        WHERE m.state = 'active'
        """
    ).fetchall()
    result: list[tuple[int, list[float]]] = []
    for row in rows:
        try:
            embedding = [float(x) for x in json.loads(row["embedding_json"])]
        except Exception:
            continue
        result.append((int(row["id"]), embedding))
    return result


def rebuild_hot_index() -> dict[str, Any]:
    with _LOCK:
        start = time.time()
        rows = _active_vector_rows()
        _hot_index().load_from_rows(rows)
        _meta_set("index_last_rebuild_at", str(time.time()))
        _bump_cache_epoch()
        return {
            "status": "ok",
            "count": len(rows),
            "duration_s": round(time.time() - start, 4),
            **_hot_index().status(),
        }


def _metadata_text(metadata: dict[str, Any], document: str, enrichment: dict[str, Any] | None) -> str:
    parts = [
        str(metadata.get("file_name", "")),
        str(metadata.get("description", "")),
        str(metadata.get("source", "")),
        str(metadata.get("media_category", "")),
        document,
    ]
    if enrichment:
        for key in ("caption", "ocr_text", "gps_city", "exif_date", "exif_camera"):
            value = enrichment.get(key)
            if value:
                parts.append(str(value))
    return "\n".join(p for p in parts if p).strip()


def _row_to_metadata(row: sqlite3.Row) -> dict[str, Any]:
    metadata = _json_loads(row["metadata_json"])
    metadata.update(
        {
            "file_path": row["path"],
            "file_name": row["file_name"],
            "file_type": row["file_type"],
            "media_category": row["media_category"],
            "timestamp": row["timestamp"],
            "source": row["source"],
            "description": row["description"],
            "file_size": row["file_size"],
            "preview": row["preview"],
            "path": row["path"],
            "state": row["state"],
        }
    )
    return metadata


def _maybe_dual_write_chroma(
    doc_id: str,
    embedding: list[float],
    metadata: dict[str, Any],
    document: str,
) -> None:
    if not config.DUAL_WRITE_CHROMA:
        return
    try:
        import chromadb

        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        coll = client.get_or_create_collection(
            name=config.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        coll.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            metadatas=[metadata],
            documents=[document],
        )
    except Exception as exc:
        logger.debug("Legacy chroma dual-write failed: %s", exc)


def _ensure_manifest_row(
    conn: sqlite3.Connection,
    doc_id: str,
    metadata: dict[str, Any],
    document: str,
) -> int:
    path = str(metadata.get("file_path", ""))
    file_name = str(metadata.get("file_name", ""))
    file_type = str(metadata.get("file_type", ""))
    media_category = str(metadata.get("media_category", ""))
    timestamp = str(metadata.get("timestamp", ""))
    source = str(metadata.get("source", ""))
    description = str(metadata.get("description", ""))
    file_size = int(metadata.get("file_size", 0) or 0)
    sha256 = str(metadata.get("sha256", doc_id))
    mtime = float(metadata.get("mtime", 0.0) or 0.0)
    preview = (document or "")[:500]
    conn.execute(
        """
        INSERT INTO manifest(
            doc_id, path, file_name, source, file_type, media_category,
            timestamp, mtime, sha256, state, description, file_size,
            preview, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
        ON CONFLICT(doc_id) DO UPDATE SET
            path = excluded.path,
            file_name = excluded.file_name,
            source = excluded.source,
            file_type = excluded.file_type,
            media_category = excluded.media_category,
            timestamp = excluded.timestamp,
            mtime = excluded.mtime,
            sha256 = excluded.sha256,
            state = 'active',
            description = excluded.description,
            file_size = excluded.file_size,
            preview = excluded.preview,
            metadata_json = excluded.metadata_json
        """,
        (
            doc_id,
            path,
            file_name,
            source,
            file_type,
            media_category,
            timestamp,
            mtime,
            sha256,
            description,
            file_size,
            preview,
            _json_dumps(metadata),
        ),
    )
    row = conn.execute("SELECT id FROM manifest WHERE doc_id = ?", (doc_id,)).fetchone()
    return int(row["id"])


def add(
    doc_id: str,
    embedding: list[float],
    metadata: dict[str, Any],
    document: str = "",
    enrichment: dict[str, Any] | None = None,
) -> None:
    embedding = _normalize([float(v) for v in embedding[: config.EMBEDDING_DIMENSIONS]])
    with _LOCK:
        conn = _connect()
        file_id = _ensure_manifest_row(conn, doc_id, metadata, document)
        conn.execute(
            "INSERT OR REPLACE INTO vectors(file_id, embedding_json, updated_at) VALUES (?, ?, ?)",
            (file_id, _json_dumps(embedding), time.time()),
        )
        if enrichment is not None:
            conn.execute(
                """
                INSERT INTO enrichment(
                    file_id, caption, ocr_text, gps_lat, gps_lon, gps_city,
                    face_count, exif_date, exif_camera
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    caption = excluded.caption,
                    ocr_text = excluded.ocr_text,
                    gps_lat = excluded.gps_lat,
                    gps_lon = excluded.gps_lon,
                    gps_city = excluded.gps_city,
                    face_count = excluded.face_count,
                    exif_date = excluded.exif_date,
                    exif_camera = excluded.exif_camera
                """,
                (
                    file_id,
                    str(enrichment.get("caption", "")),
                    str(enrichment.get("ocr_text", "")),
                    enrichment.get("gps_lat"),
                    enrichment.get("gps_lon"),
                    str(enrichment.get("gps_city", "")),
                    int(enrichment.get("face_count", 0) or 0),
                    str(enrichment.get("exif_date", "")),
                    str(enrichment.get("exif_camera", "")),
                ),
            )
        conn.execute("DELETE FROM fts_content WHERE file_id = ?", (str(file_id),))
        conn.execute(
            "INSERT INTO fts_content(file_id, path, title, body, metadata_text) VALUES (?, ?, ?, ?, ?)",
            (
                str(file_id),
                str(metadata.get("file_path", "")),
                str(metadata.get("file_name", "")),
                document,
                _metadata_text(metadata, document, enrichment),
            ),
        )
        conn.commit()
        _hot_index().add_or_update(file_id, embedding)
        _maybe_dual_write_chroma(doc_id, embedding, metadata, document)
        _bump_cache_epoch()


def _find_doc_id_by_path(path: str) -> str | None:
    row = _connect().execute(
        "SELECT doc_id FROM manifest WHERE path = ? AND state = 'active'",
        (path,),
    ).fetchone()
    return str(row["doc_id"]) if row else None


def delete(doc_id: str) -> None:
    with _LOCK:
        conn = _connect()
        row = conn.execute("SELECT id FROM manifest WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None:
            return
        file_id = int(row["id"])
        conn.execute("UPDATE manifest SET state = 'deleted' WHERE id = ?", (file_id,))
        conn.execute("DELETE FROM vectors WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM enrichment WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM fts_content WHERE file_id = ?", (str(file_id),))
        conn.commit()
        _hot_index().delete(file_id)
        _bump_cache_epoch()


def delete_by_path(path: str | Path) -> None:
    doc_id = _find_doc_id_by_path(str(path))
    if doc_id:
        delete(doc_id)


def retire_path_versions(path: str | Path, *, keep_doc_id: str) -> int:
    with _LOCK:
        conn = _connect()
        rows = conn.execute(
            """
            SELECT id
            FROM manifest
            WHERE path = ? AND state = 'active' AND doc_id != ?
            """,
            (str(path), keep_doc_id),
        ).fetchall()
        if not rows:
            return 0
        file_ids = [int(row["id"]) for row in rows]
        for file_id in file_ids:
            conn.execute("UPDATE manifest SET state = 'deleted' WHERE id = ?", (file_id,))
            conn.execute("DELETE FROM vectors WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM enrichment WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM fts_content WHERE file_id = ?", (str(file_id),))
        conn.commit()
        for file_id in file_ids:
            _hot_index().delete(file_id)
        _bump_cache_epoch()
        return len(file_ids)


def exists(doc_id: str) -> bool:
    row = _connect().execute(
        "SELECT 1 FROM manifest WHERE doc_id = ? AND state = 'active'",
        (doc_id,),
    ).fetchone()
    return row is not None


def count() -> int:
    row = _connect().execute(
        "SELECT COUNT(*) AS n FROM manifest WHERE state = 'active'"
    ).fetchone()
    return int(row["n"]) if row else 0


def list_all(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    rows = _connect().execute(
        """
        SELECT *
        FROM manifest
        WHERE state = 'active'
        ORDER BY timestamp DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    ids: list[str] = []
    metadatas: list[dict[str, Any]] = []
    documents: list[str] = []
    for row in rows:
        ids.append(str(row["doc_id"]))
        metadatas.append(_row_to_metadata(row))
        documents.append(str(row["preview"]))
    return {"ids": ids, "metadatas": metadatas, "documents": documents}


def update_metadata(doc_id: str, metadata: dict[str, Any]) -> None:
    with _LOCK:
        conn = _connect()
        row = conn.execute("SELECT id FROM manifest WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None:
            return
        file_id = int(row["id"])
        existing = get_by_doc_ids([doc_id]).get(doc_id, {})
        merged = {**existing.get("metadata", {}), **metadata}
        _ensure_manifest_row(conn, doc_id, merged, str(merged.get("preview", "")))
        conn.execute("DELETE FROM fts_content WHERE file_id = ?", (str(file_id),))
        conn.execute(
            "INSERT INTO fts_content(file_id, path, title, body, metadata_text) VALUES (?, ?, ?, ?, ?)",
            (
                str(file_id),
                str(merged.get("file_path", "")),
                str(merged.get("file_name", "")),
                str(merged.get("preview", "")),
                _metadata_text(merged, str(merged.get("preview", "")), None),
            ),
        )
        conn.commit()
        _bump_cache_epoch()


def get_sources() -> list[str]:
    rows = _connect().execute(
        "SELECT DISTINCT source FROM manifest WHERE state = 'active' AND source != '' ORDER BY source"
    ).fetchall()
    return [str(row["source"]) for row in rows]


def get_by_doc_ids(doc_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not doc_ids:
        return {}
    placeholders = ",".join("?" for _ in doc_ids)
    rows = _connect().execute(
        f"""
        SELECT m.*, e.caption, e.ocr_text, e.gps_lat, e.gps_lon, e.gps_city,
               e.face_count, e.exif_date, e.exif_camera
        FROM manifest m
        LEFT JOIN enrichment e ON e.file_id = m.id
        WHERE m.doc_id IN ({placeholders})
        """,
        tuple(doc_ids),
    ).fetchall()
    payload: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = _row_to_metadata(row)
        metadata.update(
            {
                "caption": row["caption"] if "caption" in row.keys() else "",
                "ocr_text": row["ocr_text"] if "ocr_text" in row.keys() else "",
                "gps_lat": row["gps_lat"] if "gps_lat" in row.keys() else None,
                "gps_lon": row["gps_lon"] if "gps_lon" in row.keys() else None,
                "gps_city": row["gps_city"] if "gps_city" in row.keys() else "",
                "face_count": row["face_count"] if "face_count" in row.keys() else 0,
                "exif_date": row["exif_date"] if "exif_date" in row.keys() else "",
                "exif_camera": row["exif_camera"] if "exif_camera" in row.keys() else "",
            }
        )
        payload[str(row["doc_id"])] = {
            "file_id": int(row["id"]),
            "doc_id": str(row["doc_id"]),
            "metadata": metadata,
        }
    return payload


def _match_filters(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    media_category = filters.get("media_category")
    if media_category and metadata.get("media_category") != media_category:
        return False
    sources = filters.get("sources")
    if sources and metadata.get("source") not in set(sources):
        return False
    since = filters.get("since")
    if since and str(metadata.get("timestamp", "")) < str(since):
        return False
    must_exist = filters.get("path_exists")
    if must_exist and metadata.get("file_path") and not Path(str(metadata["file_path"])).exists():
        return False
    extra = filters.get("metadata") or {}
    for key, value in extra.items():
        if metadata.get(key) != value:
            return False
    return True


def dense_search(
    query_embedding: list[float],
    n_results: int = 20,
    filters: dict[str, Any] | None = None,
    oversample: int | None = None,
) -> list[Candidate]:
    limit = oversample or max(50, n_results * 5)
    rows = _hot_index().search(_normalize(query_embedding), limit)
    doc_rows: list[Candidate] = []
    file_ids = [file_id for file_id, _ in rows]
    if not file_ids:
        return []
    placeholders = ",".join("?" for _ in file_ids)
    data = _connect().execute(
        f"""
        SELECT m.*, e.caption, e.ocr_text, e.gps_lat, e.gps_lon, e.gps_city,
               e.face_count, e.exif_date, e.exif_camera
        FROM manifest m
        LEFT JOIN enrichment e ON e.file_id = m.id
        WHERE m.id IN ({placeholders}) AND m.state = 'active'
        """,
        tuple(file_ids),
    ).fetchall()
    by_file_id = {int(row["id"]): row for row in data}
    for file_id, distance in rows:
        row = by_file_id.get(file_id)
        if row is None:
            continue
        metadata = _row_to_metadata(row)
        metadata.update(
            {
                "caption": row["caption"] if "caption" in row.keys() else "",
                "ocr_text": row["ocr_text"] if "ocr_text" in row.keys() else "",
                "gps_lat": row["gps_lat"] if "gps_lat" in row.keys() else None,
                "gps_lon": row["gps_lon"] if "gps_lon" in row.keys() else None,
                "gps_city": row["gps_city"] if "gps_city" in row.keys() else "",
                "face_count": row["face_count"] if "face_count" in row.keys() else 0,
                "exif_date": row["exif_date"] if "exif_date" in row.keys() else "",
                "exif_camera": row["exif_camera"] if "exif_camera" in row.keys() else "",
            }
        )
        if not _match_filters(metadata, filters):
            continue
        doc_rows.append(
            Candidate(
                file_id=file_id,
                doc_id=str(row["doc_id"]),
                distance=float(distance),
                score=max(0.0, 1.0 - float(distance)),
                metadata=metadata,
            )
        )
        if len(doc_rows) >= n_results:
            break
    if len(doc_rows) >= n_results:
        return doc_rows
    return _linear_dense_search(query_embedding, n_results=n_results, filters=filters, skip={c.doc_id for c in doc_rows}, append=doc_rows)


def _linear_dense_search(
    query_embedding: list[float],
    *,
    n_results: int,
    filters: dict[str, Any] | None,
    skip: set[str],
    append: list[Candidate] | None = None,
) -> list[Candidate]:
    rows = _connect().execute(
        """
        SELECT m.*, v.embedding_json, e.caption, e.ocr_text, e.gps_lat, e.gps_lon,
               e.gps_city, e.face_count, e.exif_date, e.exif_camera
        FROM manifest m
        JOIN vectors v ON v.file_id = m.id
        LEFT JOIN enrichment e ON e.file_id = m.id
        WHERE m.state = 'active'
        """
    ).fetchall()
    ranked: list[Candidate] = list(append or [])
    query_embedding = _normalize(query_embedding)
    scored: list[Candidate] = []
    for row in rows:
        doc_id = str(row["doc_id"])
        if doc_id in skip:
            continue
        metadata = _row_to_metadata(row)
        metadata.update(
            {
                "caption": row["caption"] if "caption" in row.keys() else "",
                "ocr_text": row["ocr_text"] if "ocr_text" in row.keys() else "",
                "gps_lat": row["gps_lat"] if "gps_lat" in row.keys() else None,
                "gps_lon": row["gps_lon"] if "gps_lon" in row.keys() else None,
                "gps_city": row["gps_city"] if "gps_city" in row.keys() else "",
                "face_count": row["face_count"] if "face_count" in row.keys() else 0,
                "exif_date": row["exif_date"] if "exif_date" in row.keys() else "",
                "exif_camera": row["exif_camera"] if "exif_camera" in row.keys() else "",
            }
        )
        if not _match_filters(metadata, filters):
            continue
        embedding = [float(x) for x in json.loads(row["embedding_json"])]
        distance = _cosine_distance(query_embedding, embedding)
        scored.append(
            Candidate(
                file_id=int(row["id"]),
                doc_id=doc_id,
                distance=distance,
                score=max(0.0, 1.0 - distance),
                metadata=metadata,
            )
        )
    scored.sort(key=lambda item: item.distance)
    ranked.extend(scored[: max(0, n_results - len(ranked))])
    return ranked[:n_results]


def keyword_search(
    query: str,
    n_results: int = 20,
    filters: dict[str, Any] | None = None,
) -> list[Candidate]:
    tokens = [token for token in query.lower().split() if token.strip()]
    if not tokens:
        return []
    match_expr = " OR ".join(f'"{token.replace("\"", "")}"' for token in tokens[:10])
    rows = _connect().execute(
        """
        SELECT m.*, bm25(fts_content) AS rank,
               e.caption, e.ocr_text, e.gps_lat, e.gps_lon, e.gps_city,
               e.face_count, e.exif_date, e.exif_camera
        FROM fts_content
        JOIN manifest m ON m.id = CAST(fts_content.file_id AS INTEGER)
        LEFT JOIN enrichment e ON e.file_id = m.id
        WHERE fts_content MATCH ? AND m.state = 'active'
        ORDER BY rank
        LIMIT ?
        """,
        (match_expr, max(50, n_results * 5)),
    ).fetchall()
    results: list[Candidate] = []
    for row in rows:
        metadata = _row_to_metadata(row)
        metadata.update(
            {
                "caption": row["caption"] if "caption" in row.keys() else "",
                "ocr_text": row["ocr_text"] if "ocr_text" in row.keys() else "",
                "gps_lat": row["gps_lat"] if "gps_lat" in row.keys() else None,
                "gps_lon": row["gps_lon"] if "gps_lon" in row.keys() else None,
                "gps_city": row["gps_city"] if "gps_city" in row.keys() else "",
                "face_count": row["face_count"] if "face_count" in row.keys() else 0,
                "exif_date": row["exif_date"] if "exif_date" in row.keys() else "",
                "exif_camera": row["exif_camera"] if "exif_camera" in row.keys() else "",
            }
        )
        if not _match_filters(metadata, filters):
            continue
        rank = float(row["rank"]) if row["rank"] is not None else 0.0
        score = 1.0 / (1.0 + max(rank, 0.0))
        results.append(
            Candidate(
                file_id=int(row["id"]),
                doc_id=str(row["doc_id"]),
                distance=max(0.0, 1.0 - score),
                score=score,
                metadata=metadata,
            )
        )
        if len(results) >= n_results:
            break
    return results


def index_status() -> dict[str, Any]:
    return {
        "count": count(),
        "sqlite_path": str(config.SQLITE_PATH),
        "last_rebuild_at": _meta_get("index_last_rebuild_at", ""),
        **_hot_index().status(),
    }
