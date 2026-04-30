"""Local model lifecycle for Recall.

The implementation is intentionally resilient:
- prefers local sentence-transformers models cached under ~/.recall/models
- records model state in a manifest for UI and daemon status
- falls back to deterministic in-process embeddings when heavyweight deps are
  not installed, so the rest of the product still functions
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from . import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model_name: str
    local_dir: Path
    kind: str


_MANIFEST_LOCK = Lock()
_TEXT_MODEL = None
_VISION_MODEL = None


def _model_specs() -> list[ModelSpec]:
    return [
        ModelSpec(
            key="text",
            model_name=config.EMBEDDING_MODEL,
            local_dir=config.MODELS_DIR / "text",
            kind="embedding",
        ),
        ModelSpec(
            key="vision",
            model_name=config.VISION_EMBEDDING_MODEL,
            local_dir=config.MODELS_DIR / "vision",
            kind="embedding",
        ),
        ModelSpec(
            key="reranker",
            model_name=config.RERANKER_MODEL,
            local_dir=config.MODELS_DIR / "reranker",
            kind="reranker",
        ),
    ]


def _read_manifest() -> dict[str, Any]:
    if not config.MODEL_MANIFEST_PATH.exists():
        return {}
    try:
        payload = json.loads(config.MODEL_MANIFEST_PATH.read_text())
        if isinstance(payload, dict):
            return payload
    except Exception as exc:
        logger.debug("Could not read model manifest: %s", exc)
    return {}


def _write_manifest(payload: dict[str, Any]) -> None:
    config.ensure_runtime_dirs()
    config.MODEL_MANIFEST_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))


def ensure_manifest() -> dict[str, Any]:
    with _MANIFEST_LOCK:
        payload = _read_manifest()
        models = payload.setdefault("models", {})
        for spec in _model_specs():
            row = models.setdefault(
                spec.key,
                {
                    "name": spec.model_name,
                    "kind": spec.kind,
                    "path": str(spec.local_dir),
                    "status": "pending",
                    "backend": "sentence-transformers",
                },
            )
            row["name"] = spec.model_name
            row["path"] = str(spec.local_dir)
        _write_manifest(payload)
        return payload


def _mark_status(key: str, *, status: str, backend: str, detail: str = "") -> None:
    payload = ensure_manifest()
    models = payload.setdefault("models", {})
    row = models.setdefault(key, {})
    row["status"] = status
    row["backend"] = backend
    if detail:
        row["detail"] = detail
    elif "detail" in row:
        del row["detail"]
    _write_manifest(payload)


def _load_sentence_transformer(model_name: str, cache_dir: Path):
    from sentence_transformers import SentenceTransformer

    cache_dir.mkdir(parents=True, exist_ok=True)
    return SentenceTransformer(model_name, cache_folder=str(cache_dir))


def get_text_model():
    global _TEXT_MODEL
    if _TEXT_MODEL is not None:
        return _TEXT_MODEL
    spec = next(s for s in _model_specs() if s.key == "text")
    try:
        _TEXT_MODEL = _load_sentence_transformer(spec.model_name, spec.local_dir)
        _mark_status("text", status="ready", backend="sentence-transformers")
        return _TEXT_MODEL
    except Exception as exc:
        logger.info("Local text model unavailable, using hash fallback: %s", exc)
        _mark_status("text", status="fallback", backend="hash", detail=str(exc))
        _TEXT_MODEL = False
        return None


def get_vision_model():
    global _VISION_MODEL
    if _VISION_MODEL is not None:
        return _VISION_MODEL
    spec = next(s for s in _model_specs() if s.key == "vision")
    try:
        _VISION_MODEL = _load_sentence_transformer(spec.model_name, spec.local_dir)
        _mark_status("vision", status="ready", backend="sentence-transformers")
        return _VISION_MODEL
    except Exception as exc:
        logger.info("Local vision model unavailable, using text fallback: %s", exc)
        _mark_status("vision", status="fallback", backend="text-proxy", detail=str(exc))
        _VISION_MODEL = False
        return None


def warmup() -> None:
    ensure_manifest()
    if config.EMBEDDING_PROVIDER == "local":
        get_text_model()
        get_vision_model()
    elif config.EMBEDDING_PROVIDER == "gemini":
        _mark_status("text", status="external", backend="gemini")
        _mark_status("vision", status="external", backend="gemini")
    elif config.EMBEDDING_PROVIDER == "ollama":
        _mark_status("text", status="external", backend="ollama")
        _mark_status("vision", status="external", backend="ollama")
    elif config.EMBEDDING_PROVIDER == "nim":
        _mark_status("text", status="external", backend="nim")
        _mark_status("vision", status="external", backend="nim")


def model_status() -> dict[str, Any]:
    payload = ensure_manifest()
    payload.setdefault("runtime", {})
    payload["runtime"].update(
        {
            "provider": config.EMBEDDING_PROVIDER,
            "apple_silicon": config.is_apple_silicon(),
            "models_dir": str(config.MODELS_DIR),
        }
    )
    return payload
