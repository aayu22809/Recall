from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from vector_embedded_finder import daemon


def _client(monkeypatch: Any, tmp_path: Path) -> TestClient:
    from vector_embedded_finder import config, embedder, migration, model_manager, store
    search_mod = importlib.import_module("vector_embedded_finder.search")

    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(config, "WATCHED_DIRS_FILE", tmp_path / "watched_dirs.json", raising=False)
    monkeypatch.setattr(config, "ensure_vef_dirs", lambda: None, raising=False)
    monkeypatch.setattr(config, "ensure_runtime_dirs", lambda: None, raising=False)
    monkeypatch.setattr(embedder, "warmup_provider", lambda: None)
    monkeypatch.setattr(model_manager, "warmup", lambda: None)
    monkeypatch.setattr(migration, "ensure_migrated", lambda: {"status": "complete"})
    monkeypatch.setattr(migration, "status", lambda: {"status": "complete"})
    monkeypatch.setattr(store, "initialize", lambda: None)
    monkeypatch.setattr(store, "index_status", lambda: {"backend": "memory", "count": 0, "ready": True, "dirty": False})
    monkeypatch.setattr(store, "get_sources", lambda: [])

    monkeypatch.setattr(store, "count", lambda: 0)
    monkeypatch.setattr(search_mod, "search", lambda *_a, **_k: [])
    monkeypatch.setattr(daemon, "_run_connector_sync_once", lambda **_k: {})

    app = daemon._build_app()
    return TestClient(app)


def test_health_endpoint(monkeypatch: Any, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


def test_stats_endpoint(monkeypatch: Any, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.get("/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert isinstance(body["count"], int)


def test_search_endpoint_empty(monkeypatch: Any, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.post("/search", json={"query": ""})
        assert resp.status_code == 200
        assert resp.json() == []


def test_connector_status_keys(monkeypatch: Any, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.get("/connector-status")
        assert resp.status_code == 200
        body = resp.json()
        expected = set(daemon._connector_specs().keys())
        assert set(body.keys()) == expected


def test_progress_shape(monkeypatch: Any, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.get("/progress")
        assert resp.status_code == 200
        body = resp.json()
        assert {"indexing", "queued", "total_indexed"} <= set(body.keys())
