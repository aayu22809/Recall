from __future__ import annotations

import importlib
from typing import Any

search_mod = importlib.import_module("vector_embedded_finder.search")


def _raw_result(
    *,
    doc_id: str = "id-1",
    distance: float = 0.6,
    file_name: str = "draft.txt",
    description: str = "some text",
    media_category: str = "text",
    source: str = "manual",
) -> dict[str, Any]:
    return {
        "ids": [[doc_id]],
        "metadatas": [[{
            "file_path": "/tmp/draft.txt",
            "file_name": file_name,
            "media_category": media_category,
            "timestamp": "2025-01-01T00:00:00+00:00",
            "description": description,
            "source": source,
        }]],
        "documents": [["body text"]],
        "distances": [[distance]],
    }


def test_similarity_threshold_filters_low_scores(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    monkeypatch.setattr(search_mod, "MIN_SIMILARITY", 0.45)
    monkeypatch.setattr(search_mod.store, "search", lambda *_a, **_k: _raw_result(distance=0.7))

    results = search_mod.search("anything", n_results=5)
    assert results == []


def test_keyword_boost_raises_score(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    monkeypatch.setattr(search_mod, "MIN_SIMILARITY", 0.0)

    def fake_search(*_a: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("where_document"):
            return _raw_result(
                distance=0.6,
                file_name="plasma-wound-report.txt",
                description="plasma treatment notes",
            )
        return _raw_result(
            distance=0.6,
            file_name="plasma-wound-report.txt",
            description="plasma treatment notes",
        )

    monkeypatch.setattr(search_mod.store, "search", fake_search)
    results = search_mod.search("plasma report", n_results=5)
    assert results
    assert float(results[0]["similarity"]) > 0.4


def test_intent_filter_image(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    calls: list[dict[str, Any]] = []

    def fake_search(_embedding: list[float], **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ids": [[]], "metadatas": [[]], "documents": [[]], "distances": [[]]}

    monkeypatch.setattr(search_mod.store, "search", fake_search)
    _ = search_mod.search("photo of sunset")

    assert calls
    where = calls[0].get("where")
    assert where == {"media_category": {"$eq": "image"}}


def test_intent_filter_email(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    calls: list[dict[str, Any]] = []

    def fake_search(_embedding: list[float], **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ids": [[]], "metadatas": [[]], "documents": [[]], "distances": [[]]}

    monkeypatch.setattr(search_mod.store, "search", fake_search)
    _ = search_mod.search("email from john")

    assert calls
    where = calls[0].get("where")
    assert isinstance(where, dict)
    assert "$and" in where
    terms = where["$and"]
    assert {"media_category": {"$eq": "email"}} in terms
    assert {"source": {"$eq": "gmail"}} in terms


def test_no_results_empty_db(monkeypatch: Any, fake_embedding: list[float]) -> None:
    monkeypatch.setattr(search_mod.embedder, "embed_query", lambda _q: fake_embedding)
    monkeypatch.setattr(
        search_mod.store,
        "search",
        lambda *_a, **_k: {"ids": [[]], "metadatas": [[]], "documents": [[]], "distances": [[]]},
    )
    assert search_mod.search("nothing here") == []
