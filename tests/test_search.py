from __future__ import annotations

import importlib
from typing import Any

from vector_embedded_finder.store import Candidate

search_mod = importlib.import_module("vector_embedded_finder.search")


def _candidate(
    *,
    doc_id: str = "id-1",
    score: float = 0.6,
    file_name: str = "draft.txt",
    description: str = "some text",
    media_category: str = "text",
    source: str = "manual",
) -> Candidate:
    return Candidate(
        file_id=1,
        doc_id=doc_id,
        distance=max(0.0, 1.0 - score),
        score=score,
        metadata={
            "file_path": "/tmp/draft.txt",
            "file_name": file_name,
            "media_category": media_category,
            "timestamp": "2025-01-01T00:00:00+00:00",
            "description": description,
            "source": source,
            "preview": "body text",
        },
    )


def test_similarity_threshold_filters_low_scores(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    monkeypatch.setattr(search_mod, "MIN_SIMILARITY", 0.45)
    monkeypatch.setattr(search_mod.store, "dense_search", lambda *_a, **_k: [_candidate(score=0.3)])
    monkeypatch.setattr(search_mod.store, "keyword_search", lambda *_a, **_k: [])

    results = search_mod.search("anything", n_results=5)
    assert results == []


def test_keyword_boost_raises_score(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    monkeypatch.setattr(search_mod, "MIN_SIMILARITY", 0.0)
    monkeypatch.setattr(
        search_mod.store,
        "dense_search",
        lambda *_a, **_k: [
            _candidate(
                score=0.4,
                file_name="plasma-wound-report.txt",
                description="plasma treatment notes",
            )
        ],
    )
    monkeypatch.setattr(
        search_mod.store,
        "keyword_search",
        lambda *_a, **_k: [
            _candidate(
                score=0.41,
                file_name="plasma-wound-report.txt",
                description="plasma treatment notes",
            )
        ],
    )
    results = search_mod.search("plasma report", n_results=5)
    assert results
    assert float(results[0]["similarity"]) > 0.4


def test_intent_filter_image(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    calls: list[dict[str, Any]] = []

    def fake_dense(_embedding: list[float], **kwargs: Any):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(search_mod.store, "dense_search", fake_dense)
    monkeypatch.setattr(search_mod.store, "keyword_search", lambda *_a, **_k: [])
    _ = search_mod.search("photo of sunset")

    assert calls
    filters = calls[0].get("filters")
    assert filters == {"media_category": "image"}


def test_intent_filter_email(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    calls: list[dict[str, Any]] = []

    def fake_dense(_embedding: list[float], **kwargs: Any):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(search_mod.store, "dense_search", fake_dense)
    monkeypatch.setattr(search_mod.store, "keyword_search", lambda *_a, **_k: [])
    _ = search_mod.search("email from john")

    assert calls
    filters = calls[0].get("filters") or {}
    assert filters.get("media_category") == "email"
    assert filters.get("sources") == ["gmail"]


def test_no_results_empty_db(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    monkeypatch.setattr(search_mod.store, "dense_search", lambda *_a, **_k: [])
    monkeypatch.setattr(search_mod.store, "keyword_search", lambda *_a, **_k: [])
    assert search_mod.search("nothing here") == []


def test_query_embedding_cache_skips_second_embed(monkeypatch: Any, fake_embedding: list[float]) -> None:
    calls: list[str] = []
    search_mod._embed_query_cached.cache_clear()

    def fake_embed(query: str) -> list[float]:
        calls.append(query)
        return fake_embedding

    monkeypatch.setattr(search_mod.embedder, "embed_query", fake_embed)
    monkeypatch.setattr(search_mod.store, "dense_search", lambda *_a, **_k: [])
    monkeypatch.setattr(search_mod.store, "keyword_search", lambda *_a, **_k: [])

    assert search_mod.search("repeat query") == []
    assert search_mod.search("repeat query") == []
    assert calls == ["repeat query"]
