from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = Path(os.environ.get("VEF_DATA_DIR", str(PROJECT_DIR / "data")))
CHROMA_DIR = DATA_DIR / "chromadb"
COLLECTION_NAME = "vector_embedded_finder"

SUPPORTED_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "document": {".pdf"},
    "text": {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".py", ".js", ".ts", ".go", ".rs", ".sh"},
}

ALL_EXTENSIONS = set()
for exts in SUPPORTED_EXTENSIONS.values():
    ALL_EXTENSIONS.update(exts)

EMBEDDING_MODEL = "gemini-embedding-2-preview"
EMBEDDING_DIMENSIONS = 768
MAX_TEXT_TOKENS = 8192


def get_api_key() -> str:
    load_dotenv(PROJECT_DIR / ".env")
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError(
            "GEMINI_API_KEY not set. Add it to .env or set as environment variable."
        )
    return key


def get_media_category(ext: str) -> str | None:
    ext = ext.lower()
    for category, extensions in SUPPORTED_EXTENSIONS.items():
        if ext in extensions:
            return category
    return None
