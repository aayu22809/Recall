from __future__ import annotations

from vector_embedded_finder.cli import build_parser


def test_parse_search_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["search", "hello world", "--n-results", "7"])
    assert args.command == "search"
    assert args.query == "hello world"
    assert args.n_results == 7


def test_parse_context_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["context", "my robotics projects"])
    assert args.command == "context"
    assert args.topic == "my robotics projects"


def test_parse_sync_no_source() -> None:
    parser = build_parser()
    args = parser.parse_args(["sync"])
    assert args.command == "sync"
    assert args.source is None


def test_parse_connect_gmail() -> None:
    parser = build_parser()
    args = parser.parse_args(["connect", "gmail"])
    assert args.command == "connect"
    assert args.source == "gmail"


def test_parse_index_path() -> None:
    parser = build_parser()
    args = parser.parse_args(["index", "/tmp/file.txt", "--source", "files"])
    assert args.command == "index"
    assert args.path == "/tmp/file.txt"
    assert args.source == "files"
