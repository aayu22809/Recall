"""Vector Embedded Finder - local multimodal memory with semantic search."""

from __future__ import annotations

from typing import Any

__all__ = ["search", "ingest_file", "ingest_text", "ingest_directory", "count", "delete", "list_all"]


def search(*args: Any, **kwargs: Any):
    from .search import search as _search

    return _search(*args, **kwargs)


def ingest_file(*args: Any, **kwargs: Any):
    from .ingest import ingest_file as _ingest_file

    return _ingest_file(*args, **kwargs)


def ingest_text(*args: Any, **kwargs: Any):
    from .ingest import ingest_text as _ingest_text

    return _ingest_text(*args, **kwargs)


def ingest_directory(*args: Any, **kwargs: Any):
    from .ingest import ingest_directory as _ingest_directory

    return _ingest_directory(*args, **kwargs)


def count(*args: Any, **kwargs: Any):
    from .store import count as _count

    return _count(*args, **kwargs)


def delete(*args: Any, **kwargs: Any):
    from .store import delete as _delete

    return _delete(*args, **kwargs)


def list_all(*args: Any, **kwargs: Any):
    from .store import list_all as _list_all

    return _list_all(*args, **kwargs)
