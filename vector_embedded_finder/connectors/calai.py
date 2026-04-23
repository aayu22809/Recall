"""cal.ai (Cal.com) connector — API key auth, indexes bookings."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import config, embedder, store, utils
from .base import BaseConnector

logger = logging.getLogger(__name__)

CALAI_API_BASE = "https://api.cal.com/v2"


def _creds_path() -> Path:
    return config.CALAI_CREDENTIALS_FILE


def _load_api_key() -> str | None:
    p = _creds_path()
    if p.exists():
        data = json.loads(p.read_text())
        return data.get("api_key")
    return None


def _save_api_key(key: str) -> None:
    config.ensure_vef_dirs()
    _creds_path().write_text(json.dumps({"api_key": key}, indent=2))


class CalAIConnector(BaseConnector):
    source_id = "calai"
    display_name = "cal.ai"

    def authenticate(self) -> None:
        """Prompt for API key and validate it."""
        key = input("Paste your cal.ai API key: ").strip()
        if not key:
            raise ValueError("API key cannot be empty")
        _save_api_key(key)
        logger.info("cal.ai API key saved")

    def set_api_key(self, key: str) -> None:
        """Programmatic alternative to authenticate() for wizard use."""
        _save_api_key(key)

    def is_authenticated(self) -> bool:
        return _load_api_key() is not None

    def sync(
        self,
        since: datetime | None = None,
        progress_cb: Callable[[int, int, dict], None] | None = None,
        should_pause: Callable[[], bool] | None = None,
        budget_s: float | None = None,
    ) -> list[dict]:
        api_key = _load_api_key()
        if not api_key:
            raise RuntimeError("Not authenticated — call authenticate() first")

        import httpx
        bookings: list[dict] = []
        page = 1
        per_page = 100
        headers = {"Authorization": f"Bearer {api_key}"}

        while True:
            try:
                resp = httpx.get(
                    f"{CALAI_API_BASE}/bookings",
                    headers=headers,
                    params={"page": page, "perPage": per_page},
                    timeout=15.0,
                )
                if resp.status_code in (401, 403):
                    raise RuntimeError("cal.ai authentication failed (invalid or expired API key)")
                if resp.status_code == 410:
                    raise RuntimeError("cal.ai bookings endpoint is deprecated; update connector API version")
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    batch = data.get("data") or data.get("bookings") or []
                    pagination = data.get("pagination") or {}
                    has_next = bool(pagination.get("hasNextPage"))
                else:
                    batch = data if isinstance(data, list) else []
                    has_next = False
                if not batch:
                    break
                bookings.extend(batch)
                if not has_next and len(batch) < per_page:
                    break
                page += 1
            except Exception as e:
                logger.warning("cal.ai API error: %s", e)
                raise

        total = len(bookings)
        logger.info("cal.ai: found %d bookings to sync", total)
        results: list[dict] = []

        for i, booking in enumerate(bookings, 1):
            try:
                result = self._ingest_booking(booking)
                results.append(result)
            except Exception as e:
                result = {"status": "error", "error": str(e)}
                results.append(result)

            if progress_cb:
                progress_cb(i, total, result)

        return results

    def _ingest_booking(self, booking: dict) -> dict:
        booking_id = str(booking.get("id") or booking.get("uid") or "")
        if not booking_id:
            raise ValueError("cal.ai booking missing id")
        doc_id = utils.text_hash(f"calai:{booking_id}")

        if store.exists(doc_id):
            return {"status": "skipped", "id": doc_id, "booking_id": booking_id}

        event_type = booking.get("eventType") or {}
        title = (
            booking.get("title")
            or event_type.get("title")
            or event_type.get("slug")
            or str(booking.get("eventTypeId", "Meeting"))
        )
        start_time = booking.get("start") or booking.get("startTime", "")
        end_time = booking.get("end") or booking.get("endTime", "")
        desc = booking.get("description", "")

        attendees = [
            a.get("name") or a.get("email", "")
            for a in booking.get("attendees", [])
        ]
        attendees_str = ", ".join(attendees[:10]) if attendees else ""

        description = f"{title} on {start_time}"
        if attendees_str:
            description += f" with {attendees_str}"
        if end_time:
            description += f" until {end_time}"
        if desc:
            description += f": {desc[:300]}"

        embedding = embedder.embed_text(description[:8000])

        metadata = {
            "file_path": f"calai://booking/{booking_id}",
            "file_name": str(title),
            "file_type": "text/calendar",
            "media_category": "calendar_event",
            "timestamp": utils.now_iso(),
            "source": self.source_id,
            "description": description[:500],
            "file_size": len(description.encode()),
            "booking_id": booking_id,
            "booking_uid": str(booking.get("uid", "")),
            "start": start_time,
            "end": end_time,
            "attendees": json.dumps(attendees),
        }

        store.add(doc_id, embedding, metadata, document=description[:500])
        return {"status": "embedded", "id": doc_id, "booking_id": booking_id}


def _main() -> None:
    import sys

    args = sys.argv[1:]
    connector = CalAIConnector()

    if args == ["authenticate"]:
        connector.authenticate()
        print("cal.ai authentication complete.")
        return

    if args == ["sync"]:
        results = connector.sync(since=None)
        embedded = sum(1 for r in results if r.get("status") == "embedded")
        print(f"cal.ai sync complete: {embedded}/{len(results)} embedded.")
        return

    print("Usage: python -m vector_embedded_finder.connectors.calai authenticate|sync")
    sys.exit(1)


if __name__ == "__main__":
    _main()
