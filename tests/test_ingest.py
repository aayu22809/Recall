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

    def fake_add(
        doc_id: str,
        embedding: list[float],
        metadata: dict[str, Any],
        document: str = "",
        enrichment: dict[str, Any] | None = None,
    ) -> None:
        captured["doc_id"] = doc_id
        captured["embedding"] = embedding
        captured["metadata"] = metadata
        captured["document"] = document
        captured["enrichment"] = enrichment or {}

    monkeypatch.setattr(ingest.store, "add", fake_add)
    monkeypatch.setattr(
        ingest.store,
        "retire_path_versions",
        lambda path, *, keep_doc_id: captured.__setitem__("retired", (str(path), keep_doc_id)),
    )
    result = ingest.ingest_file(p, source="manual")

    assert result["status"] == "embedded"
    assert result["category"] == "text"
    assert captured["metadata"]["file_name"] == "note.txt"
    assert captured["enrichment"] == {
        "caption": "",
        "ocr_text": "",
        "gps_city": "",
        "face_count": 0,
        "exif_date": "",
        "exif_camera": "",
    }
    assert captured["retired"] == (str(p.resolve()), captured["doc_id"])


def test_ingest_failure_does_not_retire_path_versions(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    p = tmp_path / "note.txt"
    p.write_text("hello world")

    calls = {"add": 0, "retire": 0}
    monkeypatch.setattr(ingest.store, "exists", lambda _doc_id: False)
    monkeypatch.setattr(
        ingest.store,
        "add",
        lambda *_a, **_k: calls.__setitem__("add", calls["add"] + 1),
    )
    monkeypatch.setattr(
        ingest.store,
        "retire_path_versions",
        lambda *_a, **_k: calls.__setitem__("retire", calls["retire"] + 1),
    )
    monkeypatch.setattr(
        ingest.embedder,
        "embed_text",
        lambda _text: (_ for _ in ()).throw(RuntimeError("embed fail")),
    )

    try:
        ingest.ingest_file(p, source="manual")
    except RuntimeError:
        pass

    assert calls["add"] == 0
    assert calls["retire"] == 0
