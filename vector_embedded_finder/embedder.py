"""Embedding provider wrapper with local-first defaults."""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from pathlib import Path

import httpx
from google import genai
from google.genai import types

from . import config, model_manager

logger = logging.getLogger(__name__)

_client: genai.Client | None = None

_MAX_RETRIES = 5
_BASE_BACKOFF = 2.0


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.get_api_key())
    return _client


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "rate" in msg or "quota" in msg


def _call_with_retry(fn, provider: str):
    wait = _BASE_BACKOFF
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except Exception as exc:
            if _is_rate_limit(exc) and attempt < _MAX_RETRIES - 1:
                jitter = random.uniform(0, wait * 0.2)
                actual = wait + jitter
                logger.warning(
                    "%s rate-limited, retrying in %.1fs (attempt %d/%d)",
                    provider,
                    actual,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(actual)
                wait = min(wait * 2, 32.0)
            else:
                raise
    raise RuntimeError("unreachable")


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0:
        return vec
    return [float(v / norm) for v in vec]


def _hash_embedding(text: str) -> list[float]:
    dims = config.EMBEDDING_DIMENSIONS
    vec = [0.0] * dims
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for i in range(0, min(16, dims)):
            idx = (digest[i] + (i * 31)) % dims
            sign = -1.0 if digest[(i + 1) % len(digest)] % 2 else 1.0
            vec[idx] += sign * ((digest[(i + 2) % len(digest)] / 255.0) + 0.5)
    if not any(vec):
        vec[0] = 1.0
    return _normalize(vec)


def _embed_text_local(text: str) -> list[float]:
    model = model_manager.get_text_model()
    if model is None:
        return _hash_embedding(text)
    values = model.encode([text], normalize_embeddings=True)[0]
    return [float(x) for x in values[: config.EMBEDDING_DIMENSIONS]]


def _embed_path_local(path: Path, fallback_text: str) -> list[float]:
    model = model_manager.get_vision_model()
    if model is None:
        return _embed_text_local(fallback_text)
    try:
        from PIL import Image
    except Exception:
        return _embed_text_local(fallback_text)
    try:
        image = Image.open(path).convert("RGB")
        values = model.encode([image], normalize_embeddings=True)[0]
        return [float(x) for x in values[: config.EMBEDDING_DIMENSIONS]]
    except Exception:
        return _embed_text_local(fallback_text)


def _embed_text_gemini(text: str, task: str) -> list[float]:
    client = _get_client()

    def _call():
        result = client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(
                task_type=task,
                output_dimensionality=config.EMBEDDING_DIMENSIONS,
            ),
        )
        return [float(v) for v in result.embeddings[0].values]

    return _normalize(_call_with_retry(_call, provider="gemini"))


def _embed_text_ollama(text: str) -> list[float]:
    def _call():
        resp = httpx.post(
            config.OLLAMA_EMBED_URL,
            json={"model": config.OLLAMA_EMBED_MODEL, "prompt": text},
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        emb = data.get("embedding")
        if not isinstance(emb, list) or not emb:
            raise ValueError("Ollama embedding response missing 'embedding'")
        return [float(x) for x in emb]

    return _normalize(_call_with_retry(_call, provider="ollama"))


def _embed_text_nim(text: str) -> list[float]:
    if not config.NIM_EMBED_URL:
        raise ValueError("VEF_NIM_EMBED_URL is not set")

    def _call():
        headers = {
            "Authorization": f"Bearer {config.get_nim_api_key()}",
            "Content-Type": "application/json",
        }
        payload = {"model": config.NIM_EMBED_MODEL, "input": text}
        resp = httpx.post(config.NIM_EMBED_URL, headers=headers, json=payload, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data") or []
        if not rows or "embedding" not in rows[0]:
            raise ValueError("NIM embedding response missing data[0].embedding")
        return [float(x) for x in rows[0]["embedding"]]

    return _normalize(_call_with_retry(_call, provider="nim"))


def warmup_provider() -> None:
    provider = config.EMBEDDING_PROVIDER
    if provider == "local":
        model_manager.warmup()
        return
    if provider == "gemini":
        _get_client()
        model_manager.warmup()
        return
    if provider == "ollama":
        httpx.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5.0).raise_for_status()
        model_manager.warmup()
        return
    if provider == "nim":
        if not config.NIM_EMBED_URL:
            raise ValueError("VEF_NIM_EMBED_URL is not configured")
        _ = config.get_nim_api_key()
        model_manager.warmup()
        return
    raise ValueError(f"Unsupported RECALL_EMBEDDING_PROVIDER: {provider}")


def embed_text(text: str, task: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    provider = config.EMBEDDING_PROVIDER
    if provider == "local":
        return _embed_text_local(text)
    if provider == "gemini":
        return _embed_text_gemini(text, task=task)
    if provider == "ollama":
        return _embed_text_ollama(text)
    if provider == "nim":
        return _embed_text_nim(text)
    raise ValueError(f"Unsupported provider: {provider}")


def embed_query(query: str) -> list[float]:
    return embed_text(query, task="RETRIEVAL_QUERY")


def embed_image(path: Path) -> list[float]:
    if config.EMBEDDING_PROVIDER == "local":
        return _embed_path_local(path, fallback_text=f"Image file {path.name}")
    if config.EMBEDDING_PROVIDER != "gemini":
        return embed_text(f"Image file {path.name}")
    client = _get_client()
    image_bytes = path.read_bytes()
    from . import utils
    mt = utils.mime_type(path)

    def _call():
        result = client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=types.Content(
                parts=[types.Part(inline_data=types.Blob(mime_type=mt, data=image_bytes))]
            ),
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=config.EMBEDDING_DIMENSIONS,
            ),
        )
        return [float(v) for v in result.embeddings[0].values]

    return _normalize(_call_with_retry(_call, provider="gemini"))


def embed_audio(path: Path) -> list[float]:
    return embed_text(f"Audio file {path.name}")


def embed_video(path: Path) -> list[float]:
    return embed_text(f"Video file {path.name}")


def embed_pdf(path: Path) -> list[float]:
    return embed_text(f"PDF file {path.name}")
