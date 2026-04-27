"""Embedding provider wrapper with retry/backoff.

Supported providers:
- gemini (default)
- ollama (local)
- nim (OpenAI-compatible embeddings endpoint)
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from . import config

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from google import genai
    from google.genai import types

_client: Any | None = None
_genai_mod: Any | None = None
_genai_types_mod: Any | None = None

_MAX_RETRIES = 5
_BASE_BACKOFF = 2.0  # seconds


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        genai, _types = _google_modules()
        _client = genai.Client(api_key=config.get_api_key())
    return _client


def _google_modules() -> tuple[Any, Any]:
    global _genai_mod, _genai_types_mod
    if _genai_mod is None or _genai_types_mod is None:
        from google import genai
        from google.genai import types

        _genai_mod = genai
        _genai_types_mod = types
    return _genai_mod, _genai_types_mod


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "rate" in msg or "quota" in msg


def _call_with_retry(fn, provider: str):
    """Call fn(), retrying on rate-limit errors with exponential back-off."""
    wait = _BASE_BACKOFF
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except Exception as exc:
            if _is_rate_limit(exc) and attempt < _MAX_RETRIES - 1:
                jitter = random.uniform(0, wait * 0.2)
                actual = wait + jitter
                logger.warning(
                    "%s rate-limited — retrying in %.1fs (attempt %d/%d)",
                    provider,
                    actual, attempt + 1, _MAX_RETRIES,
                )
                time.sleep(actual)
                wait = min(wait * 2, 32.0)
            else:
                raise
    raise RuntimeError("unreachable")


def _embed_text_gemini(text: str, task: str) -> list[float]:
    client = _get_client()
    _genai, types = _google_modules()

    def _call():
        result = client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(
                task_type=task,
                output_dimensionality=config.EMBEDDING_DIMENSIONS,
            ),
        )
        return result.embeddings[0].values

    return _call_with_retry(_call, provider="gemini")


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

    return _call_with_retry(_call, provider="ollama")


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

    return _call_with_retry(_call, provider="nim")


def warmup_provider() -> None:
    provider = config.EMBEDDING_PROVIDER
    if provider == "gemini":
        _get_client()
        return
    if provider == "ollama":
        # Quick local health check.
        httpx.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5.0).raise_for_status()
        return
    if provider == "nim":
        if not config.NIM_EMBED_URL:
            raise ValueError("VEF_NIM_EMBED_URL is not configured")
        _ = config.get_nim_api_key()
        return
    raise ValueError(f"Unsupported VEF_EMBEDDING_PROVIDER: {provider}")


def embed_text(text: str, task: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    provider = config.EMBEDDING_PROVIDER
    if provider == "gemini":
        return _embed_text_gemini(text, task=task)
    if provider == "ollama":
        return _embed_text_ollama(text)
    if provider == "nim":
        return _embed_text_nim(text)
    raise ValueError(f"Unsupported VEF_EMBEDDING_PROVIDER: {provider}")


def embed_query(query: str) -> list[float]:
    return embed_text(query, task="RETRIEVAL_QUERY")


def embed_image(path: Path) -> list[float]:
    if config.EMBEDDING_PROVIDER != "gemini":
        return embed_text(f"Image file: {path.name}")
    client = _get_client()
    _genai, types = _google_modules()
    with open(path, "rb") as f:
        image_bytes = f.read()

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
        return result.embeddings[0].values

    return _call_with_retry(_call, provider="gemini")


def embed_audio(path: Path) -> list[float]:
    if config.EMBEDDING_PROVIDER != "gemini":
        return embed_text(f"Audio file: {path.name}")
    client = _get_client()
    _genai, types = _google_modules()
    with open(path, "rb") as f:
        audio_bytes = f.read()

    from . import utils
    mt = utils.mime_type(path)

    def _call():
        result = client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=types.Content(
                parts=[types.Part(inline_data=types.Blob(mime_type=mt, data=audio_bytes))]
            ),
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=config.EMBEDDING_DIMENSIONS,
            ),
        )
        return result.embeddings[0].values

    return _call_with_retry(_call, provider="gemini")


def embed_video(path: Path) -> list[float]:
    if config.EMBEDDING_PROVIDER != "gemini":
        return embed_text(f"Video file: {path.name}")
    client = _get_client()
    _genai, types = _google_modules()
    with open(path, "rb") as f:
        video_bytes = f.read()

    from . import utils
    mt = utils.mime_type(path)

    def _call():
        result = client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=types.Content(
                parts=[types.Part(inline_data=types.Blob(mime_type=mt, data=video_bytes))]
            ),
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=config.EMBEDDING_DIMENSIONS,
            ),
        )
        return result.embeddings[0].values

    return _call_with_retry(_call, provider="gemini")


def embed_pdf(path: Path) -> list[float]:
    if config.EMBEDDING_PROVIDER != "gemini":
        return embed_text(f"PDF file: {path.name}")
    client = _get_client()
    _genai, types = _google_modules()
    with open(path, "rb") as f:
        pdf_bytes = f.read()

    def _call():
        result = client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=types.Content(
                parts=[types.Part(inline_data=types.Blob(mime_type="application/pdf", data=pdf_bytes))]
            ),
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=config.EMBEDDING_DIMENSIONS,
            ),
        )
        return result.embeddings[0].values

    return _call_with_retry(_call, provider="gemini")
