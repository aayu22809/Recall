"""Schoology connector — OAuth1 auth, indexes assignments, announcements,
course materials, and discussion posts."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import config, embedder, store, utils
from .base import BaseConnector

logger = logging.getLogger(__name__)

SCHOOLOGY_API_BASE = "https://api.schoology.com/v1"


def _creds_path() -> Path:
    return config.SCHOOLOGY_CREDENTIALS_FILE


def _load_creds() -> dict | None:
    p = _creds_path()
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_creds(consumer_key: str, consumer_secret: str, base_url: str) -> None:
    config.ensure_vef_dirs()
    _creds_path().write_text(json.dumps({
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
        "base_url": base_url.rstrip("/"),
    }, indent=2))


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class SchoologyConnector(BaseConnector):
    source_id = "schoology"
    display_name = "Schoology"

    def authenticate(self) -> None:
        base_url = input(
            "Schoology base URL (e.g. https://api.schoology.com/v1 or your district URL): "
        ).strip().rstrip("/")
        consumer_key = input("Schoology OAuth1 consumer key: ").strip()
        consumer_secret = input("Schoology OAuth1 consumer secret: ").strip()
        if not consumer_key or not consumer_secret:
            raise ValueError("Consumer key and secret are required")
        _save_creds(consumer_key, consumer_secret, base_url)
        logger.info("Schoology credentials saved")

    def set_credentials(self, consumer_key: str, consumer_secret: str, base_url: str) -> None:
        """Programmatic alternative to authenticate() for wizard use."""
        _save_creds(consumer_key, consumer_secret, base_url)

    def is_authenticated(self) -> bool:
        creds = _load_creds()
        return (
            creds is not None
            and bool(creds.get("consumer_key"))
            and bool(creds.get("consumer_secret"))
        )

    def _make_session(self, creds: dict):
        from requests_oauthlib import OAuth1Session
        return OAuth1Session(
            client_key=creds["consumer_key"],
            client_secret=creds["consumer_secret"],
        )

    def _paginate(self, session, url: str, key: str) -> list[dict]:
        """Fetch all Schoology pages for a list endpoint."""
        items: list[dict] = []
        start = 0
        limit = 200
        while True:
            resp = session.get(url, params={"start": start, "limit": limit})
            if resp.status_code in (404, 403):
                break
            resp.raise_for_status()
            data = resp.json()
            batch = data.get(key, [])
            items.extend(batch)
            total_reported = data.get("total", len(batch))
            start += limit
            if start >= total_reported or not batch:
                break
        return items

    def sync(
        self,
        since: datetime | None = None,
        progress_cb: Callable[[int, int, dict], None] | None = None,
        should_pause: Callable[[], bool] | None = None,
        budget_s: float | None = None,
    ) -> list[dict]:
        creds = _load_creds()
        if not creds:
            raise RuntimeError("Not authenticated — call authenticate() first")

        base_url = creds.get("base_url", SCHOOLOGY_API_BASE)
        session = self._make_session(creds)
        session.headers.update({"Accept": "application/json"})

        # Fetch sections (courses)
        sections = self._paginate(session, f"{base_url}/sections", "section")
        logger.info("Schoology: found %d sections", len(sections))

        all_items: list[tuple[str, dict, str]] = []

        for section in sections:
            sid = section["id"]
            sname = section.get("course_title", section.get("section_title", str(sid)))

            for a in self._paginate(session, f"{base_url}/sections/{sid}/assignments", "assignment"):
                all_items.append(("assignment", a, sname))

            for u in self._paginate(session, f"{base_url}/sections/{sid}/updates", "update"):
                all_items.append(("announcement", u, sname))

            for d in self._paginate(session, f"{base_url}/sections/{sid}/discussions", "discussion"):
                all_items.append(("discussion", d, sname))

            for doc in self._paginate(session, f"{base_url}/sections/{sid}/documents", "document"):
                all_items.append(("document", doc, sname))

        total = len(all_items)
        logger.info("Schoology: %d items to index", total)
        results: list[dict] = []

        for i, (item_type, item, sname) in enumerate(all_items, 1):
            try:
                result = self._ingest_item(item_type, item, sname)
                results.append(result)
            except Exception as e:
                result = {"status": "error", "type": item_type, "error": str(e)}
                results.append(result)

            if progress_cb:
                progress_cb(i, total, result)

        return results

    def _ingest_item(self, item_type: str, item: dict, section_name: str) -> dict:
        item_id = str(item.get("id", ""))
        doc_id = utils.text_hash(f"schoology:{item_type}:{item_id}")

        if store.exists(doc_id):
            return {"status": "skipped", "id": doc_id}

        if item_type == "assignment":
            title = item.get("title", "Untitled assignment")
            due = item.get("due", "")
            body = _strip_html(item.get("description") or "")[:1000]
            text = f"Assignment: {title}"
            if due:
                text += f" — due {due}"
            if body:
                text += f" — {body}"
            url = item.get("web_url", "")
            extra = {"item_type": "assignment", "due_at": due, "points_possible": str(item.get("max_points", ""))}

        elif item_type == "announcement":
            body = _strip_html(item.get("body") or "")[:1000]
            text = f"Announcement in {section_name}"
            if body:
                text += f": {body}"
            url = ""
            extra = {"item_type": "announcement", "due_at": "", "points_possible": ""}

        elif item_type == "discussion":
            title = item.get("title", "Discussion")
            body = _strip_html(item.get("body") or "")[:500]
            text = f"Discussion: {title}"
            if body:
                text += f" — {body}"
            url = ""
            extra = {"item_type": "discussion", "due_at": "", "points_possible": ""}

        elif item_type == "document":
            title = item.get("title", "Document")
            text = f"Document in {section_name}: {title}"
            url = item.get("web_url", "")
            extra = {"item_type": "file", "due_at": "", "points_possible": ""}

        else:
            return {"status": "skipped", "reason": f"unknown type: {item_type}"}

        embedding = embedder.embed_text(text[:8000])

        metadata = {
            "file_path": f"schoology://{item_type}/{item_id}",
            "file_name": item.get("title", item.get("id", "")),
            "file_type": "text/plain",
            "media_category": "lms_item",
            "timestamp": utils.now_iso(),
            "source": self.source_id,
            "description": text[:500],
            "file_size": len(text.encode()),
            "course_name": section_name,
            "course_id": str(item.get("section_id", "")),
            "url": url,
            **extra,
        }

        store.add(doc_id, embedding, metadata, document=text[:500])
        return {"status": "embedded", "id": doc_id, "item_type": item_type}
