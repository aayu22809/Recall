"""Persistent search daemon — FastAPI server on 127.0.0.1:19847.

CLI: vef-daemon start | stop | status | sync [source] | check-embed

Design notes:
- Uses FastAPI lifespan (not the deprecated on_event) for startup/shutdown.
- Sources are tracked in an in-memory set (_known_sources), populated at
  startup from a sampled scan and updated on every /ingest call. This avoids
  the O(n) full-collection scan that the naive implementation would require.
- Connector syncs run in a dedicated background thread (_connector_sync_loop).
  The thread checks every 60s and only syncs when the daemon has been idle
  (no /search requests) for at least 30 seconds.
- cmd_start polls /health for up to 6 seconds so "Daemon started" only
  prints when the daemon is actually healthy, not just when the process spawns.
- stderr is redirected to ~/.vef/daemon.log so crashes are diagnosable.
"""

from __future__ import annotations

import json
import logging
import os
import errno
import signal
import socket
import sys
import threading
import time as _time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# ── Shared state ──────────────────────────────────────────────────────────────

_last_search_time: float = 0.0   # updated by /search; read by sync thread
_known_sources: set[str] = set()  # populated at startup; updated on ingest
_sync_lock = threading.Lock()
_sync_done = threading.Event()   # set whenever a sync run completes
_last_sync_result: dict[str, dict] = {}  # result of the most recent sync run
_last_connector_sync: dict[str, float] = {}
_ingest_lock = threading.Lock()
_ingest_in_flight = 0
_index_lock = threading.Lock()
_index_state: dict[str, object] = {
    "running": False,
    "queued": 0,
    "processed": 0,
    "embedded": 0,
    "skipped": 0,
    "errors": 0,
    "active_path": None,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
}

from . import config as _config
SYNC_STATE_FILE = _config.VEF_DIR / "sync_state.json"


def _load_sync_state() -> dict[str, float]:
    try:
        if not SYNC_STATE_FILE.exists():
            return {}
        raw = json.loads(SYNC_STATE_FILE.read_text())
        if not isinstance(raw, dict):
            return {}
        return {str(k): float(v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_sync_state() -> None:
    try:
        _config.ensure_vef_dirs()
        payload = {name: float(ts) for name, ts in _last_connector_sync.items()}
        SYNC_STATE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))
    except Exception as exc:
        logger.debug("Could not persist sync state: %s", exc)


def _is_idle() -> bool:
    """True when no search has happened in the last 30 seconds."""
    return _time.time() - _last_search_time > 30


def _track_ingest(delta: int) -> None:
    global _ingest_in_flight
    with _ingest_lock:
        _ingest_in_flight = max(0, _ingest_in_flight + delta)


def _snapshot_index_state() -> dict[str, object]:
    with _index_lock:
        return dict(_index_state)


def _set_index_state(**updates: object) -> dict[str, object]:
    with _index_lock:
        _index_state.update(updates)
        return dict(_index_state)


def _reset_index_state(total: int) -> dict[str, object]:
    return _set_index_state(
        running=True,
        queued=max(0, total),
        processed=0,
        embedded=0,
        skipped=0,
        errors=0,
        active_path=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        last_error=None,
    )


# ── Connector sync background thread ─────────────────────────────────────────


def _connector_specs() -> dict[str, tuple[float, str, str]]:
    """Connector config mapping: name -> (interval_s, module, class)."""
    from . import config

    return {
        "gmail": (config.GMAIL_POLL_INTERVAL, "vector_embedded_finder.connectors.gmail", "GmailConnector"),
        "gcal": (config.GCAL_POLL_INTERVAL, "vector_embedded_finder.connectors.gcal", "GCalConnector"),
        "calai": (config.CALAI_POLL_INTERVAL, "vector_embedded_finder.connectors.calai", "CalAIConnector"),
        "canvas": (config.LMS_POLL_INTERVAL, "vector_embedded_finder.connectors.canvas", "CanvasConnector"),
        "schoology": (config.LMS_POLL_INTERVAL, "vector_embedded_finder.connectors.schoology", "SchoologyConnector"),
        "gdrive": (config.GDRIVE_POLL_INTERVAL, "vector_embedded_finder.connectors.gdrive", "GDriveConnector"),
        "notion": (config.NOTION_POLL_INTERVAL, "vector_embedded_finder.connectors.notion", "NotionConnector"),
    }


def _run_connector_sync_once(
    *,
    force: bool,
    only_sources: set[str] | None = None,
) -> dict[str, dict]:
    """Run one connector sync pass and return per-connector status."""
    import importlib

    specs = _connector_specs()
    now = _time.time()
    for name in specs:
        _last_connector_sync.setdefault(name, 0.0)

    global _last_sync_result

    status: dict[str, dict] = {}
    # Non-blocking try.  If lock is busy, return in_progress immediately so
    # callers can switch to polling mode rather than blocking indefinitely.
    acquired = _sync_lock.acquire(blocking=False)
    if not acquired:
        return {name: {"status": "skipped", "reason": "sync_in_progress"} for name in specs}

    _sync_done.clear()
    try:
        for name, (interval, module_path, class_name) in specs.items():
            if only_sources and name not in only_sources:
                continue

            if not force and now - _last_connector_sync[name] < interval:
                status[name] = {"status": "skipped", "reason": "interval_not_reached"}
                continue

            try:
                module = importlib.import_module(module_path)
                conn_class = getattr(module, class_name)
                conn = conn_class()
                if not conn.is_authenticated():
                    status[name] = {"status": "skipped", "reason": "not_authenticated"}
                    continue

                since: datetime | None = None
                if _last_connector_sync[name] > 0:
                    since = datetime.fromtimestamp(_last_connector_sync[name], tz=timezone.utc)

                def _should_pause() -> bool:
                    return (_time.time() - _last_search_time) < 5.0

                results = conn.sync(
                    since,
                    should_pause=_should_pause,
                    budget_s=_config.CONNECTOR_SYNC_BUDGET_S,
                )

                embedded = sum(1 for r in results if r.get("status") == "embedded")
                errors = sum(1 for r in results if r.get("status") == "error")
                skipped = sum(1 for r in results if r.get("status") == "skipped")
                had_partial = errors > 0 or (embedded == 0 and skipped > 0) or (embedded > 0 and (errors > 0 or skipped > 0))
                if embedded > 0:
                    _last_connector_sync[name] = _time.time()
                    _save_sync_state()
                if embedded > 0:
                    _known_sources.add(name)

                status[name] = {
                    "status": "partial" if had_partial else "ok",
                    "embedded": embedded,
                    "total": len(results),
                }
                if errors > 0:
                    status[name]["error_count"] = errors
                logger.info("Connector sync %s: %d new items (of %d)", name, embedded, len(results))
            except Exception as exc:
                status[name] = {"status": "error", "error": str(exc)}
                logger.warning("Connector sync failed for %s: %s", name, exc)
    finally:
        _last_sync_result = dict(status)
        _sync_lock.release()
        _sync_done.set()
    return status


def _connector_sync_loop() -> None:
    """Run all authenticated connectors at their configured intervals."""

    # Give the daemon a 10s head-start before the first sync attempt.
    _time.sleep(10)

    while True:
        _time.sleep(60)  # check every minute

        if not _is_idle():
            logger.debug("Connector sync skipped — daemon not idle")
            continue

        _run_connector_sync_once(force=False)


def _load_watched_dirs() -> list[str]:
    try:
        if not _config.WATCHED_DIRS_FILE.exists():
            return []
        data = json.loads(_config.WATCHED_DIRS_FILE.read_text())
        if not isinstance(data, list):
            return []
        return [str(Path(item).expanduser().resolve()) for item in data if item]
    except Exception:
        return []


def _save_watched_dirs(dirs: list[str]) -> None:
    _config.ensure_vef_dirs()
    _config.WATCHED_DIRS_FILE.write_text(json.dumps(dirs, indent=2))


def _install_watcher(app, directories: list[str]) -> None:
    watcher = getattr(app.state, "watcher", None)
    if watcher is not None:
        try:
            watcher.stop()
        except Exception as exc:
            logger.debug("Could not stop existing watcher: %s", exc)

    cleaned = [Path(path).expanduser().resolve() for path in directories if path]
    if not cleaned:
        app.state.watcher = None
        return

    from .watcher import FileWatcher
    from .ingest import ingest_file

    def _on_new_file(path: Path) -> None:
        try:
            if path.exists():
                _track_ingest(1)
                result = ingest_file(path, source="files")
                if result.get("status") == "embedded":
                    _known_sources.add("files")
                logger.info("Auto-indexed: %s", path)
        except Exception as exc:
            logger.debug("Auto-index failed for %s: %s", path, exc)
        finally:
            _track_ingest(-1)

    watcher = FileWatcher()
    watcher.start(cleaned, _on_new_file)
    app.state.watcher = watcher
    logger.info("File watcher started on %d directories", len(cleaned))


def _index_watched_dirs_worker(directories: list[str]) -> None:
    from .ingest import ingest_directory

    supported_files: list[Path] = []
    for directory in directories:
        root = Path(directory).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            continue
        supported_files.extend(
            [path for path in sorted(root.glob("**/*")) if path.is_file() and _config.get_media_category(path.suffix.lower())]
        )

    total = len(supported_files)
    _reset_index_state(total)

    def _progress(processed: int, _total: int, result: dict) -> None:
        status = result.get("status")
        updates = {
            "processed": processed,
            "queued": max(0, total - processed),
            "active_path": result.get("path") or result.get("file_path"),
        }
        if status == "embedded":
            updates["embedded"] = int(_snapshot_index_state()["embedded"]) + 1
            _known_sources.add("files")
        elif status == "skipped":
            updates["skipped"] = int(_snapshot_index_state()["skipped"]) + 1
        elif status == "error":
            updates["errors"] = int(_snapshot_index_state()["errors"]) + 1
            updates["last_error"] = str(result.get("error", "indexing error"))
        _set_index_state(**updates)

    try:
        for directory in directories:
            root = Path(directory).expanduser().resolve()
            if not root.exists() or not root.is_dir():
                continue
            ingest_directory(root, source="files", progress_callback=_progress)
    except Exception as exc:
        _set_index_state(last_error=str(exc))
        logger.exception("Watched-folder indexing failed")
    finally:
        _set_index_state(
            running=False,
            active_path=None,
            finished_at=datetime.now(timezone.utc).isoformat(),
            queued=0,
        )


# ── FastAPI app ───────────────────────────────────────────────────────────────


def _build_app():
    from fastapi import Body, FastAPI, HTTPException
    from pydantic import BaseModel, ValidationError

    # ── Lifespan (replaces deprecated on_event) ───────────────────────────────

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── Startup ───────────────────────────────────────────────────────────
        from . import store, embedder, config

        config.ensure_vef_dirs()
        if config.EMBEDDING_PROVIDER == "gemini":
            try:
                config.get_api_key()
            except ValueError as exc:
                logger.error("Startup failed: %s", exc)
                raise RuntimeError(str(exc)) from exc
        store._get_collection()
        embedder.warmup_provider()
        _last_connector_sync.update(_load_sync_state())

        # Pre-populate sources cache without a full scan.
        try:
            coll = store._get_collection()
            total = coll.count()
            sample = coll.get(limit=min(total, 5000), include=["metadatas"])
            for m in sample.get("metadatas") or []:
                if m and m.get("source"):
                    _known_sources.add(m["source"])
            logger.debug("Sources cache pre-populated: %s", sorted(_known_sources))
        except Exception as exc:
            logger.warning("Could not pre-populate sources cache: %s", exc)

        # Start filesystem watcher if directories are configured.
        app.state.watcher = None
        try:
            dirs = _load_watched_dirs()
            if dirs:
                _install_watcher(app, dirs)
        except Exception as exc:
            logger.warning("Could not start file watcher: %s", exc)

        # Start connector sync background thread.
        sync_thread = threading.Thread(
            target=_connector_sync_loop,
            daemon=True,
            name="vef-connector-sync",
        )
        sync_thread.start()

        logger.info("VEF daemon ready — port %d", config.DAEMON_PORT)

        yield  # ── server is now running ──────────────────────────────────────

        # ── Shutdown ──────────────────────────────────────────────────────────
        watcher = getattr(app.state, "watcher", None)
        if watcher:
            watcher.stop()

    app = FastAPI(title="VEF Daemon", version="1.1.0", lifespan=lifespan)

    # ── Models ────────────────────────────────────────────────────────────────

    class SearchRequest(BaseModel):
        query: str
        n_results: int = 20
        sources: Optional[List[str]] = None

    class SearchResult(BaseModel):
        id: str
        similarity: float
        file_path: str
        file_name: str
        media_category: str
        timestamp: str
        description: str
        source: str
        preview: str
        metadata: dict = {}

    class IngestRequest(BaseModel):
        path: str
        source: str = "files"
        description: str = ""

    class SyncRequest(BaseModel):
        source: Optional[str] = None

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.post("/search", response_model=List[SearchResult])
    async def search(req: dict = Body(...)):
        global _last_search_time
        _last_search_time = _time.time()

        try:
            parsed = SearchRequest.model_validate(req)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

        if not parsed.query.strip():
            return []

        from .search import search as vef_search
        try:
            results = vef_search(
                parsed.query,
                n_results=parsed.n_results,
                sources=parsed.sources,
            )
            return results
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/health")
    async def health():
        # Liveness check must be constant-time. Do NOT touch the DB here —
        # chromadb's count() serialises and can take hundreds of ms under load,
        # which would make the daemon look dead whenever Raycast polls.
        return {
            "status": "ok",
            "service": "recall-daemon",
            "version": app.version,
            "port": _config.DAEMON_PORT,
        }

    @app.get("/stats")
    async def stats():
        # Potentially slow — calls chromadb count(). Separate from /health
        # so liveness probes don't pay this cost.
        from . import store
        return {"status": "ok", "count": store.count()}

    @app.get("/sources")
    async def sources():
        return {"sources": sorted(_known_sources)}

    @app.get("/progress")
    async def progress():
        from . import store
        with _ingest_lock:
            in_flight = _ingest_in_flight
        status = _snapshot_index_state()
        return {
            "indexing": bool(status.get("running")) or in_flight > 0,
            "queued": int(status.get("queued", 0)) + in_flight,
            "processed": int(status.get("processed", 0)),
            "embedded": int(status.get("embedded", 0)),
            "skipped": int(status.get("skipped", 0)),
            "errors": int(status.get("errors", 0)),
            "total_indexed": store.count(),
        }

    @app.get("/index/status")
    async def index_status():
        return _snapshot_index_state()

    @app.post("/ingest")
    async def ingest(req: dict = Body(...)):
        from .ingest import ingest_file
        import asyncio

        try:
            parsed = IngestRequest.model_validate(req)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

        loop = asyncio.get_running_loop()
        _track_ingest(1)
        try:
            result = await loop.run_in_executor(
                None,
                lambda: ingest_file(parsed.path, source=parsed.source, description=parsed.description),
            )
        finally:
            _track_ingest(-1)
        if result.get("status") == "embedded":
            _known_sources.add(parsed.source)
        return result

    @app.get("/connector-status")
    async def connector_status():
        import importlib
        specs = _connector_specs()
        result: dict[str, dict] = {}
        for name, (interval, module_path, class_name) in specs.items():
            last = _last_connector_sync.get(name, 0.0)
            try:
                module = importlib.import_module(module_path)
                conn = getattr(module, class_name)()
                authed = conn.is_authenticated()
            except Exception:
                authed = False
            result[name] = {
                "authenticated": authed,
                "last_sync": last,
                "last_sync_iso": datetime.fromtimestamp(last, tz=timezone.utc).isoformat() if last else None,
                "interval_s": interval,
                "last_result": _last_sync_result.get(name, {}),
            }
        return result

    @app.post("/sync")
    async def sync(req: dict | None = Body(default=None)):
        import asyncio

        parsed_source: str | None = None
        if req is not None:
            try:
                parsed = SyncRequest.model_validate(req)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors()) from exc
            if parsed.source:
                parsed_source = parsed.source.strip().lower()
                if parsed_source not in _connector_specs():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unknown source '{parsed_source}'. Valid sources: {', '.join(sorted(_connector_specs()))}",
                    )

        # Always return instantly — start sync in background if not already running.
        if _sync_lock.locked():
            return {"status": "in_progress", "last_sync": _last_sync_result}

        # Fire and forget: run sync in daemon thread, don't await it.
        only = {parsed_source} if parsed_source else None
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None,
            lambda: _run_connector_sync_once(force=True, only_sources=only),
        )
        return {"status": "started", "last_sync": _last_sync_result}

    @app.get("/sync-running")
    async def sync_running():
        return {"running": _sync_lock.locked()}

    @app.get("/watched-dirs")
    async def get_watched_dirs():
        return {"dirs": _load_watched_dirs(), "restart_required": False}

    @app.post("/watched-dirs")
    async def add_watched_dir(req: dict = Body(default={})):
        path = str(req.get("path", "")).strip()
        if not path:
            raise HTTPException(status_code=400, detail="path required")
        resolved = str(Path(path).expanduser().resolve())
        if not Path(resolved).exists():
            raise HTTPException(status_code=400, detail=f"Path does not exist: {resolved}")
        if not Path(resolved).is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {resolved}")
        dirs = _load_watched_dirs()
        if resolved not in dirs:
            dirs.append(resolved)
            _save_watched_dirs(dirs)
            _install_watcher(app, dirs)
            threading.Thread(
                target=_index_watched_dirs_worker,
                args=([resolved],),
                daemon=True,
                name="vef-watch-add-index",
            ).start()
        return {"dirs": dirs, "restart_required": False}

    @app.delete("/watched-dirs")
    async def remove_watched_dir(req: dict = Body(default={})):
        path = str(req.get("path", "")).strip()
        dirs = [d for d in _load_watched_dirs() if d != path]
        _save_watched_dirs(dirs)
        _install_watcher(app, dirs)
        return {"dirs": dirs, "restart_required": False}

    @app.post("/index/watched-dirs")
    async def index_watched_dirs():
        dirs = _load_watched_dirs()
        if not dirs:
            _set_index_state(
                running=False,
                queued=0,
                processed=0,
                embedded=0,
                skipped=0,
                errors=0,
                active_path=None,
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                last_error=None,
            )
            return {"status": "idle", "reason": "no_watched_dirs", "index": _snapshot_index_state()}
        if bool(_snapshot_index_state().get("running")):
            return {"status": "in_progress", "index": _snapshot_index_state()}
        threading.Thread(
            target=_index_watched_dirs_worker,
            args=(dirs,),
            daemon=True,
            name="vef-folder-index",
        ).start()
        return {"status": "started", "index": _snapshot_index_state()}

    @app.get("/watched-dirs/stats")
    async def watched_dirs_stats():
        """Per-folder indexed document count.

        Walks the chroma collection metadata once (sampled, capped at 5000
        docs) and groups by the watched-folder root each `file_path` falls
        under. Returns 0 for unmatched folders rather than 404 so the UI
        can render a row before any documents have indexed.
        """
        from . import store
        dirs = _load_watched_dirs()
        roots = [Path(d).resolve() for d in dirs]
        counts: dict[str, int] = {str(r): 0 for r in roots}
        try:
            coll = store._get_collection()
            total = coll.count()
            sample = coll.get(limit=min(total, 5000), include=["metadatas"])
            for m in sample.get("metadatas") or []:
                fp = (m or {}).get("file_path")
                if not fp or not isinstance(fp, str):
                    continue
                if fp.startswith(("gmail://", "gcal://", "gdrive://", "calai://", "canvas://", "schoology://", "notion://")):
                    continue
                try:
                    p = Path(fp).resolve()
                except Exception:
                    continue
                for r in roots:
                    try:
                        p.relative_to(r)
                        counts[str(r)] += 1
                        break
                    except ValueError:
                        continue
        except Exception as exc:
            logger.debug("watched-dirs/stats failed: %s", exc)
        return {"stats": [{"path": k, "count": v} for k, v in counts.items()]}

    @app.put("/credentials/{source}")
    async def put_credentials(source: str, req: dict = Body(...)):
        """Write a connector credential JSON to ~/.vef/credentials/<source>.json.

        Used by the Tauri shell to deliver OAuth tokens fetched in Rust to the
        Python connector layer without the connector ever touching a Cloud
        Console-downloaded client_secrets file. Body is the full token shape
        google.oauth2.credentials.Credentials.from_authorized_user_info accepts
        for Google sources, or the provider-specific JSON shape for others.
        """
        from . import config
        allowed = {"gmail", "gcal", "gdrive", "calai", "canvas", "schoology", "notion"}
        normalized = source.strip().lower()
        if normalized not in allowed:
            raise HTTPException(status_code=400, detail=f"Unknown source '{source}'")
        if not isinstance(req, dict) or not req:
            raise HTTPException(status_code=400, detail="Request body must be a non-empty JSON object")
        config.ensure_vef_dirs()
        target_name = "gmail.json" if normalized in {"gmail", "gcal", "gdrive"} else f"{normalized}.json"
        target = config.CREDENTIALS_DIR / target_name
        target.write_text(json.dumps(req, indent=2))
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return {"ok": True, "path": str(target)}

    @app.delete("/credentials/{source}")
    async def delete_credentials(source: str):
        """Disconnect a connector by removing its credential file."""
        from . import config
        allowed = {"gmail", "gcal", "gdrive", "calai", "canvas", "schoology", "notion"}
        normalized = source.strip().lower()
        if normalized not in allowed:
            raise HTTPException(status_code=400, detail=f"Unknown source '{source}'")
        target_name = "gmail.json" if normalized in {"gmail", "gcal", "gdrive"} else f"{normalized}.json"
        target = config.CREDENTIALS_DIR / target_name
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/configure")
    async def configure(req: dict = Body(default={})):
        """Write API keys to ~/.vef/.env so they persist across daemon restarts."""
        from pathlib import Path as _Path
        env_file = _Path.home() / ".vef" / ".env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if env_file.exists():
            lines = env_file.read_text().splitlines()

        def _set(key: str, val: str) -> None:
            nonlocal lines
            lines = [l for l in lines if not l.startswith(f"{key}=")]
            lines.append(f"{key}={val}")

        if req.get("gemini_api_key"):
            _set("GEMINI_API_KEY", str(req["gemini_api_key"]))
            os.environ["GEMINI_API_KEY"] = str(req["gemini_api_key"])
        if req.get("nim_api_key"):
            _set("NIM_API_KEY", str(req["nim_api_key"]))
            os.environ["NIM_API_KEY"] = str(req["nim_api_key"])
        if req.get("vef_embedding_provider"):
            _set("VEF_EMBEDDING_PROVIDER", str(req["vef_embedding_provider"]).strip().lower())
            os.environ["VEF_EMBEDDING_PROVIDER"] = str(req["vef_embedding_provider"]).strip().lower()
        if req.get("vef_embedding_model"):
            _set("VEF_EMBEDDING_MODEL", str(req["vef_embedding_model"]))
            os.environ["VEF_EMBEDDING_MODEL"] = str(req["vef_embedding_model"])
        if req.get("vef_embedding_dimensions"):
            _set("VEF_EMBEDDING_DIMENSIONS", str(req["vef_embedding_dimensions"]))
            os.environ["VEF_EMBEDDING_DIMENSIONS"] = str(req["vef_embedding_dimensions"])
        if req.get("vef_ollama_base_url"):
            _set("VEF_OLLAMA_BASE_URL", str(req["vef_ollama_base_url"]).rstrip("/"))
            os.environ["VEF_OLLAMA_BASE_URL"] = str(req["vef_ollama_base_url"]).rstrip("/")
        if req.get("vef_ollama_embed_model"):
            _set("VEF_OLLAMA_EMBED_MODEL", str(req["vef_ollama_embed_model"]))
            os.environ["VEF_OLLAMA_EMBED_MODEL"] = str(req["vef_ollama_embed_model"])
        if req.get("vef_nim_embed_url"):
            _set("VEF_NIM_EMBED_URL", str(req["vef_nim_embed_url"]))
            os.environ["VEF_NIM_EMBED_URL"] = str(req["vef_nim_embed_url"])
        if req.get("vef_nim_embed_model"):
            _set("VEF_NIM_EMBED_MODEL", str(req["vef_nim_embed_model"]))
            os.environ["VEF_NIM_EMBED_MODEL"] = str(req["vef_nim_embed_model"])
        if req.get("canvas_api_key"):
            _set("CANVAS_API_KEY", str(req["canvas_api_key"]))
            os.environ["CANVAS_API_KEY"] = str(req["canvas_api_key"])
        if req.get("canvas_base_url"):
            _set("CANVAS_BASE_URL", str(req["canvas_base_url"]))
            os.environ["CANVAS_BASE_URL"] = str(req["canvas_base_url"])
        if req.get("schoology_consumer_key"):
            _set("SCHOOLOGY_CONSUMER_KEY", str(req["schoology_consumer_key"]))
            os.environ["SCHOOLOGY_CONSUMER_KEY"] = str(req["schoology_consumer_key"])
        if req.get("schoology_consumer_secret"):
            _set("SCHOOLOGY_CONSUMER_SECRET", str(req["schoology_consumer_secret"]))
            os.environ["SCHOOLOGY_CONSUMER_SECRET"] = str(req["schoology_consumer_secret"])
        if req.get("schoology_base_url"):
            _set("SCHOOLOGY_BASE_URL", str(req["schoology_base_url"]))
            os.environ["SCHOOLOGY_BASE_URL"] = str(req["schoology_base_url"])
        env_file.write_text("\n".join(lines) + "\n")
        return {"ok": True}

    return app


# ── Server start ──────────────────────────────────────────────────────────────


def _configure_logging() -> None:
    """Install a rotating file handler for ~/.vef/daemon.log.

    Prevents unbounded growth from repetitive warnings (e.g. chromadb compactor
    errors spamming _safe_count). Keeps the last 2 MB * 3 rotations.
    """
    from logging.handlers import RotatingFileHandler
    from . import config

    root = logging.getLogger()
    # If already configured (hot reload), skip.
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return

    config.ensure_vef_dirs()
    log_path = config.VEF_DIR / "daemon.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _run_server() -> None:
    import asyncio
    import uvicorn
    from . import config

    config.ensure_vef_dirs()
    _configure_logging()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((config.DAEMON_HOST, config.DAEMON_PORT))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            if _poll_health(config.DAEMON_HOST, config.DAEMON_PORT, timeout_s=2.0):
                print(f"another daemon already serving on {config.DAEMON_PORT}")
                sys.exit(0)
            print(f"port {config.DAEMON_PORT} already in use; try `vef-daemon stop`", file=sys.stderr)
            sys.exit(1)
        raise
    finally:
        try:
            sock.close()
        except Exception:
            pass

    config.PID_FILE.write_text(str(os.getpid()))

    def _cleanup(signum=None, frame=None):
        try:
            config.PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    app = _build_app()

    try:
        try:
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=config.DAEMON_HOST,
                    port=config.DAEMON_PORT,
                    loop="asyncio",
                    log_level="warning",
                    access_log=False,
                )
            )
            asyncio.run(server.serve())
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                if _poll_health(config.DAEMON_HOST, config.DAEMON_PORT, timeout_s=2.0):
                    print(f"another daemon already serving on {config.DAEMON_PORT}")
                    sys.exit(0)
                print(f"port {config.DAEMON_PORT} already in use; try `vef-daemon stop`", file=sys.stderr)
                sys.exit(1)
            raise
    finally:
        _cleanup()


# ── CLI commands ──────────────────────────────────────────────────────────────


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_pid() -> int | None:
    from . import config
    try:
        return int(config.PID_FILE.read_text().strip())
    except Exception:
        return None


def _poll_health(host: str, port: int, timeout_s: float) -> bool:
    """Poll /health until it responds OK or timeout expires.

    /health is a constant-time liveness check (see app.health). We give httpx
    a generous timeout so a momentary CPU spike does not fail the probe.
    """
    import socket
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                pass
            import httpx
            resp = httpx.get(f"http://{host}:{port}/health", timeout=2.0)
            if resp.is_success:
                return True
        except Exception:
            pass
        _time.sleep(0.3)
    return False


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already bound to host:port (regardless of pid file)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        sock.bind((host, port))
        return False
    except OSError as exc:
        return exc.errno == errno.EADDRINUSE
    finally:
        try:
            sock.close()
        except Exception:
            pass


def cmd_start() -> None:
    from . import config
    config.ensure_vef_dirs()

    # Case 1: PID file points at a live process — verify it's actually serving.
    pid = _read_pid()
    if pid and _pid_running(pid):
        if _poll_health(config.DAEMON_HOST, config.DAEMON_PORT, timeout_s=3.0):
            print(f"Daemon already running (pid {pid})")
            return
        # Live pid but not healthy — kill it so we can start fresh.
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                _time.sleep(0.2)
                if not _pid_running(pid):
                    break
        except Exception:
            pass
        config.PID_FILE.unlink(missing_ok=True)

    # Case 2: PID file stale — clear it.
    if pid and not _pid_running(pid):
        config.PID_FILE.unlink(missing_ok=True)

    # Case 3: Port already in use by something that's not in our PID file.
    # Check /health — if it's our daemon, treat as already running.
    if _port_in_use(config.DAEMON_HOST, config.DAEMON_PORT):
        if _poll_health(config.DAEMON_HOST, config.DAEMON_PORT, timeout_s=3.0):
            print(f"Daemon already running on port {config.DAEMON_PORT} (pid file missing)")
            return
        print(
            f"Port {config.DAEMON_PORT} is in use but /health is not responding.",
            file=sys.stderr,
        )
        print("Run `vef-daemon stop` or `lsof -i :19847` to investigate.", file=sys.stderr)
        sys.exit(1)

    # Case 4: Fresh start. Spawn the daemon.
    log_path = config.VEF_DIR / "daemon.log"
    project_root = str(Path(__file__).parent.parent)
    env = os.environ.copy()
    existing_py_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{project_root}:{existing_py_path}" if existing_py_path else project_root
    import subprocess
    with open(log_path, "a") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "vector_embedded_finder.daemon", "_serve"],
            env=env,
            cwd=project_root,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
        )

    if _poll_health(config.DAEMON_HOST, config.DAEMON_PORT, timeout_s=15.0):
        # Verify the child is still alive (it may have exited after health came up
        # if another daemon races us — though we guarded against that above).
        if proc.poll() is not None:
            pid_from_file = _read_pid()
            if pid_from_file:
                print(f"Daemon running (pid {pid_from_file}); spawned helper pid {proc.pid} exited.")
            else:
                print("Daemon responding but spawned pid exited — investigate daemon.log.")
            return
        print(f"Daemon started (pid {proc.pid})")
    else:
        print(f"Daemon process spawned (pid {proc.pid}) but did not respond within 15s.")
        print(f"Check logs: {log_path}")


def cmd_stop() -> None:
    pid = _read_pid()
    if not pid:
        print("Daemon not running (no PID file)")
        return
    if not _pid_running(pid):
        from . import config
        config.PID_FILE.unlink(missing_ok=True)
        print("Daemon not running (stale PID cleaned up)")
        return
    os.kill(pid, signal.SIGTERM)
    # Wait briefly so subsequent commands don't see a stale state
    for _ in range(10):
        _time.sleep(0.3)
        if not _pid_running(pid):
            break
    print(f"Daemon stopped (pid {pid})")


def cmd_status() -> None:
    from . import config
    pid = _read_pid()
    if not pid or not _pid_running(pid):
        log_path = config.VEF_DIR / "daemon.log"
        print("Daemon: stopped")
        if log_path.exists():
            # Show last 5 lines of log to help diagnose crashes
            lines = log_path.read_text().splitlines()
            tail = lines[-5:] if len(lines) >= 5 else lines
            if any(tail):
                print(f"Last log lines ({log_path}):")
                for ln in tail:
                    print(f"  {ln}")
        return

    try:
        import httpx
        # /stats may be slow if chromadb is backlogged — keep it short.
        resp = httpx.get(
            f"http://{config.DAEMON_HOST}:{config.DAEMON_PORT}/stats",
            timeout=5.0,
        )
        data = resp.json()
        print(f"Daemon: running (pid {pid}, {data.get('count', '?')} documents indexed)")
    except Exception:
        # Stats slow → fall back to /health for liveness.
        try:
            import httpx
            resp = httpx.get(
                f"http://{config.DAEMON_HOST}:{config.DAEMON_PORT}/health",
                timeout=2.0,
            )
            if resp.is_success:
                print(f"Daemon: running (pid {pid}, index stats unavailable)")
                return
        except Exception:
            pass
        print(f"Daemon: running (pid {pid}, health check failed — may still be starting up)")


def cmd_sync(source: str | None = None) -> None:
    from . import config
    import httpx

    payload = {"source": source} if source else {}
    try:
        resp = httpx.post(
            f"http://{config.DAEMON_HOST}:{config.DAEMON_PORT}/sync",
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Sync failed: {exc}")
        print("Start daemon first: vef-daemon start")
        sys.exit(1)

    data = resp.json()
    sync_data = data.get("last_sync", {})
    if not sync_data:
        print("No connectors synced.")
        return

    for name in sorted(sync_data):
        result = sync_data[name]
        if result.get("status") == "ok":
            print(f"{name}: {result.get('embedded', 0)} embedded of {result.get('total', 0)}")
        elif result.get("status") == "skipped":
            print(f"{name}: skipped ({result.get('reason', 'unknown')})")
        else:
            print(f"{name}: error ({result.get('error', 'unknown error')})")


def cmd_check_embed() -> None:
    from . import embedder

    try:
        vec = embedder.embed_query("embedding health check")
    except Exception as exc:
        print(f"Embedding provider check failed: {exc}")
        sys.exit(1)

    if not vec:
        print("Embedding provider check failed: empty embedding vector")
        sys.exit(1)

    print(f"Embedding provider OK (dimension {len(vec)})")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] == "start":
        cmd_start()
    elif args[0] == "stop":
        cmd_stop()
    elif args[0] == "status":
        cmd_status()
    elif args[0] == "sync":
        cmd_sync(args[1].strip().lower() if len(args) > 1 else None)
    elif args[0] == "check-embed":
        cmd_check_embed()
    elif args[0] == "_serve":
        _run_server()
    else:
        print("Usage: vef-daemon start|stop|status|sync [source]|check-embed")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_serve":
        _run_server()
    else:
        main()
