"""Google Calendar connector — OAuth2 read-only, indexes calendar events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

from .. import config, embedder, store, utils
from .base import BaseConnector

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",  # shared OAuth client
]
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

PAST_MONTHS = 3
FUTURE_MONTHS = 6


def _creds_path() -> Path:
    return config.GMAIL_CREDENTIALS_FILE  # shared token file with Gmail


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


class GCalConnector(BaseConnector):
    source_id = "gcal"
    display_name = "Google Calendar"

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

        creds = Credentials.from_authorized_user_info(token_data, CALENDAR_SCOPES)

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(json.loads(creds.to_json()))

        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def authenticate(self) -> None:
        """Open browser OAuth flow. Shares credential file with Gmail."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        client_file = _oauth_client_path()
        if not client_file.exists():
            raise FileNotFoundError(
                f"OAuth client credentials not found at {client_file}.\n"
                "Download your OAuth 2.0 client JSON from Google Cloud Console and save it there."
            )

        # Request combined scopes so one auth covers both Gmail + Calendar
        combined_scopes = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
        ]
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), combined_scopes)
        creds = flow.run_local_server(port=0)
        _save_token(json.loads(creds.to_json()))
        logger.info("Google Calendar authentication successful")

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

        now = datetime.now(timezone.utc)
        time_min = (since or (now - timedelta(days=PAST_MONTHS * 30))).isoformat()
        time_max = (now + timedelta(days=FUTURE_MONTHS * 30)).isoformat()

        # Collect all calendars
        cal_list = svc.calendarList().list().execute()
        calendars = cal_list.get("items", [])

        all_events: list[tuple[dict, str]] = []  # (event, calendar_name)
        for cal in calendars:
            cal_id = cal["id"]
            cal_name = cal.get("summary", cal_id)
            page_token = None
            while True:
                kwargs: dict = {
                    "calendarId": cal_id,
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": True,
                    "orderBy": "startTime",
                    "maxResults": 250,
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                try:
                    resp = svc.events().list(**kwargs).execute()
                    for ev in resp.get("items", []):
                        all_events.append((ev, cal_name))
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        break
                except Exception as e:
                    logger.warning("Error fetching calendar %s: %s", cal_id, e)
                    break

        total = len(all_events)
        logger.info("GCal: found %d events to sync", total)
        results: list[dict] = []

        for i, (event, cal_name) in enumerate(all_events, 1):
            try:
                result = self._ingest_event(event, cal_name)
                results.append(result)
            except Exception as e:
                result = {"status": "error", "id": event.get("id", "?"), "error": str(e)}
                results.append(result)

            if progress_cb:
                progress_cb(i, total, result)

        return results

    def _ingest_event(self, event: dict, calendar_name: str) -> dict:
        event_id = event["id"]
        doc_id = utils.text_hash(f"gcal:{event_id}")

        if store.exists(doc_id):
            return {"status": "skipped", "id": doc_id, "event_id": event_id}

        title = event.get("summary", "(no title)")
        description_body = event.get("description", "")
        location = event.get("location", "")

        start = event.get("start", {})
        end = event.get("end", {})
        start_str = start.get("dateTime") or start.get("date", "")
        end_str = end.get("dateTime") or end.get("date", "")

        attendees = [
            a.get("displayName") or a.get("email", "")
            for a in event.get("attendees", [])
            if not a.get("self")
        ]
        attendees_str = ", ".join(attendees[:10]) if attendees else ""

        description = f"{title} on {start_str}"
        if attendees_str:
            description += f" with {attendees_str}"
        if location:
            description += f" at {location}"
        if description_body:
            description += f": {description_body[:500]}"

        embedding = embedder.embed_text(description[:8000])

        metadata = {
            "file_path": f"gcal://event/{event_id}",
            "file_name": title,
            "file_type": "text/calendar",
            "media_category": "calendar_event",
            "timestamp": utils.now_iso(),
            "source": self.source_id,
            "description": description[:500],
            "file_size": len(description.encode()),
            "event_id": event_id,
            "start": start_str,
            "end": end_str,
            "location": location,
            "attendees": json.dumps(attendees),
            "calendar": calendar_name,
        }

        store.add(doc_id, embedding, metadata, document=description[:500])
        return {"status": "embedded", "id": doc_id, "event_id": event_id}


def _main() -> None:
    import sys

    args = sys.argv[1:]
    connector = GCalConnector()

    if args == ["authenticate"]:
        connector.authenticate()
        print("Google Calendar authentication complete.")
        return

    if args == ["sync"]:
        results = connector.sync(since=None)
        embedded = sum(1 for r in results if r.get("status") == "embedded")
        print(f"Google Calendar sync complete: {embedded}/{len(results)} embedded.")
        return

    print("Usage: python -m vector_embedded_finder.connectors.gcal authenticate|sync")
    sys.exit(1)


if __name__ == "__main__":
    _main()
