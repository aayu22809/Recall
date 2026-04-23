"""Canvas LMS connector — API token auth, indexes assignments, announcements,
pages, files, discussions, and submission feedback."""

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


def _creds_path() -> Path:
    return config.CANVAS_CREDENTIALS_FILE


def _load_creds() -> dict | None:
    p = _creds_path()
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_creds(token: str, base_url: str) -> None:
    config.ensure_vef_dirs()
    _creds_path().write_text(json.dumps({"token": token, "base_url": base_url}, indent=2))


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class CanvasConnector(BaseConnector):
    source_id = "canvas"
    display_name = "Canvas LMS"

    def authenticate(self) -> None:
        base_url = input("Canvas base URL (e.g. https://canvas.instructure.com): ").strip().rstrip("/")
        token = input("Canvas API token: ").strip()
        if not base_url or not token:
            raise ValueError("Base URL and API token are required")
        _save_creds(token, base_url)
        logger.info("Canvas credentials saved")

    def set_credentials(self, token: str, base_url: str) -> None:
        """Programmatic alternative to authenticate() for wizard use."""
        _save_creds(token, base_url.rstrip("/"))

    def is_authenticated(self) -> bool:
        creds = _load_creds()
        return creds is not None and bool(creds.get("token")) and bool(creds.get("base_url"))

    def _paginate(self, session, url: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages of a Canvas API list endpoint."""
        items: list[dict] = []
        params = params or {}
        params.setdefault("per_page", 100)
        while url:
            resp = session.get(url, params=params)
            if resp.status_code == 404:
                return items
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                items.extend(data)
            elif isinstance(data, dict):
                # some endpoints wrap in a key
                for v in data.values():
                    if isinstance(v, list):
                        items.extend(v)
                        break

            # Follow Link header pagination
            link = resp.headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    match = re.search(r"<([^>]+)>", part)
                    if match:
                        next_url = match.group(1)
                        break
            url = next_url
            params = {}  # already in URL for subsequent pages

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

        import httpx
        base_url = creds["base_url"]
        token = creds["token"]

        session = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            base_url=base_url,
            timeout=30.0,
            follow_redirects=True,
        )

        # Fetch active courses
        courses = self._paginate(session, "/api/v1/courses", {"enrollment_state": "active", "include[]": "term"})
        logger.info("Canvas: found %d active courses", len(courses))

        all_items: list[tuple[str, dict, str]] = []  # (type, item, course_name)

        for course in courses:
            cid = course["id"]
            cname = course.get("name", str(cid))

            # Assignments
            for a in self._paginate(session, f"/api/v1/courses/{cid}/assignments"):
                all_items.append(("assignment", a, cname))

            # Announcements
            for ann in self._paginate(session, f"/api/v1/courses/{cid}/discussion_topics",
                                       {"only_announcements": "true"}):
                all_items.append(("announcement", ann, cname))

            # Course pages
            for page in self._paginate(session, f"/api/v1/courses/{cid}/pages"):
                all_items.append(("page", page, cname))

            # Files
            for f in self._paginate(session, f"/api/v1/courses/{cid}/files"):
                all_items.append(("file", f, cname))

            # Discussion posts
            for disc in self._paginate(session, f"/api/v1/courses/{cid}/discussion_topics"):
                all_items.append(("discussion", disc, cname))

        total = len(all_items)
        logger.info("Canvas: %d items to index", total)
        results: list[dict] = []

        for i, (item_type, item, cname) in enumerate(all_items, 1):
            try:
                result = self._ingest_item(session, item_type, item, cname, base_url)
                results.append(result)
            except Exception as e:
                result = {"status": "error", "type": item_type, "error": str(e)}
                results.append(result)
                logger.debug("Canvas ingest error (%s %s): %s", item_type, item.get("id"), e)

            if progress_cb:
                progress_cb(i, total, result)

        session.close()
        return results

    def _ingest_item(
        self,
        session,
        item_type: str,
        item: dict,
        course_name: str,
        base_url: str,
    ) -> dict:
        item_id = str(item.get("id", ""))
        course_id = str(item.get("course_id", ""))
        doc_id = utils.text_hash(f"canvas:{course_id}:{item_type}:{item_id}")

        if store.exists(doc_id):
            return {"status": "skipped", "id": doc_id}

        if item_type == "assignment":
            title = item.get("name", "Untitled assignment")
            due_at = item.get("due_at", "")
            body = _strip_html(item.get("description") or "")
            text = f"Assignment: {title}"
            if due_at:
                text += f" — due {due_at}"
            if body:
                text += f" — {body[:1000]}"
            url = item.get("html_url", f"{base_url}/courses/{course_id}/assignments/{item_id}")
            media_cat = "lms_item"
            extra = {"item_type": "assignment", "due_at": due_at or "",
                     "points_possible": str(item.get("points_possible", ""))}

        elif item_type == "announcement":
            title = item.get("title", "Untitled announcement")
            author = (item.get("author") or {}).get("display_name", "")
            body = _strip_html(item.get("message") or "")
            text = f"Announcement from {author} in {course_name}: {title}"
            if body:
                text += f" — {body[:1000]}"
            url = item.get("html_url", f"{base_url}/courses/{course_id}/discussion_topics/{item_id}")
            media_cat = "lms_item"
            extra = {"item_type": "announcement", "due_at": "", "points_possible": ""}

        elif item_type == "page":
            title = item.get("title", "Untitled page")
            # Fetch full page body
            body = ""
            try:
                page_url_slug = item.get("page_id") or item.get("url", "")
                page_resp = session.get(
                    f"/api/v1/courses/{course_id}/pages/{page_url_slug}"
                )
                if page_resp.is_success:
                    body = _strip_html(page_resp.json().get("body") or "")[:2000]
            except Exception:
                pass
            text = f"{title}: {body}" if body else title
            url = item.get("html_url", f"{base_url}/courses/{course_id}/pages/{item.get('url', item_id)}")
            media_cat = "lms_item"
            extra = {"item_type": "page", "due_at": "", "points_possible": ""}

        elif item_type == "file":
            title = item.get("display_name", item.get("filename", "file"))
            url = item.get("url", "")
            mime = item.get("content-type", "")
            text = f"File in {course_name}: {title}"
            # For PDF/DOCX try to download and extract text
            if mime in ("application/pdf",) or title.lower().endswith(".pdf"):
                text = self._extract_file_text(session, url, title, course_name) or text
            media_cat = "lms_item"
            extra = {"item_type": "file", "due_at": "", "points_possible": ""}
            url = item.get("url", f"{base_url}/courses/{course_id}/files/{item_id}")

        elif item_type == "discussion":
            title = item.get("title", "Untitled discussion")
            body = _strip_html(item.get("message") or "")[:500]
            text = f"Discussion: {title}"
            if body:
                text += f" — {body}"
            url = item.get("html_url", f"{base_url}/courses/{course_id}/discussion_topics/{item_id}")
            media_cat = "lms_item"
            extra = {"item_type": "discussion", "due_at": "", "points_possible": ""}

        else:
            return {"status": "skipped", "reason": f"unknown type: {item_type}"}

        embedding = embedder.embed_text(text[:8000])

        metadata = {
            "file_path": f"canvas://{course_id}/{item_type}/{item_id}",
            "file_name": title,
            "file_type": "text/plain",
            "media_category": media_cat,
            "timestamp": utils.now_iso(),
            "source": self.source_id,
            "description": text[:500],
            "file_size": len(text.encode()),
            "course_name": course_name,
            "course_id": course_id,
            "url": url,
            **extra,
        }

        store.add(doc_id, embedding, metadata, document=text[:500])
        return {"status": "embedded", "id": doc_id, "item_type": item_type}

    def _extract_file_text(
        self, session, url: str, title: str, course_name: str
    ) -> str | None:
        """Download a PDF and extract text (best-effort)."""
        if not url:
            return None
        try:
            import io
            import pypdf
            resp = session.get(url, timeout=30.0)
            resp.raise_for_status()
            reader = pypdf.PdfReader(io.BytesIO(resp.content))
            text_parts = []
            for page in reader.pages[:20]:
                text_parts.append(page.extract_text() or "")
            text = " ".join(text_parts).strip()[:4000]
            if text:
                return f"File in {course_name}: {title}\n{text}"
        except Exception as e:
            logger.debug("File text extraction failed (%s): %s", title, e)
        return None


def _main() -> None:
    import sys

    args = sys.argv[1:]
    connector = CanvasConnector()

    if args == ["authenticate"]:
        connector.authenticate()
        print("Canvas authentication complete.")
        return

    if args == ["sync"]:
        results = connector.sync(since=None)
        embedded = sum(1 for r in results if r.get("status") == "embedded")
        print(f"Canvas sync complete: {embedded}/{len(results)} embedded.")
        return

    print("Usage: python -m vector_embedded_finder.connectors.canvas authenticate|sync")
    sys.exit(1)


if __name__ == "__main__":
    _main()
