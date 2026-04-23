from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vector_embedded_finder.connectors.canvas import CanvasConnector
from vector_embedded_finder.connectors.gmail import GmailConnector


def test_gmail_not_authenticated_without_token(monkeypatch: Any, tmp_path: Path) -> None:
    from vector_embedded_finder import config

    token_path = tmp_path / "gmail.json"
    monkeypatch.setattr(config, "GMAIL_CREDENTIALS_FILE", token_path, raising=False)
    assert GmailConnector().is_authenticated() is False


def test_canvas_not_authenticated_without_creds(monkeypatch: Any, tmp_path: Path) -> None:
    from vector_embedded_finder import config

    creds_path = tmp_path / "canvas.json"
    monkeypatch.setattr(config, "CANVAS_CREDENTIALS_FILE", creds_path, raising=False)
    assert CanvasConnector().is_authenticated() is False


def test_gmail_is_authenticated_with_token(monkeypatch: Any, tmp_path: Path) -> None:
    from vector_embedded_finder import config

    token_path = tmp_path / "gmail.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps({"token": "abc"}))
    monkeypatch.setattr(config, "GMAIL_CREDENTIALS_FILE", token_path, raising=False)
    assert GmailConnector().is_authenticated() is True
