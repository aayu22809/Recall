"""Runtime migration from legacy ~/.vef and Chroma-backed state."""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger(__name__)


def _read_status() -> dict[str, Any]:
    if not config.MIGRATION_STATUS_PATH.exists():
        return {"status": "not_started"}
    try:
        payload = json.loads(config.MIGRATION_STATUS_PATH.read_text())
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {"status": "not_started"}


def _write_status(status: str, **extra: Any) -> dict[str, Any]:
    payload = {"status": status, **extra, "updated_at": time.time()}
    config.ensure_runtime_dirs()
    config.MIGRATION_STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def status() -> dict[str, Any]:
    return _read_status()


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists() or dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def _migrate_filesystem_state() -> None:
    legacy = config.LEGACY_VEF_DIR
    if not legacy.exists():
        return
    for name in ("credentials", "watched_dirs.json", "sync_state.json", ".env"):
        _copy_if_exists(legacy / name, config.RECALL_HOME / name)


def _import_chroma() -> dict[str, Any]:
    try:
        import chromadb
    except Exception as exc:
        return {"imported": 0, "skipped": 0, "error": f"chromadb unavailable: {exc}"}

    if not config.CHROMA_DIR.exists():
        return {"imported": 0, "skipped": 0}

    from . import store

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    coll = client.get_or_create_collection(
        name=config.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    total = int(coll.count())
    if total <= 0:
        return {"imported": 0, "skipped": 0}

    imported = 0
    skipped = 0
    batch_size = max(1, int(config.INDEX_REBUILD_BATCH))
    for offset in range(0, total, batch_size):
        rows = coll.get(
            include=["embeddings", "metadatas", "documents"],
            limit=batch_size,
            offset=offset,
        )
        ids = rows.get("ids", [])
        embeddings = rows.get("embeddings") or []
        metadatas = rows.get("metadatas") or []
        documents = rows.get("documents") or []
        for idx, doc_id in enumerate(ids):
            if store.exists(doc_id):
                skipped += 1
                continue
            embedding = embeddings[idx] if idx < len(embeddings) else None
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            document = documents[idx] if idx < len(documents) else ""
            if not embedding:
                skipped += 1
                continue
            store.add(
                str(doc_id),
                [float(v) for v in embedding],
                dict(metadata or {}),
                document=str(document or ""),
            )
            imported += 1
    return {"imported": imported, "skipped": skipped}


def ensure_migrated() -> dict[str, Any]:
    existing = _read_status()
    if existing.get("status") == "complete":
        return existing

    _write_status("running")
    try:
        _migrate_filesystem_state()
        from . import store

        store.initialize()
        chroma_result = _import_chroma()
        result = _write_status("complete", chroma=chroma_result)
        return result
    except Exception as exc:
        logger.exception("Migration failed")
        return _write_status("failed", error=str(exc))
