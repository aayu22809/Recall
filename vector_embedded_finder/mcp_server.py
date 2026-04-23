"""MCP server exposing Recall search/context tools."""

from __future__ import annotations

try:
    from fastmcp import FastMCP
except Exception as exc:  # pragma: no cover - import guard for optional runtime
    raise RuntimeError(
        "fastmcp is required to run recall-mcp. Install with: pip install fastmcp"
    ) from exc

from .search import format_results, search

mcp = FastMCP("recall")


@mcp.tool()
def search_memory(query: str, n_results: int = 10) -> str:
    """Search across indexed files, email, calendar, and connected sources."""
    results = search(query, n_results=n_results)
    return format_results(results)


@mcp.tool()
def get_context(topic: str, n_results: int = 5) -> str:
    """Get personal context snippets for AI assistant priming."""
    results = search(topic, n_results=n_results)
    return format_results(results)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
