"""Search interface for natural language queries."""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from . import config, embedder, store
from .reranker import maybe_rerank, reciprocal_rank_fusion

MIN_SIMILARITY = float(os.environ.get("VEF_MIN_SIMILARITY", "0.35"))
RRF_K = int(os.environ.get("VEF_RRF_K", "60"))
EMBED_CACHE_SIZE = int(os.environ.get("VEF_SEARCH_EMBED_CACHE_SIZE", "256"))
RESULT_CACHE_SIZE = int(os.environ.get("RECALL_RESULT_CACHE_SIZE", "128"))

_RESULT_CACHE: OrderedDict[tuple[Any, ...], list[dict[str, Any]]] = OrderedDict()

_MEDIA_KEYWORDS = {
    "image": {"image", "photo", "picture", "screenshot"},
    "video": {"video", "clip", "recording"},
    "audio": {"audio", "sound", "voice", "meeting recording"},
    "email": {"email", "gmail", "thread", "message"},
    "document": {"pdf", "doc", "document", "paper", "slides", "deck"},
}


def _tokenize_query(query: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1}


def _keyword_boost(result: dict[str, Any], query: str) -> float:
    words = _tokenize_query(query)
    if not words:
        return 0.0
    text = " ".join(
        [
            str(result.get("file_name", "")),
            str(result.get("description", "")),
            str(result.get("preview", "")),
        ]
    ).lower()
    meta = result.get("metadata", {})
    if isinstance(meta, dict):
        text += " " + " ".join(
            str(meta.get(key, ""))
            for key in ("caption", "ocr_text", "gps_city", "exif_camera")
        ).lower()
    matches = sum(1 for word in words if word in text)
    return min(0.2 * matches / max(len(words), 1), 0.2)


def _detect_media_intent(query: str) -> str | None:
    q = query.lower()
    for media, keywords in _MEDIA_KEYWORDS.items():
        if any(keyword in q for keyword in keywords):
            return media
    return None


def _detect_source_intent(query: str) -> list[str] | None:
    q = query.lower()
    if any(word in q for word in ("email", "gmail", "thread", "inbox")):
        return ["gmail"]
    if any(word in q for word in ("calendar", "event", "meeting", "appointment")):
        return ["gcal", "calai"]
    return None


def _detect_time_cutoff(query: str) -> str | None:
    q = query.lower()
    now = datetime.now(timezone.utc)
    if "today" in q:
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if "yesterday" in q:
        dt = now - timedelta(days=1)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if "last week" in q:
        return (now - timedelta(days=7)).isoformat()
    if "last month" in q:
        return (now - timedelta(days=30)).isoformat()
    return None


def _build_filters(
    query: str,
    *,
    media_type: str | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    inferred_media = _detect_media_intent(query) if not media_type else None
    if media_type:
        filters["media_category"] = media_type
    elif inferred_media:
        filters["media_category"] = inferred_media

    inferred_sources = _detect_source_intent(query) if not sources else None
    source_filters = sources or inferred_sources
    if source_filters:
        filters["sources"] = list(source_filters)

    since_cutoff = _detect_time_cutoff(query)
    if since_cutoff:
        filters["since"] = since_cutoff
    return filters


def _candidate_to_result(candidate) -> dict[str, Any]:
    meta = dict(candidate.metadata)
    preview = str(meta.get("preview", ""))[:200]
    return {
        "id": candidate.doc_id,
        "similarity": round(float(candidate.score), 4),
        "file_path": meta.get("file_path", ""),
        "file_name": meta.get("file_name", ""),
        "media_category": meta.get("media_category", ""),
        "timestamp": meta.get("timestamp", ""),
        "description": meta.get("description", ""),
        "source": meta.get("source", ""),
        "preview": preview,
        "metadata": meta,
    }


@lru_cache(maxsize=EMBED_CACHE_SIZE)
def _embed_query_cached(
    query: str,
    provider: str,
    model: str,
    dimensions: int,
) -> tuple[float, ...]:
    return tuple(float(v) for v in embedder.embed_query(query))


def _query_embedding(query: str) -> list[float]:
    return list(
        _embed_query_cached(
            query,
            config.EMBEDDING_PROVIDER,
            config.EMBEDDING_MODEL,
            config.EMBEDDING_DIMENSIONS,
        )
    )


def _result_cache_key(
    query: str,
    n_results: int,
    media_type: str | None,
    sources: list[str] | None,
) -> tuple[Any, ...]:
    return (
        query.strip().lower(),
        int(n_results),
        media_type or "",
        tuple(sorted(sources or [])),
        config.EMBEDDING_PROVIDER,
        config.EMBEDDING_MODEL,
        store.cache_epoch(),
    )


def _result_cache_get(key: tuple[Any, ...]) -> list[dict[str, Any]] | None:
    cached = _RESULT_CACHE.get(key)
    if cached is None:
        return None
    _RESULT_CACHE.move_to_end(key)
    return cached


def _result_cache_put(key: tuple[Any, ...], value: list[dict[str, Any]]) -> None:
    _RESULT_CACHE[key] = value
    _RESULT_CACHE.move_to_end(key)
    while len(_RESULT_CACHE) > RESULT_CACHE_SIZE:
        _RESULT_CACHE.popitem(last=False)


def search(
    query: str,
    n_results: int = 20,
    media_type: str | None = None,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []

    cache_key = _result_cache_key(query, n_results, media_type, sources)
    cached = _result_cache_get(cache_key)
    if cached is not None:
        return cached

    filters = _build_filters(query, media_type=media_type, sources=sources)
    query_embedding = _query_embedding(query)

    dense = store.dense_search(query_embedding, n_results=n_results, filters=filters)
    try:
        keyword = store.keyword_search(query, n_results=n_results, filters=filters)
    except Exception:
        keyword = []

    by_id: dict[str, dict[str, Any]] = {}
    for candidate in dense:
        row = _candidate_to_result(candidate)
        by_id[row["id"]] = row
    for candidate in keyword:
        row = _candidate_to_result(candidate)
        existing = by_id.get(row["id"])
        if existing is None or float(row["similarity"]) > float(existing["similarity"]):
            by_id[row["id"]] = row

    if keyword:
        fused_ids = reciprocal_rank_fusion(
            [[row.doc_id for row in dense], [row.doc_id for row in keyword]],
            k=RRF_K,
        )
    else:
        fused_ids = [row.doc_id for row in dense]

    fused_len = max(len(fused_ids), 1)
    results: list[dict[str, Any]] = []
    for idx, doc_id in enumerate(fused_ids):
        row = by_id.get(doc_id)
        if row is None:
            continue
        rrf_bonus = max(0.0, 0.08 * (1 - (idx / fused_len)))
        row["similarity"] = round(
            min(1.0, float(row["similarity"]) + _keyword_boost(row, query) + rrf_bonus),
            4,
        )
        if float(row["similarity"]) >= MIN_SIMILARITY:
            results.append(row)

    if not results and dense:
        fallback = [_candidate_to_result(candidate) for candidate in dense[:n_results]]
        results = [row for row in fallback if float(row["similarity"]) >= MIN_SIMILARITY]

    results.sort(key=lambda item: float(item["similarity"]), reverse=True)
    results = maybe_rerank(query, results[:n_results])
    _result_cache_put(cache_key, results[:n_results])
    return results[:n_results]


def format_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No results found."

    lines = []
    for i, row in enumerate(results, 1):
        score_pct = f"{float(row['similarity']) * 100:.1f}%"
        path = row["file_path"] or "(text snippet)"
        category = row["media_category"]
        ts = str(row["timestamp"])[:10] if row["timestamp"] else "unknown"
        lines.append(f"**{i}. [{category}] {row['file_name'] or 'text'}** — {score_pct} match")
        lines.append(f"   Path: `{path}`")
        lines.append(f"   Date: {ts} | Source: {row['source']}")
        if row["preview"]:
            preview = str(row["preview"])[:150].replace("\n", " ")
            lines.append(f"   Preview: {preview}")
        if row["description"]:
            lines.append(f"   Description: {row['description']}")
        lines.append("")
    return "\n".join(lines)
