"""Notion connector — API key auth, indexes pages as text."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx

from .. import config, embedder, store, utils
from .base import BaseConnector

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
_MAX_TEXT_CHARS = 8000


def _creds_path() -> Path:
    return config.NOTION_CREDENTIALS_FILE


def _load_api_key() -> str | None:
    p = _creds_path()
    if p.exists():
        data = json.loads(p.read_text())
        return data.get("api_key")
    return None


def _save_api_key(key: str) -> None:
    config.ensure_vef_dirs()
    _creds_path().write_text(json.dumps({"api_key": key}, indent=2))


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _plain_from_rich_text(rich_text: list[dict]) -> str:
    return "".join(str(part.get("plain_text", "")) for part in rich_text if isinstance(part, dict)).strip()


class NotionConnector(BaseConnector):
    source_id = "notion"
    display_name = "Notion"

    def authenticate(self) -> None:
        key = input("Paste your Notion integration token: ").strip()
        if not key:
            raise ValueError("Notion API key cannot be empty")
        _save_api_key(key)
        logger.info("Notion API key saved")

    def set_api_key(self, key: str) -> None:
        _save_api_key(key.strip())

    def is_authenticated(self) -> bool:
        key = _load_api_key()
        return bool(key)

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

        pages: list[dict] = []
        has_more = True
        cursor: str | None = None

        with httpx.Client(timeout=20.0) as client:
            while has_more:
                payload: dict = {"page_size": 100, "filter": {"value": "page", "property": "object"}}
                if cursor:
                    payload["start_cursor"] = cursor
                resp = client.post(f"{NOTION_API_BASE}/search", headers=_headers(api_key), json=payload)
                if resp.status_code in (401, 403):
                    raise RuntimeError("Notion authentication failed (invalid or expired API key)")
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("results", [])
                if since is not None:
                    batch = [p for p in batch if self._is_newer_than(p, since)]
                pages.extend(batch)
                has_more = bool(data.get("has_more"))
                cursor = data.get("next_cursor")

            total = len(pages)
            logger.info("Notion: found %d pages to sync", total)
            results: list[dict] = []
            for i, page in enumerate(pages, 1):
                try:
                    result = self._ingest_page(client, api_key, page)
                except Exception as exc:
                    result = {"status": "error", "id": page.get("id", ""), "error": str(exc)}
                results.append(result)
                if progress_cb:
                    progress_cb(i, total, result)
            return results

    def _is_newer_than(self, page: dict, since: datetime) -> bool:
        edited = str(page.get("last_edited_time", ""))
        if not edited:
            return True
        try:
            edited_dt = datetime.fromisoformat(edited.replace("Z", "+00:00"))
            if since.tzinfo is None:
                since = since.replace(tzinfo=edited_dt.tzinfo)
            return edited_dt >= since
        except Exception:
            return True

    def _ingest_page(self, client: httpx.Client, api_key: str, page: dict) -> dict:
        page_id = str(page.get("id", "")).replace("-", "")
        if not page_id:
            return {"status": "skipped", "reason": "missing page id"}

        doc_id = utils.text_hash(f"notion:{page_id}")
        if store.exists(doc_id):
            return {"status": "skipped", "id": doc_id, "page_id": page_id}

        title = self._extract_title(page)
        body = self._fetch_page_text(client, api_key, page_id)
        if not body and not title:
            return {"status": "skipped", "id": doc_id, "reason": "empty page"}

        text = f"{title}\n\n{body}".strip()
        embedding = embedder.embed_text(text[:_MAX_TEXT_CHARS])
        metadata = {
            "file_path": f"notion://page/{page_id}",
            "file_name": title or "Untitled Notion page",
            "file_type": "text/plain",
            "media_category": "document",
            "timestamp": str(page.get("last_edited_time", "")) or utils.now_iso(),
            "source": self.source_id,
            "description": text[:500],
            "file_size": len(text.encode()),
            "page_id": page_id,
            "url": str(page.get("url", "")),
        }
        store.add(doc_id, embedding, metadata, document=text[:5000])
        return {"status": "embedded", "id": doc_id, "page_id": page_id}

    def _extract_title(self, page: dict) -> str:
        properties = page.get("properties", {})
        if not isinstance(properties, dict):
            return ""
        for prop in properties.values():
            if not isinstance(prop, dict):
                continue
            if prop.get("type") == "title":
                return _plain_from_rich_text(prop.get("title", []))
        return ""

    def _fetch_page_text(self, client: httpx.Client, api_key: str, page_id: str) -> str:
        text_parts: list[str] = []
        cursor: str | None = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            resp = client.get(
                f"{NOTION_API_BASE}/blocks/{page_id}/children",
                headers=_headers(api_key),
                params=params,
            )
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            data = resp.json()
            for block in data.get("results", []):
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                block_data = block.get(block_type, {}) if isinstance(block_type, str) else {}
                rich_text = block_data.get("rich_text", []) if isinstance(block_data, dict) else []
                if rich_text:
                    text_parts.append(_plain_from_rich_text(rich_text))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return "\n".join(part for part in text_parts if part).strip()


def _main() -> None:
    import sys

    args = sys.argv[1:]
    connector = NotionConnector()

    if args == ["authenticate"]:
        connector.authenticate()
        print("Notion authentication complete.")
        return
    if args == ["sync"]:
        results = connector.sync(since=None)
        embedded = sum(1 for r in results if r.get("status") == "embedded")
        print(f"Notion sync complete: {embedded}/{len(results)} embedded.")
        return

    print("Usage: python -m vector_embedded_finder.connectors.notion authenticate|sync")
    sys.exit(1)


if __name__ == "__main__":
    _main()
