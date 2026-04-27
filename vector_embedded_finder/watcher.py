"""Filesystem watcher with debounce, dedupe, and delete handling."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil

from . import config, utils

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0
MAX_QUEUE_SIZE = 500


@dataclass
class _PendingEvent:
    op: str
    path: Path
    ready_at: float


class _FileEventHandler:
    def __init__(self, event_queue: "queue.Queue[_PendingEvent]"):
        self._queue = event_queue
        self._pending: dict[str, _PendingEvent] = {}
        self._lock = threading.Lock()

    def on_created(self, event) -> None:
        self._handle("upsert", event)

    def on_modified(self, event) -> None:
        self._handle("upsert", event)

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        self._enqueue("delete", Path(event.src_path))
        self._enqueue("upsert", Path(event.dest_path))

    def on_deleted(self, event) -> None:
        self._handle("delete", event)

    def _handle(self, op: str, event) -> None:
        if event.is_directory:
            return
        self._enqueue(op, Path(event.src_path))

    def _enqueue(self, op: str, path: Path) -> None:
        if path.name.startswith("._"):
            return
        if op == "upsert" and not utils.is_supported(path):
            return
        ready_at = time.time() + DEBOUNCE_SECONDS
        key = f"{op}:{path}"
        with self._lock:
            self._pending[key] = _PendingEvent(op=op, path=path, ready_at=ready_at)

    def flush_ready(self) -> None:
        now = time.time()
        ready: list[_PendingEvent] = []
        with self._lock:
            for key, pending in list(self._pending.items()):
                if pending.ready_at <= now:
                    ready.append(pending)
                    del self._pending[key]
        for pending in ready:
            try:
                self._queue.put_nowait(pending)
            except queue.Full:
                logger.debug("Watcher queue full, dropping event for %s", pending.path)


class FileWatcher:
    def __init__(self):
        self._observer = None
        self._handler: _FileEventHandler | None = None
        self._queue: queue.Queue[_PendingEvent] = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._worker: threading.Thread | None = None
        self._flusher: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(
        self,
        directories: list[Path],
        callback: Callable[[Path], None],
        delete_callback: Callable[[Path], None] | None = None,
    ) -> None:
        from watchdog.events import FileSystemEventHandler
        try:
            from watchdog.observers.fsevents import FSEventsObserver as Observer
        except Exception:
            from watchdog.observers import Observer

        self._stopped.clear()
        self._handler = _FileEventHandler(self._queue)

        class WatchdogShim(FileSystemEventHandler):
            def __init__(self, inner: _FileEventHandler):
                self._inner = inner

            def on_created(self, event):
                self._inner.on_created(event)

            def on_modified(self, event):
                self._inner.on_modified(event)

            def on_moved(self, event):
                self._inner.on_moved(event)

            def on_deleted(self, event):
                self._inner.on_deleted(event)

        self._observer = Observer()
        shim = WatchdogShim(self._handler)
        for directory in directories:
            resolved = directory.expanduser().resolve()
            if resolved.exists():
                self._observer.schedule(shim, str(resolved), recursive=True)
                logger.info("Watching directory: %s", resolved)
        self._observer.start()

        def _flush_loop() -> None:
            while not self._stopped.is_set():
                if self._handler is not None:
                    self._handler.flush_ready()
                time.sleep(0.5)

        def _worker_loop() -> None:
            while not self._stopped.is_set():
                try:
                    pending = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                while psutil.cpu_percent(interval=0.2) > config.CPU_GUARD_PERCENT:
                    time.sleep(1.0)
                try:
                    if pending.op == "delete":
                        if delete_callback is not None:
                            delete_callback(pending.path)
                    elif pending.path.exists():
                        callback(pending.path)
                except Exception as exc:
                    logger.error("Watcher callback error for %s: %s", pending.path, exc)
                finally:
                    self._queue.task_done()

        self._flusher = threading.Thread(target=_flush_loop, daemon=True, name="recall-watch-flusher")
        self._worker = threading.Thread(target=_worker_loop, daemon=True, name="recall-watch-worker")
        self._flusher.start()
        self._worker.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        if self._worker:
            self._worker.join(timeout=1.0)
            self._worker = None
        if self._flusher:
            self._flusher.join(timeout=1.0)
            self._flusher = None

    def is_alive(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def queued(self) -> int:
        return self._queue.qsize()
