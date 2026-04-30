"""Persistent Recall daemon.

Primary transport is a Unix domain socket under ~/.recall/recall.sock.
Optional localhost HTTP compatibility can be enabled with
RECALL_ENABLE_COMPAT_HTTP=1 for migration and diagnostics.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time as _time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

import httpx

logger = logging.getLogger(__name__)

_last_search_time: float = 0.0
_sync_lock = threading.Lock()
_sync_done = threading.Event()
_last_sync_result: dict[str, dict] = {}
_last_connector_sync: dict[str, float] = {}
_ingest_lock = threading.Lock()
_ingest_in_flight = 0
_watcher = None
_watcher_queue_depth: Callable[[], int] | None = None
_watcher_lock = threading.Lock()

from . import config as _config

SYNC_STATE_FILE = _config.RECALL_HOME / "sync_state.json"


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
        _config.ensure_runtime_dirs()
        payload = {name: float(ts) for name, ts in _last_connector_sync.items()}
        SYNC_STATE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))
    except Exception as exc:
        logger.debug("Could not persist sync state: %s", exc)


def _is_idle() -> bool:
    return _time.time() - _last_search_time > 30


def _track_ingest(delta: int) -> None:
    global _ingest_in_flight
    with _ingest_lock:
        _ingest_in_flight = max(0, _ingest_in_flight + delta)


def _connector_specs() -> dict[str, tuple[float, str, str]]:
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
    import importlib

    specs = _connector_specs()
    now = _time.time()
    for name in specs:
        _last_connector_sync.setdefault(name, 0.0)

    global _last_sync_result

    status: dict[str, dict] = {}
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
                embedded = sum(1 for row in results if row.get("status") == "embedded")
                errors = sum(1 for row in results if row.get("status") == "error")
                skipped = sum(1 for row in results if row.get("status") == "skipped")
                had_partial = errors > 0 or (embedded == 0 and skipped > 0) or (
                    embedded > 0 and (errors > 0 or skipped > 0)
                )
                if embedded > 0:
                    _last_connector_sync[name] = _time.time()
                    _save_sync_state()
                status[name] = {
                    "status": "partial" if had_partial else "ok",
                    "embedded": embedded,
                    "total": len(results),
                }
                if errors > 0:
                    status[name]["error_count"] = errors
            except Exception as exc:
                status[name] = {"status": "error", "error": str(exc)}
                logger.warning("Connector sync failed for %s: %s", name, exc)
    finally:
        _last_sync_result = dict(status)
        _sync_lock.release()
        _sync_done.set()
    return status


def _connector_sync_loop() -> None:
    _time.sleep(10)
    while True:
        _time.sleep(60)
        if not _is_idle():
            continue
        _run_connector_sync_once(force=False)


def _build_app():
    from fastapi import Body, FastAPI, HTTPException
    from pydantic import BaseModel, ValidationError

    def _load_watched_dirs() -> list[str]:
        if not _config.WATCHED_DIRS_FILE.exists():
            return []
        try:
            payload = json.loads(_config.WATCHED_DIRS_FILE.read_text())
            if isinstance(payload, list):
                return [str(row) for row in payload if row]
        except Exception:
            pass
        return []

    def _persist_watched_dirs(dirs: list[str]) -> None:
        _config.ensure_runtime_dirs()
        _config.WATCHED_DIRS_FILE.write_text(json.dumps(dirs, indent=2))

    def _on_new_file(path: Path) -> None:
        from .ingest import ingest_file

        try:
            if path.exists():
                _track_ingest(1)
                ingest_file(path, source="files")
        except Exception as exc:
            logger.debug("Auto-index failed for %s: %s", path, exc)
        finally:
            _track_ingest(-1)

    def _on_delete_file(path: Path) -> None:
        from . import store

        try:
            store.delete_by_path(path)
        except Exception as exc:
            logger.debug("Delete handling failed for %s: %s", path, exc)

    def _restart_watcher(dirs: list[str]) -> None:
        from .watcher import FileWatcher

        resolved_dirs = [Path(row).expanduser().resolve() for row in dirs]
        existing_dirs = [row for row in resolved_dirs if row.exists()]
        with _watcher_lock:
            global _watcher_queue_depth, _watcher
            if _watcher is not None:
                _watcher.stop()
                _watcher = None
                _watcher_queue_depth = None
            if not existing_dirs:
                return
            watcher = FileWatcher()
            watcher.start(existing_dirs, _on_new_file, delete_callback=_on_delete_file)
            _watcher = watcher
            _watcher_queue_depth = watcher.queued
            logger.info("File watcher started on %d directories", len(existing_dirs))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from . import embedder, migration, store

        _config.ensure_runtime_dirs()
        migration.ensure_migrated()
        store.initialize()
        embedder.warmup_provider()
        _last_connector_sync.update(_load_sync_state())

        try:
            _restart_watcher(_load_watched_dirs())
        except Exception as exc:
            logger.warning("Could not start file watcher: %s", exc)

        sync_thread = threading.Thread(
            target=_connector_sync_loop,
            daemon=True,
            name="recall-connector-sync",
        )
        sync_thread.start()
        logger.info("Recall daemon ready at %s", _config.SOCKET_PATH)
        yield
        with _watcher_lock:
            global _watcher_queue_depth, _watcher
            if _watcher is not None:
                _watcher.stop()
                _watcher = None
            _watcher_queue_depth = None

    app = FastAPI(title="Recall Daemon", version="2.0.0", lifespan=lifespan)

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

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        from . import migration, store

        return {
            "status": "ok",
            "migration": migration.status().get("status", "not_started"),
            "index": store.index_status(),
        }

    @app.get("/stats")
    async def stats():
        from . import store

        return {"status": "ok", "count": store.count()}

    @app.get("/sources")
    async def sources():
        from . import store

        return {"sources": store.get_sources()}

    @app.get("/progress")
    async def progress():
        from . import store

        with _ingest_lock:
            in_flight = _ingest_in_flight
        queued = _watcher_queue_depth() if _watcher_queue_depth else 0
        return {
            "indexing": (in_flight > 0 or queued > 0),
            "queued": queued + in_flight,
            "total_indexed": store.count(),
        }

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
        from .search import search as recall_search

        try:
            return recall_search(parsed.query, n_results=parsed.n_results, sources=parsed.sources)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/ingest")
    async def ingest(req: dict = Body(...)):
        import asyncio
        from .ingest import ingest_file

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
        return result

    @app.get("/connector-status")
    async def connector_status():
        import importlib

        result: dict[str, dict] = {}
        for name, (interval, module_path, class_name) in _connector_specs().items():
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
                    raise HTTPException(status_code=400, detail=f"Unknown source '{parsed_source}'")

        if _sync_lock.locked():
            return {"status": "in_progress", "last_sync": _last_sync_result}

        only = {parsed_source} if parsed_source else None
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, lambda: _run_connector_sync_once(force=True, only_sources=only))
        return {"status": "started", "last_sync": _last_sync_result}

    @app.get("/sync-running")
    async def sync_running():
        return {"running": _sync_lock.locked()}

    @app.get("/watched-dirs")
    async def get_watched_dirs():
        return {"dirs": _load_watched_dirs()}

    @app.post("/watched-dirs")
    async def add_watched_dir(req: dict = Body(default={})):
        path = str(req.get("path", "")).strip()
        if not path:
            raise HTTPException(status_code=400, detail="path required")
        resolved = str(Path(path).expanduser().resolve())
        dirs = _load_watched_dirs()
        if resolved not in dirs:
            dirs.append(resolved)
            _persist_watched_dirs(dirs)
            try:
                _restart_watcher(dirs)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Watcher reload failed: {exc}") from exc
        return {"dirs": dirs}

    @app.delete("/watched-dirs")
    async def remove_watched_dir(req: dict = Body(default={})):
        path = str(req.get("path", "")).strip()
        resolved = str(Path(path).expanduser().resolve()) if path else ""
        dirs = [row for row in _load_watched_dirs() if row != path and row != resolved]
        _persist_watched_dirs(dirs)
        try:
            _restart_watcher(dirs)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Watcher reload failed: {exc}") from exc
        return {"dirs": dirs}

    @app.post("/configure")
    async def configure(req: dict = Body(default={})):
        env_file = _config.RECALL_HOME / ".env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = env_file.read_text().splitlines() if env_file.exists() else []

        def _set(key: str, val: str) -> None:
            nonlocal lines
            lines = [line for line in lines if not line.startswith(f"{key}=")]
            lines.append(f"{key}={val}")

        for src_key, env_key in (
            ("gemini_api_key", "GEMINI_API_KEY"),
            ("canvas_api_key", "CANVAS_API_KEY"),
            ("canvas_base_url", "CANVAS_BASE_URL"),
            ("schoology_consumer_key", "SCHOOLOGY_CONSUMER_KEY"),
            ("schoology_consumer_secret", "SCHOOLOGY_CONSUMER_SECRET"),
        ):
            value = req.get(src_key)
            if value:
                _set(env_key, str(value))
                os.environ[env_key] = str(value)
        env_file.write_text("\n".join(lines) + "\n")
        return {"ok": True}

    @app.get("/model-status")
    async def model_status():
        from . import model_manager

        return model_manager.model_status()

    @app.get("/index-status")
    async def index_status():
        from . import store

        return store.index_status()

    @app.get("/migration-status")
    async def migration_status():
        from . import migration

        return migration.status()

    @app.post("/rebuild-index")
    async def rebuild_index():
        from . import store

        return store.rebuild_hot_index()

    return app


def _configure_logging() -> None:
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return

    _config.ensure_runtime_dirs()
    log_path = _config.LOG_DIR / "daemon.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _uds_client(timeout: float = 2.0) -> httpx.Client:
    transport = httpx.HTTPTransport(uds=str(_config.SOCKET_PATH))
    return httpx.Client(transport=transport, base_url=_config.RECALL_SOCKET_BASE_URL, timeout=timeout)


def _poll_health_socket(timeout_s: float) -> bool:
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        try:
            with _uds_client(timeout=2.0) as client:
                resp = client.get("/health")
                if resp.is_success:
                    return True
        except Exception:
            pass
        _time.sleep(0.3)
    return False


def _poll_health_http(host: str, port: int, timeout_s: float) -> bool:
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        try:
            resp = httpx.get(f"http://{host}:{port}/health", timeout=2.0)
            if resp.is_success:
                return True
        except Exception:
            pass
        _time.sleep(0.3)
    return False


def _build_compat_proxy_app():
    from fastapi import FastAPI, Request, Response

    app = FastAPI(title="Recall Compat Proxy", version="1.0.0")

    async def _forward(path: str, request: Request) -> Response:
        upstream = f"/{path}" if path else "/"
        body = await request.body()
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        transport = httpx.AsyncHTTPTransport(uds=str(_config.SOCKET_PATH))
        async with httpx.AsyncClient(
            transport=transport,
            base_url=_config.RECALL_SOCKET_BASE_URL,
            timeout=60.0,
        ) as client:
            upstream_resp = await client.request(
                request.method,
                upstream,
                params=request.query_params,
                content=body,
                headers=headers,
            )
        response_headers = {
            key: value
            for key, value in upstream_resp.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding", "connection"}
        }
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            headers=response_headers,
            media_type=upstream_resp.headers.get("content-type"),
        )

    @app.api_route(
        "/",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy_root(request: Request) -> Response:
        return await _forward("", request)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy_path(path: str, request: Request) -> Response:
        return await _forward(path, request)

    return app


def _run_server() -> None:
    import uvicorn

    _config.ensure_runtime_dirs()
    _configure_logging()

    if _config.SOCKET_PATH.exists():
        try:
            _config.SOCKET_PATH.unlink()
        except Exception:
            pass

    _config.PID_FILE.write_text(str(os.getpid()))

    def _cleanup(signum=None, frame=None):
        try:
            _config.PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            _config.SOCKET_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    app = _build_app()
    compat_thread = None
    if _config.RECALL_ENABLE_COMPAT_HTTP:
        compat_app = _build_compat_proxy_app()
        compat_thread = threading.Thread(
            target=uvicorn.run,
            kwargs={
                "app": compat_app,
                "host": _config.DAEMON_HOST,
                "port": _config.DAEMON_PORT,
                "log_level": "warning",
                "access_log": False,
            },
            daemon=True,
            name="recall-compat-http",
        )
        compat_thread.start()

    try:
        uvicorn.run(
            app,
            uds=str(_config.SOCKET_PATH),
            log_level="warning",
            access_log=False,
        )
    finally:
        _cleanup()


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_pid() -> int | None:
    try:
        return int(_config.PID_FILE.read_text().strip())
    except Exception:
        return None


def cmd_start() -> None:
    _config.ensure_runtime_dirs()
    pid = _read_pid()
    if pid and _pid_running(pid):
        if _poll_health_socket(timeout_s=3.0):
            print(f"Daemon already running (pid {pid})")
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except PermissionError:
            print(f"Daemon process {pid} is unhealthy and cannot be terminated (permission denied).")
            sys.exit(1)
        except ProcessLookupError:
            pass
        for _ in range(20):
            _time.sleep(0.2)
            if not _pid_running(pid):
                break
        if _pid_running(pid):
            print(f"Daemon process {pid} is unhealthy and could not be stopped.")
            print("Stop it manually and retry.")
            sys.exit(1)
        _config.PID_FILE.unlink(missing_ok=True)
    elif pid:
        _config.PID_FILE.unlink(missing_ok=True)

    log_path = _config.LOG_DIR / "daemon.log"
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

    if _poll_health_socket(timeout_s=20.0):
        print(f"Daemon started (pid {proc.pid})")
    else:
        print(f"Daemon process spawned (pid {proc.pid}) but did not respond within 20s.")
        print(f"Check logs: {log_path}")


def cmd_stop() -> None:
    pid = _read_pid()
    if not pid:
        print("Daemon not running (no PID file)")
        return
    if not _pid_running(pid):
        _config.PID_FILE.unlink(missing_ok=True)
        print("Daemon not running (stale PID cleaned up)")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(10):
        _time.sleep(0.3)
        if not _pid_running(pid):
            break
    print(f"Daemon stopped (pid {pid})")


def cmd_status() -> None:
    pid = _read_pid()
    if not pid or not _pid_running(pid):
        log_path = _config.LOG_DIR / "daemon.log"
        print("Daemon: stopped")
        if log_path.exists():
            lines = log_path.read_text().splitlines()
            tail = lines[-5:] if len(lines) >= 5 else lines
            if any(tail):
                print(f"Last log lines ({log_path}):")
                for line in tail:
                    print(f"  {line}")
        return
    try:
        with _uds_client(timeout=5.0) as client:
            stats = client.get("/stats").json()
            ready = client.get("/ready").json()
        print(f"Daemon: running (pid {pid}, {stats.get('count', '?')} documents indexed)")
        print(f"Migration: {ready.get('migration', 'unknown')}")
    except Exception:
        print(f"Daemon: running (pid {pid}, health check failed)")


def cmd_sync(source: str | None = None) -> None:
    payload = {"source": source} if source else {}
    try:
        with _uds_client(timeout=120.0) as client:
            resp = client.post("/sync", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        print(f"Sync failed: {exc}")
        print("Start daemon first: vef-daemon start")
        sys.exit(1)
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
