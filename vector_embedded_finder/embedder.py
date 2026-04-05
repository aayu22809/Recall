"""Gemini Embedding 2 API wrapper for multimodal embedding."""

from __future__ import annotations

from pathlib import Path

from google import genai
from google.genai import types

from . import config


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.get_api_key())
    return _client


def embed_text(text: str, task: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    client = _get_client()
    result = client.models.embed_content(
        model=config.EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type=task,
            output_dimensionality=config.EMBEDDING_DIMENSIONS,
        ),
    )
    return result.embeddings[0].values


def embed_query(query: str) -> list[float]:
    return embed_text(query, task="RETRIEVAL_QUERY")


def embed_image(path: Path) -> list[float]:
    client = _get_client()
    with open(path, "rb") as f:
        image_bytes = f.read()

    from . import utils
    mt = utils.mime_type(path)

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


def embed_audio(path: Path) -> list[float]:
    client = _get_client()
    with open(path, "rb") as f:
        audio_bytes = f.read()

    from . import utils
    mt = utils.mime_type(path)

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


def embed_video(path: Path) -> list[float]:
    client = _get_client()
    with open(path, "rb") as f:
        video_bytes = f.read()

    from . import utils
    mt = utils.mime_type(path)

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


def embed_pdf(path: Path) -> list[float]:
    client = _get_client()
    with open(path, "rb") as f:
        pdf_bytes = f.read()

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
