"""Recall CLI commands."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import config
from .search import format_results, search

try:
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
    from rich.text import Text

    _RICH_AVAILABLE = True
except Exception:
    _RICH_AVAILABLE = False

# Recall accent colour
_ACCENT = "#d97757"
_DIM = "#737373"


def _console() -> Console | None:
    return Console() if _RICH_AVAILABLE else None


def _daemon_base_url() -> str:
    return f"http://{config.DAEMON_HOST}:{config.DAEMON_PORT}"


def _render_results(results: list[dict[str, Any]]) -> None:
    text = format_results(results)
    console = _console()
    if console is None:
        print(text)
        return
    console.print(Markdown(text))


def _run_daemon_command(args: list[str]) -> int:
    cmd = [sys.executable, "-m", "vector_embedded_finder.daemon", *args]
    proc = subprocess.run(cmd)
    return proc.returncode


def _fetch_json(path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    url = f"{_daemon_base_url()}{path}"
    with httpx.Client(timeout=timeout) as client:
        if method == "GET":
            resp = client.get(url)
        else:
            resp = client.post(url, json=payload or {})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected response shape from {path}")
    return data


def _cmd_start(_: argparse.Namespace) -> int:
    return _run_daemon_command(["start"])


def _cmd_stop(_: argparse.Namespace) -> int:
    return _run_daemon_command(["stop"])


def _fmt_last_sync(value: Any) -> str:
    if not value:
        return "never"
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        return "unknown"
    delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    sec = int(max(delta.total_seconds(), 0))
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def _cmd_status(_: argparse.Namespace) -> int:
    pid = None
    if config.PID_FILE.exists():
        try:
            pid = int(config.PID_FILE.read_text().strip())
        except Exception:
            pid = None

    try:
        progress = _fetch_json("/progress")
        connector_status = _fetch_json("/connector-status")
    except Exception as exc:
        print(f"Daemon status check failed: {exc}")
        print("Run: recall start")
        return 1

    # /stats reports count. /health no longer includes it (liveness-only).
    try:
        stats = _fetch_json("/stats")
    except Exception:
        stats = {}

    docs = int(stats.get("count", 0))
    indexing = bool(progress.get("indexing", False))
    queued = int(progress.get("queued", 0))

    console = _console()
    daemon_line = f"Daemon running (pid {pid if pid else 'unknown'}, {docs:,} docs)"
    indexing_line = f"Indexing {'active' if indexing else 'idle'}"
    if indexing:
        indexing_line += f" (queued {queued})"

    if console is None:
        print(daemon_line)
        print(indexing_line)
        for name in sorted(connector_status):
            row = connector_status.get(name, {})
            authed = bool(row.get("authenticated"))
            print(
                f"{name}: {'authed' if authed else 'not authed'} | last sync {_fmt_last_sync(row.get('last_sync_iso'))}"
            )
        return 0

    console.print(f"[bold green]{daemon_line}[/]")
    console.print(indexing_line)
    table = Table(title="Connector Status")
    table.add_column("Connector")
    table.add_column("Auth")
    table.add_column("Last Sync")
    for name in sorted(connector_status):
        row = connector_status.get(name, {})
        authed = bool(row.get("authenticated"))
        table.add_row(name, "✔ authed" if authed else "✗ not authed", _fmt_last_sync(row.get("last_sync_iso")))
    console.print(table)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    results = search(args.query, n_results=args.n_results)
    _render_results(results)
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    results = search(args.topic, n_results=args.n_results)
    _render_results(results)
    return 0


def _build_sync_panel(
    connector_status: dict[str, Any],
    progress_data: dict[str, Any],
    elapsed: float,
    done: bool,
) -> Any:
    """Build a Rich renderable for live sync display."""
    if not _RICH_AVAILABLE:
        return None

    docs = int(progress_data.get("total_indexed", 0))
    indexing = bool(progress_data.get("indexing", False))
    queued = int(progress_data.get("queued", 0))

    # Header line
    state = "[bold green]done[/]" if done else (f"[bold {_ACCENT}]syncing…[/]" if indexing else f"[{_DIM}]waiting…[/]")
    header = Text()
    header.append(f"  {docs:,} docs  ", style="bold")
    header.append(f"elapsed {int(elapsed)}s  ", style=_DIM)
    if queued:
        header.append(f"queued {queued}  ", style=f"bold {_ACCENT}")
    header.append(state)

    # Connector table
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", width=12)
    table.add_column(width=14)
    table.add_column(style="dim")

    for name in sorted(connector_status):
        row = connector_status[name]
        authed = bool(row.get("authenticated"))
        last = _fmt_last_sync(row.get("last_sync_iso"))
        auth_text = f"[bold {_ACCENT}]✔[/] authed" if authed else f"[{_DIM}]✗ not authed[/]"
        table.add_row(f"  {name}", auth_text, last)

    return Panel(
        Text.assemble(header, "\n\n") if not connector_status else
        Panel.__new__(Panel),  # replaced below
        title=f"[bold {_ACCENT}]  RECALL SYNC  [/]",
        border_style=_ACCENT,
        padding=(1, 2),
    )


def _render_sync_live(connector_status: dict[str, Any], progress_data: dict[str, Any], elapsed: float, done: bool) -> Any:
    """Build Rich renderable grid for Live display."""
    docs = int(progress_data.get("total_indexed", 0))
    indexing = bool(progress_data.get("indexing", False))
    queued = int(progress_data.get("queued", 0))

    state_str = "done" if done else ("syncing…" if indexing else "waiting…")
    state_style = "bold green" if done else (f"bold {_ACCENT}" if indexing else _DIM)

    from rich.columns import Columns

    # Status line
    top = Text()
    top.append(f"  {docs:,} docs indexed", style="bold")
    top.append("  ·  ", style=_DIM)
    top.append(f"{int(elapsed)}s elapsed", style=_DIM)
    if queued:
        top.append(f"  ·  {queued} queued", style=f"bold {_ACCENT}")
    top.append("  ·  ", style=_DIM)
    top.append(state_str, style=state_style)

    # Connector rows
    table = Table(box=None, padding=(0, 1), show_header=False)
    table.add_column(style=_DIM, width=12)
    table.add_column(width=16)
    table.add_column(style=_DIM)

    for name in sorted(connector_status):
        row = connector_status[name]
        authed = bool(row.get("authenticated"))
        last = _fmt_last_sync(row.get("last_sync_iso"))
        dot = f"[bold {_ACCENT}]●[/]" if authed else f"[{_DIM}]○[/]"
        table.add_row(f"  {dot} {name}", "authed" if authed else "not authed", last)

    return Panel(
        Text.assemble(top, "\n\n", "") if not connector_status else top,
        subtitle=table if connector_status else None,
        title=f"[bold {_ACCENT}]  RECALL — syncing  [/]",
        border_style=_ACCENT,
        padding=(1, 2),
    )


def _print_sync_table(sync_results: dict[str, Any]) -> None:
    if not sync_results:
        return
    if _RICH_AVAILABLE:
        console = Console()
        table = Table(title="Sync Results", box=None, padding=(0, 2))
        table.add_column("Connector", style=_DIM)
        table.add_column("Status")
        table.add_column("Embedded", justify="right")
        table.add_column("Note", style=_DIM)
        for name in sorted(sync_results):
            r = sync_results[name]
            st = r.get("status", "?")
            embedded = str(r.get("embedded", "")) if st == "ok" else ""
            note = r.get("reason", r.get("error", "")[:60]) if st != "ok" else f"of {r.get('total', 0)}"
            colour = "green" if st == "ok" else (_ACCENT if st == "skipped" else "red")
            table.add_row(name, f"[{colour}]{st}[/]", embedded, note)
        console.print(table)
    else:
        print(json.dumps(sync_results, indent=2))


def _cmd_sync(args: argparse.Namespace) -> int:
    payload = {"source": args.source} if args.source else {}

    # POST returns instantly — daemon starts sync in background or reports in_progress.
    try:
        data = _fetch_json("/sync", method="POST", payload=payload, timeout=10.0)
    except Exception as exc:
        print(f"Sync error: {exc}", file=sys.stderr)
        return 1

    sync_status = data.get("status", "started")  # "started" | "in_progress"
    label_init = "syncing connectors…" if sync_status == "started" else "waiting for sync…"
    start = time.monotonic()

    if not _RICH_AVAILABLE:
        print(label_init, end="", flush=True)
        while True:
            time.sleep(3)
            try:
                if not _fetch_json("/sync-running", timeout=5.0).get("running"):
                    break
            except Exception:
                pass
            print(".", end="", flush=True)
        print(f" done ({int(time.monotonic() - start)}s)")
        _print_sync_table(data.get("last_sync", {}))
        return 0

    with Progress(
        SpinnerColumn(spinner_name="dots", style=f"bold {_ACCENT}"),
        BarColumn(
            bar_width=28,
            style=_DIM,
            complete_style=f"bold {_ACCENT}",
            finished_style="bold green",
            pulse_style=f"bold {_ACCENT}",
        ),
        TextColumn("[bold]{task.description}[/bold]"),
        TimeElapsedColumn(),
        console=Console(stderr=False),
        refresh_per_second=8,
        transient=False,
    ) as prog:
        bar_task = prog.add_task(f"[{_ACCENT}]{label_init}[/]", total=None)
        docs_prev = 0
        idle_polls = 0

        while True:
            time.sleep(1.5)
            try:
                progress_data = _fetch_json("/progress", timeout=3.0)
                docs = int(progress_data.get("total_indexed", 0))
                new_docs = docs - docs_prev
                docs_prev = docs
                suffix = f"[{_DIM}]{docs:,} docs[/]"
                if new_docs > 0:
                    suffix += f"  [bold {_ACCENT}]+{new_docs}[/]"
                running = _fetch_json("/sync-running", timeout=3.0).get("running", True)
                if not running:
                    idle_polls += 1
                else:
                    idle_polls = 0
                label = "syncing…" if running else "finishing…"
                prog.update(bar_task, description=f"[{_ACCENT}]{label}[/]  {suffix}")
                if idle_polls >= 2:
                    break
            except Exception:
                idle_polls += 1
                if idle_polls >= 4:
                    break

        elapsed = time.monotonic() - start
        prog.update(
            bar_task, total=1, completed=1,
            description=f"[bold green]done[/]  [{_DIM}]{docs_prev:,} docs · {int(elapsed)}s[/]",
        )

    # Show last sync result
    try:
        fresh = _fetch_json("/sync", method="POST", payload=payload, timeout=10.0)
        _print_sync_table(fresh.get("last_sync", {}))
    except Exception:
        pass

    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"Path not found: {path}", file=sys.stderr)
        return 1

    if path.is_file():
        payload = {"path": str(path), "source": args.source, "description": args.description}
        try:
            data = _fetch_json("/ingest", method="POST", payload=payload)
        except Exception as exc:
            print(f"File ingest failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(data, indent=2))
        return 0

    script = (
        "from vector_embedded_finder.ingest import ingest_directory\n"
        "import sys, json\n"
        "p = sys.argv[1]\n"
        "src = sys.argv[2]\n"
        "rows = ingest_directory(p, source=src)\n"
        "embedded = sum(1 for r in rows if r.get('status') == 'embedded')\n"
        "skipped = sum(1 for r in rows if r.get('status') == 'skipped')\n"
        "errors = sum(1 for r in rows if r.get('status') == 'error')\n"
        "print(json.dumps({'total': len(rows), 'embedded': embedded, 'skipped': skipped, 'errors': errors}, indent=2))\n"
    )
    env = os.environ.copy()
    project_root = str(config.PROJECT_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{project_root}:{existing}" if existing else project_root

    result_holder: dict[str, Any] = {}

    def _do_index() -> None:
        proc = subprocess.run(
            [sys.executable, "-c", script, str(path), args.source],
            env=env,
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        result_holder["returncode"] = proc.returncode
        result_holder["stdout"] = proc.stdout
        result_holder["stderr"] = proc.stderr

    thread = threading.Thread(target=_do_index, daemon=True)
    thread.start()
    start = time.monotonic()

    if not _RICH_AVAILABLE:
        print(f"Indexing {path}", end="", flush=True)
        while thread.is_alive():
            time.sleep(2)
            print(".", end="", flush=True)
        print()
    else:
        with Progress(
            SpinnerColumn(spinner_name="dots", style=f"bold {_ACCENT}"),
            BarColumn(
                bar_width=28,
                style=_DIM,
                complete_style=f"bold {_ACCENT}",
                finished_style="bold green",
                pulse_style=f"bold {_ACCENT}",
            ),
            TextColumn("[bold]{task.description}[/bold]"),
            TimeElapsedColumn(),
            console=Console(stderr=False),
            refresh_per_second=8,
            transient=False,
        ) as prog:
            bar_task = prog.add_task(f"[{_ACCENT}]indexing {path.name}…[/]", total=None)
            while thread.is_alive():
                time.sleep(1.5)
            elapsed = time.monotonic() - start
            prog.update(bar_task, total=1, completed=1, description=f"[bold green]done[/]  [{_DIM}]{int(elapsed)}s[/]")

    thread.join()

    rc = result_holder.get("returncode", 1)
    if result_holder.get("stderr"):
        print(result_holder["stderr"], file=sys.stderr, end="")
    if result_holder.get("stdout"):
        try:
            data = json.loads(result_holder["stdout"])
            if _RICH_AVAILABLE:
                console = Console()
                table = Table(box=None, padding=(0, 2))
                table.add_column("Metric", style=_DIM)
                table.add_column("Count", justify="right")
                for k, v in data.items():
                    colour = "green" if k == "embedded" else ("red" if k == "errors" and v else _DIM)
                    table.add_row(k, f"[{colour}]{v}[/]")
                console.print(table)
            else:
                print(result_holder["stdout"])
        except Exception:
            print(result_holder["stdout"])
    return rc


def _cmd_connect(args: argparse.Namespace) -> int:
    source = args.source.strip().lower()
    mapping: dict[str, tuple[str, str]] = {
        "gmail": ("vector_embedded_finder.connectors.gmail", "GmailConnector"),
        "gcal": ("vector_embedded_finder.connectors.gcal", "GCalConnector"),
        "gdrive": ("vector_embedded_finder.connectors.gdrive", "GDriveConnector"),
        "notion": ("vector_embedded_finder.connectors.notion", "NotionConnector"),
        "canvas": ("vector_embedded_finder.connectors.canvas", "CanvasConnector"),
        "calai": ("vector_embedded_finder.connectors.calai", "CalAIConnector"),
        "schoology": ("vector_embedded_finder.connectors.schoology", "SchoologyConnector"),
    }
    if source not in mapping:
        print(f"Unsupported source '{source}'.", file=sys.stderr)
        print(f"Supported: {', '.join(sorted(mapping))}", file=sys.stderr)
        return 1

    module_name, class_name = mapping[source]
    module = __import__(module_name, fromlist=[class_name])
    connector_cls = getattr(module, class_name)
    connector = connector_cls()
    connector.authenticate()
    if connector.is_authenticated():
        print(f"{source} connected.")
        return 0
    print(f"{source} authentication did not complete.", file=sys.stderr)
    return 1


def _cmd_open_memory(args: argparse.Namespace) -> int:
    results = search(args.query, n_results=1)
    if not results:
        print("No matching result found.")
        return 1

    row = results[0]
    file_path = str(row.get("file_path", ""))
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    source = str(row.get("source", ""))

    if file_path and "://" not in file_path and Path(file_path).exists():
        webbrowser.open(f"file://{file_path}")
        print(f"Opened file: {file_path}")
        return 0

    if source == "gmail" and metadata.get("thread_id"):
        webbrowser.open(f"https://mail.google.com/mail/u/0/#all/{metadata['thread_id']}")
        print("Opened Gmail thread.")
        return 0

    if source == "gcal" and metadata.get("event_id"):
        webbrowser.open(f"https://calendar.google.com/calendar/r/eventedit/{metadata['event_id']}")
        print("Opened Google Calendar event.")
        return 0

    if isinstance(metadata.get("url"), str) and metadata["url"]:
        webbrowser.open(metadata["url"])
        print(f"Opened URL: {metadata['url']}")
        return 0

    print(format_results(results))
    return 0


def _cmd_doctor(_: argparse.Namespace) -> int:
    """Health-check the Recall setup and print a status report."""
    console = _console()
    checks: list[tuple[str, bool, str]] = []
    daemon_ok = False

    # 1. Daemon reachable
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(f"{_daemon_base_url()}/health")
        daemon_ok = r.status_code == 200
    except Exception:
        daemon_ok = False
    checks.append(("Daemon reachable", daemon_ok, "Run: recall start"))

    # 2. Embedding provider configured
    provider = (os.environ.get("VEF_EMBEDDING_PROVIDER") or "").strip().lower()
    if not provider:
        try:
            from dotenv import dotenv_values
            for candidate in [config.VEF_DIR / ".env", config.PROJECT_DIR / ".env"]:
                if candidate.exists():
                    provider = (dotenv_values(candidate).get("VEF_EMBEDDING_PROVIDER") or "").strip().lower()
                    if provider:
                        break
        except Exception:
            pass
    provider = provider or "gemini"

    embed_ok = False
    embed_hint = ""
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            try:
                from dotenv import dotenv_values
                for candidate in [config.VEF_DIR / ".env", config.PROJECT_DIR / ".env"]:
                    if candidate.exists():
                        key = dotenv_values(candidate).get("GEMINI_API_KEY", "") or ""
                        if key:
                            break
            except Exception:
                pass
        embed_ok = bool(key)
        embed_hint = "Set GEMINI_API_KEY in ~/.vef/.env — get a free key at https://aistudio.google.com/apikey"
    elif provider == "ollama":
        ollama_url = os.environ.get("VEF_OLLAMA_BASE_URL", "http://localhost:11434")
        try:
            with httpx.Client(timeout=3.0) as client:
                client.get(f"{ollama_url}/api/tags")
            embed_ok = True
        except Exception:
            embed_ok = False
            embed_hint = "Ollama not running — brew install ollama && brew services start ollama"
    else:
        embed_ok = True

    checks.append((f"Embedding provider ({provider})", embed_ok, embed_hint))

    # 3. Watched directories
    watched_ok = False
    try:
        dirs_file = config.WATCHED_DIRS_FILE
        if dirs_file.exists():
            dirs = json.loads(dirs_file.read_text())
            watched_ok = isinstance(dirs, list) and len(dirs) > 0
    except Exception:
        watched_ok = False
    checks.append(("Watched directories configured", watched_ok, "Run: recall index ~/Documents"))

    # 4. Index has documents (only if daemon is up)
    if daemon_ok:
        try:
            stats = _fetch_json("/stats", timeout=5.0)
            count = int(stats.get("count", 0))
            checks.append((f"Index has documents ({count:,})", count > 0, "Run: recall sync  or  recall index <path>"))
        except Exception:
            checks.append(("Index has documents", False, "Run: recall sync"))

    critical_failed = any(not passed for _, passed, _ in checks)

    if console is None:
        print("Recall doctor")
        print("─" * 40)
        for label, passed, hint in checks:
            status = "[OK]  " if passed else "[FAIL]"
            print(f"{status} {label}")
            if not passed and hint:
                print(f"       → {hint}")
    else:
        console.print()
        console.print(f"  [bold]Recall doctor[/bold]  [{_DIM}]setup health check[/]")
        console.print(f"  [{_DIM}]{'─' * 44}[/]")
        console.print()
        for label, passed, hint in checks:
            if passed:
                console.print(f"  [bold green]✔[/]  {label}")
            else:
                console.print(f"  [bold red]✗[/]  {label}")
                if hint:
                    console.print(f"     [{_DIM}]→ {hint}[/]")
        console.print()

    # 5. Connector status (informational)
    if daemon_ok:
        try:
            connector_status = _fetch_json("/connector-status")
            if connector_status:
                if console is None:
                    print("Connectors (optional):")
                    for name in sorted(connector_status):
                        authed = bool(connector_status[name].get("authenticated"))
                        print(f"  {'✔' if authed else '✗'} {name}")
                else:
                    console.print(f"  [{_DIM}]Connectors (optional)[/]")
                    console.print(f"  [{_DIM}]{'─' * 44}[/]")
                    for name in sorted(connector_status):
                        authed = bool(connector_status[name].get("authenticated"))
                        dot = f"[bold {_ACCENT}]✔[/]" if authed else f"[{_DIM}]✗[/]"
                        console.print(f"  {dot}  {name}")
                    console.print()
        except Exception:
            pass

    return 1 if critical_failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recall", description="Recall — local semantic search CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start daemon")
    p_start.set_defaults(func=_cmd_start)

    p_stop = sub.add_parser("stop", help="Stop daemon")
    p_stop.set_defaults(func=_cmd_stop)

    p_status = sub.add_parser("status", help="Show daemon/connector/indexing status")
    p_status.set_defaults(func=_cmd_status)

    p_search = sub.add_parser("search", help="Semantic search across indexed data")
    p_search.add_argument("query")
    p_search.add_argument("-n", "--n-results", type=int, default=10)
    p_search.set_defaults(func=_cmd_search)

    p_context = sub.add_parser("context", help="Fetch concise context for an AI prompt topic")
    p_context.add_argument("topic")
    p_context.add_argument("-n", "--n-results", type=int, default=5)
    p_context.set_defaults(func=_cmd_context)

    p_sync = sub.add_parser("sync", help="Trigger immediate connector sync via daemon")
    p_sync.add_argument("source", nargs="?", default=None)
    p_sync.set_defaults(func=_cmd_sync)

    p_index = sub.add_parser("index", help="Index a file or directory")
    p_index.add_argument("path")
    p_index.add_argument("--source", default="manual")
    p_index.add_argument("--description", default="")
    p_index.set_defaults(func=_cmd_index)

    p_connect = sub.add_parser("connect", help="Authenticate a connector")
    p_connect.add_argument("source")
    p_connect.set_defaults(func=_cmd_connect)

    p_open = sub.add_parser("open-memory", help="Open top matching search result")
    p_open.add_argument("query")
    p_open.set_defaults(func=_cmd_open_memory)

    p_doctor = sub.add_parser("doctor", help="Check Recall setup health")
    p_doctor.set_defaults(func=_cmd_doctor)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
