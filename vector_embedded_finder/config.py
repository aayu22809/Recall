from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

KEYCHAIN_SERVICE = "ai.recall.app"


def _resolve_secret(value: str | None) -> str | None:
    """Resolve a `keychain://<account>` placeholder to its plaintext value.

    The Tauri shell stores API keys and OAuth refresh tokens in the macOS
    Keychain under service=ai.recall.app and writes a placeholder string
    `keychain://<account>` to ~/.vef/.env so the daemon never has plaintext
    secrets on disk. Plain (non-placeholder) values pass through unchanged
    for backward compatibility with `.env` files predating the Tauri shell.
    """
    if not value or not isinstance(value, str):
        return value
    prefix = "keychain://"
    if not value.startswith(prefix):
        return value
    account = value[len(prefix):].strip()
    if not account:
        return None
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", account,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n") or None


def _env(key: str, default: str = "") -> str:
    """Read an env var, transparently resolving keychain:// placeholders."""
    raw = os.environ.get(key, default)
    resolved = _resolve_secret(raw)
    return resolved if resolved is not None else (default or "")


PROJECT_DIR = Path(__file__).parent.parent
load_dotenv(PROJECT_DIR / ".env")
DEFAULT_VEF_DIR = Path(os.environ.get("VEF_DIR", str(Path.home() / ".vef")))
load_dotenv(DEFAULT_VEF_DIR / ".env", override=True)

DATA_DIR = Path(os.environ.get("VEF_DATA_DIR", str(PROJECT_DIR / "data")))
CHROMA_DIR = DATA_DIR / "chromadb"

EMBEDDING_PROVIDER = os.environ.get("VEF_EMBEDDING_PROVIDER", "gemini").strip().lower()
EMBEDDING_MODEL = os.environ.get("VEF_EMBEDDING_MODEL", "gemini-embedding-2-preview")
EMBEDDING_DIMENSIONS = int(os.environ.get("VEF_EMBEDDING_DIMENSIONS", "768"))
MAX_TEXT_TOKENS = 8192

OLLAMA_BASE_URL = os.environ.get("VEF_OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_EMBED_MODEL = os.environ.get("VEF_OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_EMBED_URL = os.environ.get("VEF_OLLAMA_EMBED_URL", f"{OLLAMA_BASE_URL}/api/embeddings")

NIM_EMBED_URL = os.environ.get("VEF_NIM_EMBED_URL", "").strip()
NIM_EMBED_MODEL = os.environ.get("VEF_NIM_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5")

if "VEF_COLLECTION_NAME" in os.environ:
    COLLECTION_NAME = os.environ["VEF_COLLECTION_NAME"]
elif EMBEDDING_PROVIDER == "gemini":
    COLLECTION_NAME = "vector_embedded_finder"
elif EMBEDDING_PROVIDER == "ollama":
    COLLECTION_NAME = "vector_embedded_finder_ollama"
elif EMBEDDING_PROVIDER == "nim":
    COLLECTION_NAME = "vector_embedded_finder_nim"
else:
    COLLECTION_NAME = f"vector_embedded_finder_{EMBEDDING_PROVIDER}"

# Credentials and runtime state directory
VEF_DIR = Path(os.environ.get("VEF_DIR", str(DEFAULT_VEF_DIR)))
CREDENTIALS_DIR = VEF_DIR / "credentials"
PID_FILE = VEF_DIR / "daemon.pid"

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

# ── Resource limits ────────────────────────────────────────────────────────────

# Number of concurrent ingest workers in the thread pool
MAX_CONCURRENT_INGEST = int(os.environ.get("VEF_CONCURRENCY", "10"))

# Daemon HTTP port
DAEMON_PORT = int(os.environ.get("VEF_PORT", "19847"))
DAEMON_HOST = "127.0.0.1"

# CPU guard: back off if sustained CPU exceeds this percentage
CPU_GUARD_PERCENT = 30

# Memory guard: require at least this many bytes free before loading AI models
MIN_FREE_RAM_BYTES = 8 * 1024 ** 3  # 8 GB

# ── Connector sync intervals (seconds) ────────────────────────────────────────

GMAIL_POLL_INTERVAL = 15 * 60        # 15 minutes
GCAL_POLL_INTERVAL = 30 * 60         # 30 minutes
CALAI_POLL_INTERVAL = 30 * 60        # 30 minutes
LMS_POLL_INTERVAL = 60 * 60          # 60 minutes
GDRIVE_POLL_INTERVAL = 30 * 60       # 30 minutes
NOTION_POLL_INTERVAL = 30 * 60       # 30 minutes
CONNECTOR_SYNC_BUDGET_S = float(os.environ.get("VEF_CONNECTOR_SYNC_BUDGET_S", "600"))

# ── Connector credential files ─────────────────────────────────────────────────

GMAIL_CREDENTIALS_FILE = CREDENTIALS_DIR / "gmail.json"
CANVAS_CREDENTIALS_FILE = CREDENTIALS_DIR / "canvas.json"
CALAI_CREDENTIALS_FILE = CREDENTIALS_DIR / "calai.json"
SCHOOLOGY_CREDENTIALS_FILE = CREDENTIALS_DIR / "schoology.json"
GDRIVE_CREDENTIALS_FILE = CREDENTIALS_DIR / "gdrive.json"
NOTION_CREDENTIALS_FILE = CREDENTIALS_DIR / "notion.json"

# ── Watched directories (populated by setup wizard) ───────────────────────────

WATCHED_DIRS_FILE = VEF_DIR / "watched_dirs.json"


def get_api_key() -> str:
    key = _env("GEMINI_API_KEY")
    if not key:
        raise ValueError(
            "GEMINI_API_KEY not set. Add it to .env or set as environment variable."
        )
    return key


def get_nim_api_key() -> str:
    key = _env("NIM_API_KEY")
    if not key:
        raise ValueError(
            "NIM_API_KEY not set. Add it to .env or set as environment variable."
        )
    return key


def get_media_category(ext: str) -> str | None:
    ext = ext.lower()
    for category, extensions in SUPPORTED_EXTENSIONS.items():
        if ext in extensions:
            return category
    return None


def ensure_vef_dirs() -> None:
    """Create ~/.vef and subdirectories if they don't exist."""
    VEF_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
