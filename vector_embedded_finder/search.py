"""Search interface for natural language queries."""

from . import embedder, store


def search(
    query: str,
    n_results: int = 5,
    media_type: str | None = None,
) -> list[dict]:
    query_embedding = embedder.embed_query(query)

    where = None
    if media_type:
        where = {"media_category": media_type}

    raw = store.search(query_embedding, n_results=n_results, where=where)

    results = []
    for i in range(len(raw["ids"][0])):
        meta = raw["metadatas"][0][i]
        distance = raw["distances"][0][i]
        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        similarity = 1 - distance

        results.append({
            "id": raw["ids"][0][i],
            "similarity": round(similarity, 4),
            "file_path": meta.get("file_path", ""),
            "file_name": meta.get("file_name", ""),
            "media_category": meta.get("media_category", ""),
            "timestamp": meta.get("timestamp", ""),
            "description": meta.get("description", ""),
            "source": meta.get("source", ""),
            "preview": raw["documents"][0][i][:200] if raw["documents"][0][i] else "",
        })

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
