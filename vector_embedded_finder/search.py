"""Search interface for natural language queries."""

from __future__ import annotations

import os
import re
import logging
from datetime import datetime, timedelta, timezone

from . import embedder, store
from .reranker import reciprocal_rank_fusion

logger = logging.getLogger(__name__)

MIN_SIMILARITY = float(os.environ.get("VEF_MIN_SIMILARITY", "0.45"))
RRF_K = int(os.environ.get("VEF_RRF_K", "60"))

_MEDIA_KEYWORDS = {
    "image": {"image", "photo", "picture", "screenshot"},
    "video": {"video", "clip", "recording"},
    "audio": {"audio", "sound", "voice", "meeting recording"},
    "email": {"email", "gmail", "thread", "message"},
    "document": {"pdf", "doc", "document", "paper", "slides", "deck"},
}


def _tokenize_query(query: str) -> set[str]:
    tokens = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1}
    return tokens


def _keyword_boost(result: dict, query: str) -> float:
    media_category = str(result.get("media_category", "")).lower()
    if media_category not in {"text", "document"}:
        return 0.0

    words = _tokenize_query(query)
    if not words:
        return 0.0

    text = f"{result.get('file_name', '')} {result.get('description', '')}".lower()
    matches = sum(1 for w in words if w in text)
    return min(0.15 * matches / max(len(words), 1), 0.15)


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


def _build_results(raw: dict) -> list[dict]:
    results: list[dict] = []
    if not raw.get("ids") or not raw["ids"] or not raw["ids"][0]:
        return results

    for i in range(len(raw["ids"][0])):
        meta = raw["metadatas"][0][i]
        distance = raw["distances"][0][i]
        similarity = 1 - distance
        results.append(
            {
                "id": raw["ids"][0][i],
                "similarity": round(similarity, 4),
                "file_path": meta.get("file_path", ""),
                "file_name": meta.get("file_name", ""),
                "media_category": meta.get("media_category", ""),
                "timestamp": meta.get("timestamp", ""),
                "description": meta.get("description", ""),
                "source": meta.get("source", ""),
                "preview": raw["documents"][0][i][:200] if raw["documents"][0][i] else "",
                "metadata": {k: v for k, v in meta.items()},
            }
        )
    return results


def search(
    query: str,
    n_results: int = 20,
    media_type: str | None = None,
    sources: list[str] | None = None,
) -> list[dict]:
    try:
        query_embedding = embedder.embed_query(query)
    except Exception as exc:
        logger.warning("Search embedding failed for query %r: %s", query, exc)
        return []

    where: dict | None = None
    filters: list[dict] = []

    inferred_media = _detect_media_intent(query) if not media_type else None
    if media_type:
        filters.append({"media_category": {"$eq": media_type}})
    elif inferred_media:
        filters.append({"media_category": {"$eq": inferred_media}})

    inferred_sources = _detect_source_intent(query) if not sources else None
    source_filters = sources or inferred_sources
    if source_filters:
        if len(source_filters) == 1:
            filters.append({"source": {"$eq": source_filters[0]}})
        else:
            filters.append({"source": {"$in": source_filters}})

    since_cutoff = _detect_time_cutoff(query)
    if since_cutoff:
        filters.append({"timestamp": {"$gte": since_cutoff}})

    if len(filters) == 1:
        where = filters[0]
    elif len(filters) > 1:
        where = {"$and": filters}

    vector_raw = store.search(query_embedding, n_results=n_results, where=where)
    vector_results = _build_results(vector_raw)

    keyword_token = ""
    query_tokens = sorted(_tokenize_query(query), key=len, reverse=True)
    for token in query_tokens:
        if len(token) >= 3:
            keyword_token = token
            break

    keyword_results: list[dict] = []
    if keyword_token:
        keyword_raw = store.search(
            query_embedding,
            n_results=n_results,
            where=where,
            where_document={"$contains": keyword_token},
        )
        keyword_results = _build_results(keyword_raw)

    by_id: dict[str, dict] = {r["id"]: r for r in vector_results}
    for row in keyword_results:
        existing = by_id.get(row["id"])
        if existing is None or float(row["similarity"]) > float(existing["similarity"]):
            by_id[row["id"]] = row

    if keyword_results:
        fused_ids = reciprocal_rank_fusion(
            [
                [r["id"] for r in vector_results],
                [r["id"] for r in keyword_results],
            ],
            k=RRF_K,
        )
    else:
        fused_ids = [r["id"] for r in vector_results]

    fused_len = max(len(fused_ids), 1)
    for idx, doc_id in enumerate(fused_ids):
        row = by_id.get(doc_id)
        if not row:
            continue
        rrf_bonus = max(0.0, 0.08 * (1 - (idx / fused_len)))
        boosted = min(
            1.0,
            float(row["similarity"]) + _keyword_boost(row, query) + rrf_bonus,
        )
        row["similarity"] = round(boosted, 4)

    results = [by_id[doc_id] for doc_id in fused_ids if doc_id in by_id]
    results = [r for r in results if float(r["similarity"]) >= MIN_SIMILARITY]
    results.sort(key=lambda r: float(r["similarity"]), reverse=True)
    return results


def format_results(results: list[dict]) -> str:
    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        score_pct = f"{r['similarity'] * 100:.1f}%"
        path = r["file_path"] or "(text snippet)"
        category = r["media_category"]
        ts = r["timestamp"][:10] if r["timestamp"] else "unknown"

        lines.append(f"**{i}. [{category}] {r['file_name'] or 'text'}** — {score_pct} match")
        if path:
            lines.append(f"   Path: `{path}`")
        lines.append(f"   Date: {ts} | Source: {r['source']}")
        if r["preview"]:
            preview = r["preview"][:150].replace("\n", " ")
            lines.append(f"   Preview: {preview}")
        if r["description"]:
            lines.append(f"   Description: {r['description']}")
        lines.append("")

    return "\n".join(lines)
