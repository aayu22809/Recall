"""Ranking utilities for blending retrieval signals."""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    *,
    k: int = 60,
) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


_RERANK_CACHE: OrderedDict[tuple[str, tuple[str, ...]], list[str]] = OrderedDict()
_RERANK_CACHE_SIZE = 128


def maybe_rerank(
    query: str,
    rows: list[dict[str, Any]],
    *,
    low_confidence_threshold: float = 0.15,
) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return rows

    top_scores = [float(row.get("similarity", 0.0)) for row in rows[:5]]
    if not top_scores or (max(top_scores) - min(top_scores)) >= low_confidence_threshold:
        return rows

    key = (query, tuple(str(row.get("id", "")) for row in rows[:20]))
    cached = _RERANK_CACHE.get(key)
    if cached is not None:
        index = {str(row.get("id", "")): row for row in rows}
        return [index[doc_id] for doc_id in cached if doc_id in index]

    # Lightweight local heuristic rerank until a heavyweight cross-encoder is
    # available: prefer rows whose caption/ocr/preview contain more query terms.
    terms = [part for part in query.lower().split() if part]

    def score(row: dict[str, Any]) -> tuple[float, float]:
        haystack = " ".join(
            str(row.get(field, ""))
            for field in ("file_name", "description", "preview")
        ).lower()
        meta = row.get("metadata", {})
        if isinstance(meta, dict):
            haystack += " " + " ".join(
                str(meta.get(field, ""))
                for field in ("caption", "ocr_text", "gps_city", "exif_camera")
            ).lower()
        overlap = sum(1 for term in terms if term in haystack)
        return (float(overlap), float(row.get("similarity", 0.0)))

    reranked = sorted(rows, key=score, reverse=True)
    _RERANK_CACHE[key] = [str(row.get("id", "")) for row in reranked[:20]]
    while len(_RERANK_CACHE) > _RERANK_CACHE_SIZE:
        _RERANK_CACHE.popitem(last=False)
    return reranked
