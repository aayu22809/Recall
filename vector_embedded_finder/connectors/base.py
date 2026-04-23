"""Abstract base class for all Recall source connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable


class BaseConnector(ABC):
    """
    Every connector must implement these methods.

    Lifecycle:
        1. authenticate() — OAuth flow or API key validation
        2. sync(since, progress_cb) — bulk import; returns list of ingested doc dicts
        3. watch(callback) — optional real-time push; call callback(doc) on new items
    """

    source_id: str = ""       # e.g. "gmail", "gcal", "canvas"
    display_name: str = ""    # shown in UI

    @abstractmethod
    def authenticate(self) -> None:
        """Perform OAuth / API key auth.  Store tokens to credentials file."""
        ...

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Return True if valid credentials exist (without making a network call)."""
        ...

    @abstractmethod
    def sync(
        self,
        since: datetime | None = None,
        progress_cb: Callable[[int, int, dict], None] | None = None,
        should_pause: Callable[[], bool] | None = None,
        budget_s: float | None = None,
    ) -> list[dict]:
        """
        Pull items from the source and embed them into ChromaDB.

        Args:
            since: Only import items newer than this timestamp.
                   If None, do a full initial sync.
            progress_cb: Called with (current, total, result_dict) for each item.
            should_pause: Optional callback allowing connector to cooperatively pause.
            budget_s: Optional wall-clock sync budget in seconds.

        Returns:
            List of result dicts with at least {"status": "embedded"|"skipped"|"error"}.
        """
        ...

    def watch(self, callback: Callable[[dict], None]) -> None:
        """
        Subscribe to real-time updates.  Optional — not all sources support push.
        Default implementation does nothing.
        """
