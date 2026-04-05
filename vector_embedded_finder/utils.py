import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from . import config


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def mime_type(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    return mt or "application/octet-stream"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_supported(path: Path) -> bool:
    # Skip macOS resource fork files (._filename)
    if path.name.startswith("._"):
        return False
    return path.suffix.lower() in config.ALL_EXTENSIONS


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)
