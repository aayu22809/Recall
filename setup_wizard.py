#!/usr/bin/env python3
"""
vector-embedded-finder setup wizard
Run: python setup_wizard.py
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# ── Auto-install UI deps ───────────────────────────────────────────────────────


def _ensure_ui_deps() -> None:
    missing = []
    try:
        import rich  # noqa: F401
    except ImportError:
        missing.append("rich>=13.0")
    try:
        import questionary  # noqa: F401
    except ImportError:
        missing.append("questionary>=2.0")
    if missing:
        print(f"Installing setup dependencies: {', '.join(missing)} …")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q"] + missing,
            stdout=subprocess.DEVNULL,
        )


_ensure_ui_deps()

# ── Imports (safe after auto-install) ─────────────────────────────────────────

from rich.console import Console  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.progress import (  # noqa: E402
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.text import Text  # noqa: E402
import questionary  # noqa: E402
from questionary import Style as QStyle  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.resolve()
ENV_FILE = REPO_ROOT / ".env"
CONSOLE = Console()

C = {
    "primary": "default",
    "dim": "#737373",
    "accent": "#d97757",
    "success": "green",
    "error": "red",
    "warn": "yellow",
}

QSTYLE = QStyle([
    ("qmark",       "fg:#737373"),
    ("question",    "fg:default"),
    ("answer",      "fg:default bold"),
    ("pointer",     "fg:#d97757"),
    ("highlighted", "fg:#d97757"),
    ("selected",    "fg:default bold"),
    ("separator",   "fg:#737373"),
    ("instruction", "fg:#737373"),
    ("text",        "fg:default"),
    ("disabled",    "fg:#737373"),
])

SUPPORTED_EXT = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".pdf",
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".toml", ".xml", ".html", ".py", ".js", ".ts",
    ".go", ".rs", ".sh",
}

# ── Helpers ───────────────────────────────────────────────────────────────────


def ok(msg: str) -> str:
    return f"[{C['success']}]✔[/] {msg}"


def warn(msg: str) -> str:
    return f"[{C['warn']}]⚠[/] {msg}"


def err_msg(msg: str) -> str:
    return f"[{C['error']}]✖[/] {msg}"


def step_header(n: int, total: int, title: str) -> None:
    CONSOLE.print()
    t = Text()
    t.append(f"  {title.upper()}", style=f"bold")
    CONSOLE.print(t)
    CONSOLE.print(f"  [{C['dim']}]{'─' * 52}[/]")
    CONSOLE.print()


def count_supported(path: Path, limit: int = 5000) -> int:
    """Count supported files quickly, stop at limit to avoid hanging on huge dirs."""
    n = 0
    try:
        for f in path.expanduser().rglob("*"):
            if (
                f.is_file()
                and not f.name.startswith("._")
                and f.suffix.lower() in SUPPORTED_EXT
            ):
                n += 1
                if n >= limit:
                    return n
    except (PermissionError, OSError):
        pass
    return n


def save_api_key(key: str) -> None:
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
        updated, found = [], False
        for line in lines:
            if line.startswith("GEMINI_API_KEY="):
                updated.append(f"GEMINI_API_KEY={key}")
                found = True
            else:
                updated.append(line)
        if not found:
            updated.append(f"GEMINI_API_KEY={key}")
        ENV_FILE.write_text("\n".join(updated) + "\n")
    else:
        ENV_FILE.write_text(f"GEMINI_API_KEY={key}\n")


# ── Screen 1: Splash ──────────────────────────────────────────────────────────


def screen_splash() -> None:
    CONSOLE.clear()
    CONSOLE.print()
    CONSOLE.print(f"  [{C['dim']}]╭─ vector-embedded-finder setup ─╮[/]")
    CONSOLE.print(f"  [{C['dim']}]│ local multimodal memory        │[/]")
    CONSOLE.print(f"  [{C['dim']}]╰────────────────────────────────╯[/]")
    CONSOLE.print()
    time.sleep(0.5)


# ── Screen 2: Auto-detect ─────────────────────────────────────────────────────


def screen_detect() -> dict:
    CONSOLE.clear()
    CONSOLE.print()
    CONSOLE.print(f"  [{C['dim']}]scanning your system…[/]")
    CONSOLE.print()

    result: dict = {}

    # Python
    time.sleep(0.18)
    python = shutil.which("python3") or sys.executable
    result["python"] = python
    CONSOLE.print(f"  {ok(f'python3  [dim]→ {python}[/dim]')}")

    # Repo root
    time.sleep(0.12)
    result["repo"] = str(REPO_ROOT)
    CONSOLE.print(f"  {ok(f'repo     [dim]→ {REPO_ROOT}[/dim]')}")

    # Required packages
    time.sleep(0.12)
    pkg_checks = [
        ("chromadb",    "chromadb"),
        ("google-genai", "google.genai"),
        ("python-dotenv", "dotenv"),
    ]
    for display, import_name in pkg_checks:
        try:
            importlib.import_module(import_name)
            CONSOLE.print(f"  {ok(display)}")
        except ImportError:
            CONSOLE.print(f"  {err_msg(f'{display} not found')}")
            result["missing_dep"] = display

    # Existing API key?
    time.sleep(0.10)
    existing_key = os.environ.get("GEMINI_API_KEY", "")
    if not existing_key and ENV_FILE.exists():
        try:
            from dotenv import dotenv_values
            existing_key = dotenv_values(ENV_FILE).get("GEMINI_API_KEY", "")
        except Exception:
            pass

    if existing_key:
        result["existing_key"] = existing_key
        CONSOLE.print(f"  {warn(f'API key found  [dim]→ you can update or keep it[/dim]')}")
    else:
        CONSOLE.print(f"  {warn('no API key found  [dim]→ we will set it up[/dim]')}")

    CONSOLE.print()
    time.sleep(0.3)
    return result


# ── Screen 3: API Key ─────────────────────────────────────────────────────────


def screen_api_key(detected: dict) -> str:
    CONSOLE.clear()
    step_header(1, 3, "GEMINI API KEY")

    existing = detected.get("existing_key", "")
    if existing:
        keep = questionary.confirm(
            "An API key is already configured. Keep it?",
            default=True,
            style=QSTYLE,
            qmark=">",
        ).ask()
        if keep:
            CONSOLE.print()
            return existing

    CONSOLE.print(f"  [{C['dim']}]You need a free Gemini key to generate embeddings.[/]")
    CONSOLE.print()
    CONSOLE.print(f"  [{C['accent']}]→ Get yours free at:[/] [underline]https://aistudio.google.com/apikey[/underline]")
    CONSOLE.print()

    try:
        webbrowser.open("https://aistudio.google.com/apikey")
        CONSOLE.print(f"  [{C['dim']}]  (opening in browser…)[/]")
    except Exception:
        pass

    CONSOLE.print()

    while True:
        key = questionary.password(
            "Paste your Gemini API key:",
            style=QSTYLE,
            qmark=">",
        ).ask()

        if not key or not key.strip():
            CONSOLE.print(f"  [{C['warn']}]Key can't be empty. Try again.[/]")
            continue

        key = key.strip()

        CONSOLE.print()
        CONSOLE.print(f"  [{C['dim']}]validating key…[/]")

        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key)
            client.models.embed_content(
                model="gemini-embedding-2-preview",
                contents="test",
                config=types.EmbedContentConfig(output_dimensionality=8),
            )
            CONSOLE.print(f"  {ok('API key is valid')}")
            break
        except Exception as e:
            msg = str(e).lower()
            if any(x in msg for x in ("api_key", "invalid", "unauthenticated", "permission", "401", "403")):
                CONSOLE.print(f"  {err_msg('Invalid API key. Check and try again.')}")
            else:
                # Network issue or model error — accept the key anyway
                CONSOLE.print(f"  {ok(f'Key accepted  [dim](validation skipped: {str(e)[:60]})[/dim]')}")
                break

    save_api_key(key)
    CONSOLE.print(f"  [{C['dim']}]saved to {ENV_FILE.name}[/]")
    CONSOLE.print()
    time.sleep(0.4)
    return key


# ── Screen 4: Folder Picker ───────────────────────────────────────────────────


def screen_folders() -> list[Path]:
    CONSOLE.clear()
    step_header(2, 3, "CHOOSE FOLDERS TO INDEX")

    CONSOLE.print(f"  [{C['dim']}]Space to toggle  ·  Enter to confirm  ·  arrows to move[/]")
    CONSOLE.print()

    candidates = [
        Path("~/Photos"),
        Path("~/Pictures"),
        Path("~/Documents"),
        Path("~/Downloads"),
        Path("~/Desktop"),
        Path("~/Movies"),
        Path("~/Music"),
    ]
    present = [p for p in candidates if p.expanduser().exists()]

    CONSOLE.print(f"  [{C['dim']}]counting files…[/]", end="")
    counts: dict[Path, int] = {}
    for p in present:
        counts[p] = count_supported(p)
        CONSOLE.print(f" [{C['dim']}]..[/]", end="")
    CONSOLE.print()
    CONSOLE.print()

    choices: list = []
    for p in present:
        n = counts[p]
        label = f"{p}  ({n:,}+ files)" if n >= 5000 else f"{p}  ({n:,} files)" if n else f"{p}  (no supported files)"
        choices.append(questionary.Choice(label, value=p))

    choices.append(questionary.Separator("─────────────────────────"))
    choices.append(questionary.Choice("+ enter a custom path…", value="__custom__"))

    selected = questionary.checkbox(
        "Which folders should be indexed?",
        choices=choices,
        style=QSTYLE,
        qmark=">",
        pointer="❯",
    ).ask()

    if not selected:
        CONSOLE.print(f"  [{C['warn']}]No folders selected — skipping indexing.[/]")
        return []

    folders: list[Path] = []
    for s in selected:
        if s == "__custom__":
            raw = questionary.text(
                "Path to index:",
                style=QSTYLE,
                qmark=">",
            ).ask()
            if raw and raw.strip():
                folders.append(Path(raw.strip()))
        else:
            folders.append(s)

    total = sum(counts.get(f, count_supported(f)) for f in folders)
    CONSOLE.print()
    CONSOLE.print(f"  [{C['success']}]{len(folders)} folder(s) selected · ~{total:,} files[/]")
    CONSOLE.print()
    time.sleep(0.4)
    return folders


# ── Screen 5: Indexing ────────────────────────────────────────────────────────


def screen_index(folders: list[Path], api_key: str) -> dict[str, int]:
    if not folders:
        return {"embedded": 0, "skipped": 0, "errors": 0}

    CONSOLE.clear()
    step_header(3, 3, "INDEXING")

    os.environ["GEMINI_API_KEY"] = api_key
    sys.path.insert(0, str(REPO_ROOT))

    # Collect files
    all_files: list[Path] = []
    for folder in folders:
        try:
            base = folder.expanduser().resolve()
            for f in sorted(base.rglob("*")):
                if (
                    f.is_file()
                    and not f.name.startswith("._")
                    and f.suffix.lower() in SUPPORTED_EXT
                ):
                    all_files.append(f)
        except (PermissionError, OSError):
            pass

    if not all_files:
        CONSOLE.print(f"  [{C['warn']}]No supported files found in selected folders.[/]")
        return {"embedded": 0, "skipped": 0, "errors": 0}

    from vector_embedded_finder.ingest import ingest_file

    stats = {"embedded": 0, "skipped": 0, "errors": 0}
    retry_wait = 60  # seconds for first rate-limit hit

    with Progress(
        SpinnerColumn(style=f"bold {C['dim']}"),
        BarColumn(
            bar_width=36,
            style=C["dim"],
            complete_style=f"bold {C['primary']}",
            finished_style=f"bold {C['success']}",
        ),
        TaskProgressColumn(),
        TextColumn("[dim]{task.description}[/dim]"),
        TimeRemainingColumn(),
        console=CONSOLE,
        refresh_per_second=8,
    ) as progress:
        task = progress.add_task("starting…", total=len(all_files))

        for fp in all_files:
            progress.update(task, description=fp.name[:48])

            while True:
                try:
                    r = ingest_file(fp)
                    if r["status"] == "embedded":
                        stats["embedded"] += 1
                    elif r["status"] == "skipped":
                        stats["skipped"] += 1
                    break
                except Exception as e:
                    msg = str(e)
                    if "429" in msg or "rate" in msg.lower() or "quota" in msg.lower():
                        progress.stop()
                        CONSOLE.print()
                        for remaining in range(retry_wait, 0, -1):
                            countdown = (
                                f"  [{C['warn']}]⏳  Rate limited — "
                                f"retrying in {remaining}s  "
                                f"(Gemini free tier: ~1,500 req/min)[/]"
                            )
                            CONSOLE.print(countdown, end="\r")
                            time.sleep(1)
                        CONSOLE.print(" " * 80, end="\r")
                        progress.start()
                        retry_wait = min(retry_wait * 2, 300)
                    else:
                        stats["errors"] += 1
                        break

            progress.advance(task)

    return stats


# ── Screen 6: Done + Raycast Card ─────────────────────────────────────────────


def screen_done(detected: dict, stats: dict[str, int]) -> None:
    CONSOLE.clear()
    CONSOLE.print()

    headline = f"  ✦  {stats['embedded']:,} files embedded  ✦"
    CONSOLE.print(headline, justify="left", style="bold")

    # Stats line
    parts: list[str] = []
    if stats["skipped"]:
        parts.append(f"[{C['dim']}]{stats['skipped']:,} already indexed[/]")
    if stats["errors"]:
        parts.append(f"[{C['warn']}]{stats['errors']:,} errors[/]")
    if parts:
        CONSOLE.print(f"  {' · '.join(parts)}")

    CONSOLE.print()

    # ── Raycast setup card ─────────────────────────────────────────────────────
    python_path = detected["python"]
    repo_path = detected["repo"]

    card = Text()
    card.append("Open Raycast  →  search ", style=C["dim"])
    card.append('"Memory Search"', style=f"bold")
    card.append("  →  ", style=C["dim"])
    card.append("Preferences\n\n", style=C["dim"])

    fields = [
        ("Python Package Path", repo_path,    "bold"),
        ("Python Binary",       python_path,  "bold"),
        ("Gemini API Key",      f"(set — see {ENV_FILE.name} in repo root)",  C["warn"]),
    ]
    for label, value, color in fields:
        card.append(f"  {label}\n", style=f"dim")
        card.append(f"  {value}\n\n", style=color)

    CONSOLE.print(Panel(
        card,
        title=f"[bold]  RAYCAST SETUP  [/]",
        border_style=C["dim"],
        padding=(1, 2),
    ))

    # Attempt clipboard copy
    try:
        import pyperclip
        clip = f"Python Package Path: {repo_path}\nPython Binary: {python_path}"
        pyperclip.copy(clip)
        CONSOLE.print(f"  [{C['dim']}]Paths copied to clipboard ✔[/]")
    except Exception:
        pass

    CONSOLE.print()
    CONSOLE.print(f"  [{C['dim']}]You're all set. Open Raycast and search for Memory Search to try it. 🔍[/]")
    CONSOLE.print()


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    try:
        screen_splash()
        detected = screen_detect()

        if "missing_dep" in detected:
            CONSOLE.print(f"  [{C['warn']}]Fix missing dependency first:[/]")
            CONSOLE.print(f"  [bold]pip install -e .[/bold]")
            CONSOLE.print(f"  Then re-run: [bold]python setup_wizard.py[/bold]\n")
            sys.exit(1)

        api_key = screen_api_key(detected)
        folders = screen_folders()
        stats = screen_index(folders, api_key)
        screen_done(detected, stats)

    except KeyboardInterrupt:
        CONSOLE.print(f"\n\n  [{C['dim']}]Setup cancelled.[/]\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
