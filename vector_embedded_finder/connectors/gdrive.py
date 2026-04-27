"""Google Drive connector — OAuth2 read-only, indexes files and Google docs."""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pypdf

from .. import config, embedder, store, utils
from .base import BaseConnector

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

_DOC_EXPORT = "text/plain"
_SHEET_EXPORT = "text/csv"
_SLIDE_EXPORT = "text/plain"
_MAX_TEXT_CHARS = 8000


def _token_path() -> Path:
    # Shared with Gmail/GCal so one OAuth flow can cover all Google connectors.
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
    p = _token_path()
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_token(data: dict) -> None:
    config.ensure_vef_dirs()
    _token_path().write_text(json.dumps(data, indent=2))


class GDriveConnector(BaseConnector):
    source_id = "gdrive"
    display_name = "Google Drive"

    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_data = _load_token()
        if not token_data:
            raise RuntimeError("Not authenticated — call authenticate() first")

        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(json.loads(creds.to_json()))

        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def authenticate(self) -> None:
        """Open browser OAuth flow.

        Short-circuits to success when the Tauri shell has already written a
        token JSON via PUT /credentials/gmail (the token is shared across
        Gmail/GCal/GDrive).
        """
        if self.is_authenticated():
            logger.info("Google Drive already authenticated (token present)")
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
        logger.info("Google Drive authentication successful")

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
        files: list[dict] = []
        page_token = None
        query_parts = ["trashed = false"]
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            query_parts.append(f"modifiedTime > '{since.isoformat().replace('+00:00', 'Z')}'")
        query = " and ".join(query_parts)

        while True:
            resp = (
                svc.files()
                .list(
                    q=query,
                    pageSize=200,
                    pageToken=page_token,
                    fields=(
                        "nextPageToken, files("
                        "id,name,mimeType,modifiedTime,size,webViewLink,parents)"
                        ")"
                    ),
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            batch = resp.get("files", [])
            files.extend(batch)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        total = len(files)
        logger.info("Google Drive: found %d files to sync", total)
        results: list[dict] = []

        for i, item in enumerate(files, 1):
            try:
                result = self._ingest_item(svc, item)
            except Exception as exc:
                result = {"status": "error", "id": item.get("id", ""), "error": str(exc)}
            results.append(result)
            if progress_cb:
                progress_cb(i, total, result)

        return results

    def _ingest_item(self, svc, item: dict) -> dict:
        file_id = str(item.get("id", ""))
        if not file_id:
            return {"status": "skipped", "reason": "missing file id"}

        doc_id = utils.text_hash(f"gdrive:{file_id}")
        if store.exists(doc_id):
            return {"status": "skipped", "id": doc_id, "file_id": file_id}

        name = str(item.get("name", "Untitled"))
        mime = str(item.get("mimeType", ""))
        modified = str(item.get("modifiedTime", "")) or utils.now_iso()
        link = str(item.get("webViewLink", ""))

        text = self._extract_text(svc, file_id, name, mime)
        if not text:
            return {"status": "skipped", "id": doc_id, "reason": "unsupported_or_empty"}

        embedding = embedder.embed_text(text[:_MAX_TEXT_CHARS])
        metadata = {
            "file_path": f"gdrive://file/{file_id}",
            "file_name": name,
            "file_type": mime or "application/octet-stream",
            "media_category": "document",
            "timestamp": modified,
            "source": self.source_id,
            "description": text[:500],
            "file_size": int(item.get("size", 0) or 0),
            "file_id": file_id,
            "url": link,
        }
        store.add(doc_id, embedding, metadata, document=text[:5000])
        return {"status": "embedded", "id": doc_id, "file_id": file_id}

    def _extract_text(self, svc, file_id: str, name: str, mime: str) -> str:
        try:
            if mime == "application/vnd.google-apps.document":
                data = svc.files().export_media(fileId=file_id, mimeType=_DOC_EXPORT).execute()
                return data.decode("utf-8", errors="replace").strip()
            if mime == "application/vnd.google-apps.spreadsheet":
                data = svc.files().export_media(fileId=file_id, mimeType=_SHEET_EXPORT).execute()
                return data.decode("utf-8", errors="replace").strip()
            if mime == "application/vnd.google-apps.presentation":
                data = svc.files().export_media(fileId=file_id, mimeType=_SLIDE_EXPORT).execute()
                return data.decode("utf-8", errors="replace").strip()
            if mime == "application/pdf" or name.lower().endswith(".pdf"):
                data = svc.files().get_media(fileId=file_id).execute()
                reader = pypdf.PdfReader(io.BytesIO(data))
                chunks = [(p.extract_text() or "") for p in reader.pages[:30]]
                return "\n".join(chunks).strip()
            if mime.startswith("text/") or name.lower().endswith(
                (".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".py", ".js", ".ts")
            ):
                data = svc.files().get_media(fileId=file_id).execute()
                return data.decode("utf-8", errors="replace").strip()
        except Exception as exc:
            logger.debug("Drive text extraction failed for %s (%s): %s", name, file_id, exc)
        return ""


def _main() -> None:
    import sys

    args = sys.argv[1:]
    connector = GDriveConnector()

    if args == ["authenticate"]:
        connector.authenticate()
        print("Google Drive authentication complete.")
        return
    if args == ["sync"]:
        results = connector.sync(since=None)
        embedded = sum(1 for r in results if r.get("status") == "embedded")
        print(f"Google Drive sync complete: {embedded}/{len(results)} embedded.")
        return

    print("Usage: python -m vector_embedded_finder.connectors.gdrive authenticate|sync")
    sys.exit(1)


if __name__ == "__main__":
    _main()
