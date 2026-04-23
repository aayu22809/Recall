"""Filesystem watcher — indexes new/modified files within ~10 seconds.

Uses the watchdog library to monitor configured directories.  A 2-second
debounce prevents re-indexing partially-written files.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

import psutil

from . import config, utils

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0


class _DebounceTimer:
    """Fires callback once after no new events for `delay` seconds."""

    def __init__(self, delay: float, callback: Callable[[Path], None], path: Path):
        self._delay = delay
        self._callback = callback
        self._path = path
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def touch(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        try:
            self._callback(self._path)
        except Exception as e:
            logger.error("watcher callback error for %s: %s", self._path, e)


class _FileEventHandler:
    """Watchdog event handler that debounces and queues ingest calls."""

    def __init__(self, callback: Callable[[Path], None]):
        self._callback = callback
        self._timers: dict[str, _DebounceTimer] = {}
        self._lock = threading.Lock()

    # watchdog calls these methods
    def on_created(self, event) -> None:
        self._handle(event)

    def on_modified(self, event) -> None:
        self._handle(event)

    def on_moved(self, event) -> None:
        # Index the destination path (file was renamed/moved to a new location)
        if event.is_directory:
            return
        dest = Path(event.dest_path)
        if dest.name.startswith("._") or not utils.is_supported(dest):
            return
        key = str(dest)
        with self._lock:
            if key not in self._timers:
                self._timers[key] = _DebounceTimer(DEBOUNCE_SECONDS, self._on_ready, dest)
            self._timers[key].touch()

    def _handle(self, event) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name.startswith("._"):
            return
        if not utils.is_supported(path):
            return

        key = str(path)
        with self._lock:
            if key not in self._timers:
                self._timers[key] = _DebounceTimer(DEBOUNCE_SECONDS, self._on_ready, path)
            self._timers[key].touch()

    def _on_ready(self, path: Path) -> None:
        # CPU guard before ingesting
        while psutil.cpu_percent(interval=0.5) > config.CPU_GUARD_PERCENT:
            logger.debug("CPU busy, deferring ingest of %s by 5s", path)
            time.sleep(5)
        self._callback(path)


class FileWatcher:
    """Start / stop watching a list of directories."""

    def __init__(self):
        self._observer = None
        self._handler: _FileEventHandler | None = None

    def start(
        self,
        directories: list[Path],
        callback: Callable[[Path], None],
    ) -> None:
        """Begin watching `directories`. `callback(path)` called for each new/modified file."""
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        self._handler = _FileEventHandler(callback)

        # Wrap our handler in a watchdog-compatible shim
        class WatchdogShim(FileSystemEventHandler):
            def __init__(self, inner: _FileEventHandler):
                self._inner = inner

            def on_created(self, event):
                self._inner.on_created(event)

            def on_modified(self, event):
                self._inner.on_modified(event)

            def on_moved(self, event):
                self._inner.on_moved(event)

        shim = WatchdogShim(self._handler)

        self._observer = Observer()
        for d in directories:
            expanded = d.expanduser().resolve()
            if expanded.exists():
                self._observer.schedule(shim, str(expanded), recursive=True)
                logger.info("Watching directory: %s", expanded)
            else:
                logger.warning("Watch directory does not exist, skipping: %s", expanded)

        self._observer.start()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    def is_alive(self) -> bool:
        return self._observer is not None and self._observer.is_alive()
