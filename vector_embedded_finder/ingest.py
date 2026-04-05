"""File ingestion pipeline - detect type, embed, store."""

from __future__ import annotations

from pathlib import Path

from . import config, embedder, store, utils


def ingest_file(
    path: str | Path,
    source: str = "manual",
    description: str = "",
) -> dict:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if not utils.is_supported(path):
        raise ValueError(f"Unsupported file type: {path.suffix}")

    category = config.get_media_category(path.suffix.lower())
    doc_id = utils.file_hash(path)

    if store.exists(doc_id):
        return {"status": "skipped", "reason": "already embedded", "id": doc_id, "path": str(path)}

    if category == "text":
        text = path.read_text(errors="replace")
        if len(text) > 32000:
            text = text[:32000]
        embedding = embedder.embed_text(text)
        doc_text = text[:500]
    elif category == "image":
        embedding = embedder.embed_image(path)
        doc_text = description or f"Image: {path.name}"
    elif category == "audio":
        embedding = embedder.embed_audio(path)
        doc_text = description or f"Audio: {path.name}"
    elif category == "video":
        embedding = embedder.embed_video(path)
        doc_text = description or f"Video: {path.name}"
    elif category == "document":
        embedding = embedder.embed_pdf(path)
        doc_text = description or f"PDF: {path.name}"
    else:
        raise ValueError(f"Unknown category: {category}")

    metadata = {
        "file_path": str(path),
        "file_name": path.name,
        "file_type": utils.mime_type(path),
        "media_category": category,
        "timestamp": utils.now_iso(),
        "source": source,
        "description": description,
        "file_size": path.stat().st_size,
    }

    store.add(doc_id, embedding, metadata, document=doc_text)
    return {"status": "embedded", "id": doc_id, "path": str(path), "category": category}


def ingest_text(
    text: str,
    description: str = "",
    source: str = "manual",
    tags: str = "",
) -> dict:
    doc_id = utils.text_hash(text)

    if store.exists(doc_id):
        return {"status": "skipped", "reason": "already embedded", "id": doc_id}

    embedding = embedder.embed_text(text)

    metadata = {
        "file_path": "",
        "file_name": "",
        "file_type": "text/plain",
        "media_category": "text",
        "timestamp": utils.now_iso(),
        "source": source,
        "description": description,
        "tags": tags,
        "file_size": len(text.encode()),
    }

    store.add(doc_id, embedding, metadata, document=text[:500])
    return {"status": "embedded", "id": doc_id, "category": "text"}


def ingest_directory(
    path: str | Path,
    source: str = "manual",
    recursive: bool = True,
    progress_callback: callable | None = None,
) -> list[dict]:
    path = Path(path).resolve()
    results = []
    pattern = "**/*" if recursive else "*"

    files = [f for f in sorted(path.glob(pattern)) if f.is_file() and utils.is_supported(f)]
    total = len(files)

    for i, file_path in enumerate(files, 1):
        try:
            result = ingest_file(file_path, source=source)
            results.append(result)

            if progress_callback:
                progress_callback(i, total, result)
        except Exception as e:
            err = {"status": "error", "path": str(file_path), "error": str(e)}
            results.append(err)
            if progress_callback:
                progress_callback(i, total, err)
    return results
