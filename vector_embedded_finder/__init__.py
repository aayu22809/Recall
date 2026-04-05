"""Vector Embedded Finder - local multimodal memory with semantic search."""

from .search import search
from .ingest import ingest_file, ingest_text, ingest_directory
from .store import count, delete, list_all

__all__ = ["search", "ingest_file", "ingest_text", "ingest_directory", "count", "delete", "list_all"]
