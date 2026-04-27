from __future__ import annotations

import os
import platform
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent.parent
load_dotenv(PROJECT_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


LEGACY_VEF_DIR = Path(os.environ.get("VEF_DIR", str(Path.home() / ".vef")))
RECALL_HOME = Path(
    os.environ.get("RECALL_HOME")
    or os.environ.get("VEF_DIR")
    or str(Path.home() / ".recall")
)

load_dotenv(LEGACY_VEF_DIR / ".env", override=False)
load_dotenv(RECALL_HOME / ".env", override=True)

DB_DIR = RECALL_HOME / "db"
HNSW_DIR = RECALL_HOME / "hnsw"
MODELS_DIR = RECALL_HOME / "models"
CACHE_DIR = RECALL_HOME / "cache"
LOG_DIR = RECALL_HOME / "logs"
CREDENTIALS_DIR = RECALL_HOME / "credentials"

SQLITE_PATH = DB_DIR / "recall.db"
MODEL_MANIFEST_PATH = MODELS_DIR / "manifest.json"
MIGRATION_STATUS_PATH = RECALL_HOME / "migration_status.json"
WATCHED_DIRS_FILE = RECALL_HOME / "watched_dirs.json"
PID_FILE = RECALL_HOME / "daemon.pid"
SOCKET_PATH = Path(
    os.environ.get("RECALL_SOCKET_PATH", str(RECALL_HOME / "recall.sock"))
)

# Backward-compatible aliases used throughout the existing repo.
VEF_DIR = RECALL_HOME
DEFAULT_VEF_DIR = LEGACY_VEF_DIR

LEGACY_DATA_DIR = Path(os.environ.get("VEF_DATA_DIR", str(PROJECT_DIR / "data")))
CHROMA_DIR = LEGACY_DATA_DIR / "chromadb"

EMBEDDING_PROVIDER = os.environ.get("RECALL_EMBEDDING_PROVIDER", "").strip().lower()
if not EMBEDDING_PROVIDER:
    EMBEDDING_PROVIDER = os.environ.get("VEF_EMBEDDING_PROVIDER", "local").strip().lower()
if EMBEDDING_PROVIDER not in {"local", "gemini", "ollama", "nim"}:
    EMBEDDING_PROVIDER = "local"

EMBEDDING_MODEL = os.environ.get(
    "RECALL_EMBEDDING_MODEL",
    os.environ.get("VEF_EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5"),
)
VISION_EMBEDDING_MODEL = os.environ.get(
    "RECALL_VISION_EMBEDDING_MODEL",
    "nomic-ai/nomic-embed-vision-v1.5",
)
RERANKER_MODEL = os.environ.get(
    "RECALL_RERANKER_MODEL",
    "cross-encoder/ms-marco-MiniLM-L6-v2",
)
EMBEDDING_DIMENSIONS = int(
    os.environ.get(
        "RECALL_EMBEDDING_DIMENSIONS",
        os.environ.get("VEF_EMBEDDING_DIMENSIONS", "768"),
    )
)
MAX_TEXT_TOKENS = 8192

OLLAMA_BASE_URL = os.environ.get(
    "VEF_OLLAMA_BASE_URL",
    os.environ.get("RECALL_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
).rstrip("/")
OLLAMA_EMBED_MODEL = os.environ.get("VEF_OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_EMBED_URL = os.environ.get(
    "VEF_OLLAMA_EMBED_URL",
    f"{OLLAMA_BASE_URL}/api/embeddings",
)

NIM_EMBED_URL = os.environ.get("VEF_NIM_EMBED_URL", "").strip()
NIM_EMBED_MODEL = os.environ.get("VEF_NIM_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5")

COLLECTION_NAME = os.environ.get("VEF_COLLECTION_NAME", "vector_embedded_finder")

SUPPORTED_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "document": {".pdf"},
    "text": {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".html",
        ".py",
        ".js",
        ".ts",
        ".go",
        ".rs",
        ".sh",
    },
}

ALL_EXTENSIONS: set[str] = set()
for exts in SUPPORTED_EXTENSIONS.values():
    ALL_EXTENSIONS.update(exts)

# Resource limits
MAX_CONCURRENT_INGEST = int(os.environ.get("VEF_CONCURRENCY", "10"))
CPU_GUARD_PERCENT = int(os.environ.get("RECALL_CPU_GUARD_PERCENT", "30"))
MIN_FREE_RAM_BYTES = 8 * 1024**3

# Daemon transport
DAEMON_HOST = os.environ.get("RECALL_COMPAT_HOST", "127.0.0.1")
DAEMON_PORT = int(os.environ.get("VEF_PORT", os.environ.get("RECALL_COMPAT_PORT", "19847")))
RECALL_ENABLE_COMPAT_HTTP = _env_bool("RECALL_ENABLE_COMPAT_HTTP", default=False)
RECALL_SOCKET_BASE_URL = "http://recall.local"

# Search/index flags
DUAL_WRITE_CHROMA = _env_bool("RECALL_DUAL_WRITE_CHROMA", default=True)
READ_FROM_CHROMA = _env_bool("RECALL_READ_FROM_CHROMA", default=False)
ENABLE_CLOUD_ENRICHMENT = _env_bool("RECALL_ENABLE_CLOUD_ENRICHMENT", default=False)
ENABLE_OPTIONAL_CAPTIONING = _env_bool("RECALL_ENABLE_OPTIONAL_CAPTIONING", default=True)
INDEX_REBUILD_BATCH = int(os.environ.get("RECALL_INDEX_REBUILD_BATCH", "500"))

# Connector sync intervals (seconds)
GMAIL_POLL_INTERVAL = 15 * 60
GCAL_POLL_INTERVAL = 30 * 60
CALAI_POLL_INTERVAL = 30 * 60
LMS_POLL_INTERVAL = 60 * 60
GDRIVE_POLL_INTERVAL = 30 * 60
NOTION_POLL_INTERVAL = 30 * 60
CONNECTOR_SYNC_BUDGET_S = float(os.environ.get("VEF_CONNECTOR_SYNC_BUDGET_S", "600"))

GMAIL_CREDENTIALS_FILE = CREDENTIALS_DIR / "gmail.json"
CANVAS_CREDENTIALS_FILE = CREDENTIALS_DIR / "canvas.json"
CALAI_CREDENTIALS_FILE = CREDENTIALS_DIR / "calai.json"
SCHOOLOGY_CREDENTIALS_FILE = CREDENTIALS_DIR / "schoology.json"
GDRIVE_CREDENTIALS_FILE = CREDENTIALS_DIR / "gdrive.json"
NOTION_CREDENTIALS_FILE = CREDENTIALS_DIR / "notion.json"


def get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError(
            "GEMINI_API_KEY not set. Add it to ~/.recall/.env or configure it in Recall."
        )
    return key


def get_nim_api_key() -> str:
    key = os.environ.get("NIM_API_KEY", "")
    if not key:
        raise ValueError("NIM_API_KEY not set. Add it to ~/.recall/.env.")
    return key


def get_media_category(ext: str) -> str | None:
    ext = ext.lower()
    for category, extensions in SUPPORTED_EXTENSIONS.items():
        if ext in extensions:
            return category
    return None


def ensure_vef_dirs() -> None:
    ensure_runtime_dirs()


def ensure_runtime_dirs() -> None:
    RECALL_HOME.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    HNSW_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists() and SOCKET_PATH.is_dir():
        raise RuntimeError(f"Socket path points to a directory: {SOCKET_PATH}")


def is_apple_silicon() -> bool:
    machine = platform.machine().lower()
    return machine in {"arm64", "aarch64"} and sys_platform_is_macos()


def sys_platform_is_macos() -> bool:
    return platform.system().lower() == "darwin"
