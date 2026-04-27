"""File ingestion pipeline — local-first enrichment, embedding, and storage."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import psutil

from . import captioner, config, embedder, store, utils

logger = logging.getLogger(__name__)


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(text)
        return "\n".join(pages)
    except Exception as exc:
        logger.debug("pypdf extraction failed for %s: %s", path, exc)
        return ""


def _cpu_guard() -> None:
    while True:
        usage = psutil.cpu_percent(interval=0.5)
        if usage <= config.CPU_GUARD_PERCENT:
            return
        logger.debug("CPU %.0f%% > %d%%, backing off 5s", usage, config.CPU_GUARD_PERCENT)
        time.sleep(5)


def _set_low_priority() -> None:
    try:
        os.nice(10)
    except (AttributeError, PermissionError):
        pass


def _image_enrichment(path: Path, category: str) -> dict[str, Any]:
    enrichment: dict[str, Any] = {
        "caption": "",
        "ocr_text": "",
        "gps_city": "",
        "face_count": 0,
        "exif_date": "",
        "exif_camera": "",
    }
    if category != "image":
        return enrichment

    try:
        from PIL import Image

        image = Image.open(path)
        exif = getattr(image, "getexif", lambda: None)() or {}
        make = exif.get(271) or ""
        model = exif.get(272) or ""
        when = exif.get(36867) or exif.get(306) or ""
        enrichment["exif_camera"] = " ".join(part for part in (str(make).strip(), str(model).strip()) if part)
        enrichment["exif_date"] = str(when)
    except Exception as exc:
        logger.debug("EXIF extraction failed for %s: %s", path, exc)

    if config.ENABLE_OPTIONAL_CAPTIONING:
        try:
            caption = captioner.caption_file(path)
            if caption:
                enrichment["caption"] = caption
        except Exception as exc:
            logger.debug("Caption enrichment failed for %s: %s", path, exc)
    return enrichment


def _build_text_payload(
    path: Path,
    category: str,
    description: str,
    caption: str | None,
) -> tuple[list[float], str]:
    if category == "text":
        text = path.read_text(errors="replace")
        if len(text) > 32000:
            text = text[:32000]
        return embedder.embed_text(text), text[:5000]

    if category == "document":
        extracted_text = _extract_pdf_text(path)
        if extracted_text:
            if len(extracted_text) > 32000:
                extracted_text = extracted_text[:32000]
            return embedder.embed_text(extracted_text), extracted_text[:5000]
        fallback = description or f"PDF file {path.name}"
        return embedder.embed_pdf(path), fallback

    if category == "image":
        if caption:
            return embedder.embed_text(caption), caption
        return embedder.embed_image(path), description or f"Image file {path.name}"

    if category == "audio":
        if caption:
            return embedder.embed_text(caption), caption
        return embedder.embed_audio(path), description or f"Audio file {path.name}"

    if category == "video":
        if caption:
            return embedder.embed_text(caption), caption
        return embedder.embed_video(path), description or f"Video file {path.name}"

    raise ValueError(f"Unknown category: {category}")


def ingest_file(
    path: str | Path,
    source: str = "manual",
    description: str = "",
) -> dict[str, Any]:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not utils.is_supported(path):
        raise ValueError(f"Unsupported file type: {path.suffix}")

    category = config.get_media_category(path.suffix.lower())
    if category is None:
        raise ValueError(f"Unknown category for file: {path}")

    stat = path.stat()
    doc_id = utils.file_hash(path)
    if store.exists(doc_id):
        return {"status": "skipped", "reason": "already embedded", "id": doc_id, "path": str(path)}

    # Remove any previous active row for this path before adding the new content hash.
    store.delete_by_path(path)

    caption: str | None = None
    if category in {"image", "audio", "video"} and config.ENABLE_OPTIONAL_CAPTIONING:
        _cpu_guard()
        try:
            caption = captioner.caption_file(path)
        except Exception as exc:
            logger.debug("Caption generation failed for %s: %s", path, exc)

    embedding, document = _build_text_payload(path, category, description, caption)
    enrichment = _image_enrichment(path, category)
    if caption and not enrichment.get("caption"):
        enrichment["caption"] = caption

    metadata = {
        "file_path": str(path),
        "file_name": path.name,
        "file_type": utils.mime_type(path),
        "media_category": category,
        "timestamp": utils.now_iso(),
        "source": source,
        "description": caption or description,
        "file_size": stat.st_size,
        "mtime": stat.st_mtime,
        "sha256": doc_id,
    }

    store.add(doc_id, embedding, metadata, document=document, enrichment=enrichment)
    return {"status": "embedded", "id": doc_id, "path": str(path), "category": category}


def ingest_text(
    text: str,
    description: str = "",
    source: str = "manual",
    tags: str = "",
) -> dict[str, Any]:
    doc_id = utils.text_hash(text)
    if store.exists(doc_id):
        return {"status": "skipped", "reason": "already embedded", "id": doc_id}

    metadata = {
        "file_path": "",
        "file_name": description or "text snippet",
        "file_type": "text/plain",
        "media_category": "text",
        "timestamp": utils.now_iso(),
        "source": source,
        "description": description,
        "tags": tags,
        "file_size": len(text.encode()),
        "sha256": doc_id,
        "mtime": 0.0,
    }
    store.add(doc_id, embedder.embed_text(text), metadata, document=text[:5000], enrichment=None)
    return {"status": "embedded", "id": doc_id, "category": "text"}


def _ingest_worker(file_path: Path, source: str) -> dict[str, Any]:
    _set_low_priority()
    try:
        return ingest_file(file_path, source=source)
    except Exception as exc:
        return {"status": "error", "path": str(file_path), "error": str(exc)}


def ingest_directory(
    path: str | Path,
    source: str = "manual",
    recursive: bool = True,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    path = Path(path).expanduser().resolve()
    pattern = "**/*" if recursive else "*"
    files = [f for f in sorted(path.glob(pattern)) if f.is_file() and utils.is_supported(f)]
    total = len(files)
    results: list[dict[str, Any]] = [None] * total  # type: ignore[list-item]
    completed = 0

    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_INGEST) as pool:
        future_to_idx = {pool.submit(_ingest_worker, fp, source): i for i, fp in enumerate(files)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"status": "error", "path": str(files[idx]), "error": str(exc)}
            results[idx] = result
            completed += 1
            if progress_callback:
                try:
                    progress_callback(completed, total, result)
                except Exception:
                    pass
    return results
