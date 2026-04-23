"""File ingestion pipeline — detect type, caption, embed, store."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import psutil

from . import config, embedder, store, utils

logger = logging.getLogger(__name__)


def _extract_pdf_text(path: Path) -> str:
    """Extract plain text from a PDF using pypdf. Returns empty string on failure."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            t = page.extract_text() or ""
            stripped = t.strip()
            if stripped:
                pages.append(stripped)
        return "\n".join(pages)
    except Exception as e:
        logger.debug("pypdf extraction failed for %s: %s", path, e)
        return ""


def _cpu_guard() -> None:
    """Back off until CPU usage is below the guard threshold."""
    while True:
        usage = psutil.cpu_percent(interval=0.5)
        if usage <= config.CPU_GUARD_PERCENT:
            break
        logger.debug("CPU %.0f%% > %d%%, backing off 5s", usage, config.CPU_GUARD_PERCENT)
        time.sleep(5)


def _set_low_priority() -> None:
    """Lower OS scheduling priority for the current thread."""
    try:
        os.nice(10)
    except (AttributeError, PermissionError):
        pass


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

    caption: str | None = None

    if category in ("image", "audio", "video"):
        _cpu_guard()
        try:
            from . import captioner
            caption = captioner.caption_file(path)
        except Exception as e:
            logger.debug("captioner import/call failed for %s: %s", path, e)

    if category == "text":
        text = path.read_text(errors="replace")
        if len(text) > 32000:
            text = text[:32000]
        embedding = embedder.embed_text(text)
        doc_text = text[:500]
    elif category == "image":
        if caption:
            embedding = embedder.embed_text(caption)
            doc_text = caption[:500]
        else:
            embedding = embedder.embed_image(path)
            doc_text = description or f"Image: {path.name}"
    elif category == "audio":
        if caption:
            embedding = embedder.embed_text(caption)
            doc_text = caption[:500]
        else:
            embedding = embedder.embed_audio(path)
            doc_text = description or f"Audio: {path.name}"
    elif category == "video":
        if caption:
            embedding = embedder.embed_text(caption)
            doc_text = caption[:500]
        else:
            embedding = embedder.embed_video(path)
            doc_text = description or f"Video: {path.name}"
    elif category == "document":
        # Prefer text extraction: semantically searchable + far smaller API payload.
        extracted_text = _extract_pdf_text(path)
        if extracted_text:
            if len(extracted_text) > 32000:
                extracted_text = extracted_text[:32000]
            embedding = embedder.embed_text(extracted_text)
            doc_text = extracted_text[:500]
        else:
            # Fallback: binary PDF embedding (scanned documents, image-only PDFs)
            embedding = embedder.embed_pdf(path)
            doc_text = description or f"PDF: {path.name}"
    else:
        raise ValueError(f"Unknown category: {category}")

    effective_description = caption or description

    metadata = {
        "file_path": str(path),
        "file_name": path.name,
        "file_type": utils.mime_type(path),
        "media_category": category,
        "timestamp": utils.now_iso(),
        "source": source,
        "description": effective_description,
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


def _ingest_worker(file_path: Path, source: str) -> dict:
    """Worker function run inside the thread pool."""
    _set_low_priority()
    try:
        return ingest_file(file_path, source=source)
    except Exception as e:
        return {"status": "error", "path": str(file_path), "error": str(e)}


def ingest_directory(
    path: str | Path,
    source: str = "manual",
    recursive: bool = True,
    progress_callback: Callable[[int, int, dict], None] | None = None,
) -> list[dict]:
    path = Path(path).resolve()
    pattern = "**/*" if recursive else "*"

    files = [f for f in sorted(path.glob(pattern)) if f.is_file() and utils.is_supported(f)]
    total = len(files)
    results: list[dict] = [None] * total  # type: ignore[list-item]
    completed = 0

    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_INGEST) as pool:
        future_to_idx = {
            pool.submit(_ingest_worker, fp, source): i
            for i, fp in enumerate(files)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
            except Exception as e:
                result = {"status": "error", "path": str(files[idx]), "error": str(e)}

            results[idx] = result
            completed += 1

            if progress_callback:
                try:
                    progress_callback(completed, total, result)
                except Exception:
                    pass

    return results
