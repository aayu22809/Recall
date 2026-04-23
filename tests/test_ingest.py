from __future__ import annotations

from pathlib import Path
from typing import Any

from vector_embedded_finder import ingest


def test_dedup_skips_existing(monkeypatch: Any, tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello")

    monkeypatch.setattr(ingest.store, "exists", lambda _doc_id: True)
    result = ingest.ingest_file(p)
    assert result["status"] == "skipped"


def test_error_envelope_on_bad_path() -> None:
    result = ingest._ingest_worker(Path("/definitely/not/a/real/file.txt"), "manual")
    assert result["status"] == "error"


def test_text_file_ingested(monkeypatch: Any, tmp_path: Path, fake_embedding: list[float]) -> None:
    p = tmp_path / "note.txt"
    p.write_text("hello world")

    captured: dict[str, Any] = {}
    monkeypatch.setattr(ingest.store, "exists", lambda _doc_id: False)
    monkeypatch.setattr(ingest.embedder, "embed_text", lambda _text: fake_embedding)

    def fake_add(doc_id: str, embedding: list[float], metadata: dict[str, Any], document: str = "") -> None:
        captured["doc_id"] = doc_id
        captured["embedding"] = embedding
        captured["metadata"] = metadata
        captured["document"] = document

    monkeypatch.setattr(ingest.store, "add", fake_add)
    result = ingest.ingest_file(p, source="manual")

    assert result["status"] == "embedded"
    assert result["category"] == "text"
    assert captured["metadata"]["file_name"] == "note.txt"
