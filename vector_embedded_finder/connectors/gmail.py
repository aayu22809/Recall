"""Gmail connector — OAuth2 read-only, indexes email threads."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import config, embedder, store, utils
from .base import BaseConnector

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
MAX_CONCURRENT_EMBEDS = 5  # respect Gmail API quota
INITIAL_SYNC_MONTHS = 6
BATCH_SIZE = 100
THREAD_CHUNK_SIZE = 25
CURSOR_FILE = config.VEF_DIR / "gmail_cursor.json"


def _creds_path() -> Path:
    return config.GMAIL_CREDENTIALS_FILE


def _oauth_client_path() -> Path:
    canonical = config.CREDENTIALS_DIR / "gmail_oauth_client.json"
    legacy = config.CREDENTIALS_DIR / "gmail_oauth_client.json.json"
    if canonical.exists():
        return canonical
    if legacy.exists():
        try:
            legacy.replace(canonical)
            logger.info("Renamed legacy OAuth client file to %s", canonical)
            return canonical
        except OSError:
            return legacy
    return canonical


def _load_token() -> dict | None:
    p = _creds_path()
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_token(data: dict) -> None:
    config.ensure_vef_dirs()
    _creds_path().write_text(json.dumps(data, indent=2))


class GmailConnector(BaseConnector):
    source_id = "gmail"
    display_name = "Gmail"

    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        token_data = _load_token()
        if not token_data:
            raise RuntimeError("Not authenticated — call authenticate() first")

        creds = Credentials.from_authorized_user_info(token_data, SCOPES)

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(json.loads(creds.to_json()))

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def authenticate(self) -> None:
        """Open browser OAuth flow and save tokens.

        Short-circuits to success if the Tauri shell has already written a
        valid token JSON via PUT /credentials/gmail — in that path the Rust
        OAuth flow owns client_id/client_secret and the legacy
        gmail_oauth_client.json file is irrelevant.
        """
        if self.is_authenticated():
            logger.info("Gmail already authenticated (token present)")
            return

        from google_auth_oauthlib.flow import InstalledAppFlow

        client_file = _oauth_client_path()
        if not client_file.exists():
            raise FileNotFoundError(
                f"OAuth client credentials not found at {client_file}.\n"
                "Either authenticate from the Recall app (Sources panel) or "
                "download your OAuth 2.0 client JSON from Google Cloud Console "
                "and save it there."
            )

        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
        creds = flow.run_local_server(port=0)
        _save_token(json.loads(creds.to_json()))
        logger.info("Gmail authentication successful")

    def is_authenticated(self) -> bool:
        token = _load_token()
        return token is not None and bool(token.get("token") or token.get("refresh_token"))

    def sync(
        self,
        since: datetime | None = None,
        progress_cb: Callable[[int, int, dict], None] | None = None,
        should_pause: Callable[[], bool] | None = None,
        budget_s: float | None = None,
    ) -> list[dict]:
        svc = self._get_service()
        results: list[dict] = []
        started_at = time.time()

        # Build query: last N months or since timestamp
        if since:
            after_ts = int(since.timestamp())
            query = f"after:{after_ts}"
        else:
            six_months_ago = int(time.time()) - INITIAL_SYNC_MONTHS * 30 * 86400
            cursor_after = self._load_cursor_after_ts()
            if cursor_after and cursor_after > six_months_ago:
                six_months_ago = cursor_after
            query = f"after:{six_months_ago}"

        # List all thread IDs matching query
        thread_ids: list[str] = []
        page_token = None
        while True:
            kwargs: dict = {"userId": "me", "q": query, "maxResults": 500}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = svc.users().threads().list(**kwargs).execute()
            threads = resp.get("threads", [])
            thread_ids.extend(t["id"] for t in threads)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        total = len(thread_ids)
        logger.info("Gmail: found %d threads to sync", total)
        processed = 0
        for chunk_start in range(0, total, THREAD_CHUNK_SIZE):
            if budget_s is not None and (time.time() - started_at) > budget_s:
                logger.info("Gmail sync budget reached after %d/%d threads", processed, total)
                break

            while should_pause and should_pause():
                time.sleep(2)

            chunk = thread_ids[chunk_start: chunk_start + THREAD_CHUNK_SIZE]
            for tid in chunk:
                try:
                    result = self._ingest_thread(svc, tid)
                    results.append(result)
                except Exception as e:
                    result = {"status": "error", "id": tid, "error": str(e)}
                    results.append(result)
                    logger.debug("Gmail thread %s error: %s", tid, e)

                processed += 1
                if progress_cb:
                    progress_cb(processed, total, result)

                if "internal_ts" in result:
                    self._save_cursor_after_ts(result["internal_ts"])

                # Rate limit: max 5 concurrent embeds — stagger with small delay
                if processed % MAX_CONCURRENT_EMBEDS == 0:
                    time.sleep(0.2)

        if processed >= total:
            self._clear_cursor()

        return results

    def _ingest_thread(self, svc, thread_id: str) -> dict:
        doc_id = utils.text_hash(f"gmail:{thread_id}")
        if store.exists(doc_id):
            return {"status": "skipped", "id": doc_id, "thread_id": thread_id}

        thread = svc.users().threads().get(
            userId="me", id=thread_id, format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()

        messages = thread.get("messages", [])
        if not messages:
            return {"status": "skipped", "id": doc_id, "reason": "empty thread"}

        first = messages[0]
        headers = {h["name"]: h["value"] for h in first.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender = headers.get("From", "")
        date_str = headers.get("Date", "")
        snippet = first.get("snippet", "")

        # Fetch body of first message (up to 2000 chars)
        body_preview = self._get_body_preview(svc, first["id"])

        # Collect labels from all messages
        labels: set[str] = set()
        for msg in messages:
            labels.update(msg.get("labelIds", []))

        description = f"{sender} — {subject} — {date_str}: {body_preview}"
        embedding = embedder.embed_text(description[:8000])

        metadata = {
            "file_path": f"gmail://thread/{thread_id}",
            "file_name": subject,
            "file_type": "message/rfc822",
            "media_category": "email",
            "timestamp": utils.now_iso(),
            "source": self.source_id,
            "description": description[:500],
            "file_size": len(description.encode()),
            "thread_id": thread_id,
            "subject": subject,
            "from": sender,
            "date": date_str,
            "labels": json.dumps(sorted(labels)),
            "snippet": snippet,
        }

        store.add(doc_id, embedding, metadata, document=description[:500])
        internal_ts = self._thread_internal_ts(messages)
        return {"status": "embedded", "id": doc_id, "thread_id": thread_id, "internal_ts": internal_ts}

    def _thread_internal_ts(self, messages: list[dict]) -> int:
        values: list[int] = []
        for msg in messages:
            raw = msg.get("internalDate")
            if not raw:
                continue
            try:
                values.append(int(int(raw) / 1000))
            except (TypeError, ValueError):
                continue
        return max(values) if values else int(time.time())

    def _load_cursor_after_ts(self) -> int | None:
        try:
            if not CURSOR_FILE.exists():
                return None
            payload = json.loads(CURSOR_FILE.read_text())
            value = payload.get("last_internal_ts")
            if value is None:
                return None
            return int(value)
        except Exception:
            return None

    def _save_cursor_after_ts(self, internal_ts: int) -> None:
        try:
            config.ensure_vef_dirs()
            CURSOR_FILE.write_text(json.dumps({"last_internal_ts": int(internal_ts)}, indent=2))
        except Exception as exc:
            logger.debug("Could not save Gmail cursor: %s", exc)

    def _clear_cursor(self) -> None:
        try:
            CURSOR_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _get_body_preview(self, svc, message_id: str) -> str:
        """Extract plain-text body from a Gmail message (first 2000 chars)."""
        try:
            msg = svc.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
            payload = msg.get("payload", {})
            return self._extract_text(payload)[:2000]
        except Exception:
            return ""

    def _extract_text(self, payload: dict) -> str:
        import base64
        parts = payload.get("parts", [])
        if not parts:
            data = payload.get("body", {}).get("data", "")
            if data:
                try:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                except Exception:
                    return ""
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    try:
                        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                    except Exception:
                        pass
        # Recurse into multipart
        for part in parts:
            text = self._extract_text(part)
            if text:
                return text
        return ""


def _main() -> None:
    import sys

    args = sys.argv[1:]
    connector = GmailConnector()

    if args == ["authenticate"]:
        connector.authenticate()
        print("Gmail authentication complete.")
        return

    if args == ["sync"]:
        results = connector.sync(since=None)
        embedded = sum(1 for r in results if r.get("status") == "embedded")
        print(f"Gmail sync complete: {embedded}/{len(results)} embedded.")
        return

    print("Usage: python -m vector_embedded_finder.connectors.gmail authenticate|sync")
    sys.exit(1)


if __name__ == "__main__":
    _main()
