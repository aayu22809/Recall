#!/usr/bin/env python3
"""
vector-embedded-finder setup wizard
Run: python setup_wizard.py
"""

from __future__ import annotations

import importlib
import json
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
    t.append(f"  {title.upper()}", style="bold")
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


def _detect_embedding_provider() -> str:
    provider = os.environ.get("VEF_EMBEDDING_PROVIDER", "").strip().lower()
    if provider:
        return provider
    if ENV_FILE.exists():
        try:
            from dotenv import dotenv_values

            provider = (
                (dotenv_values(ENV_FILE).get("VEF_EMBEDDING_PROVIDER") or "")
                .strip()
                .lower()
            )
        except Exception:
            provider = ""
    return provider or "gemini"


# ── Screen 1: Splash ──────────────────────────────────────────────────────────


def screen_splash() -> None:
    CONSOLE.clear()
    CONSOLE.print()
    CONSOLE.print(f"  [{C['dim']}]╭─────── Recall setup ────────────╮[/]")
    CONSOLE.print(f"  [{C['dim']}]│ local search for everything     │[/]")
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

    provider = _detect_embedding_provider()
    result["embedding_provider"] = provider
    CONSOLE.print(f"  {ok(f'embedding provider  [dim]→ {provider}[/dim]')}")

    # Required packages
    time.sleep(0.12)
    pkg_checks = [("chromadb", "chromadb"), ("python-dotenv", "dotenv")]
    if provider == "gemini":
        pkg_checks.append(("google-genai", "google.genai"))
    elif provider in {"ollama", "nim"}:
        pkg_checks.append(("httpx", "httpx"))

    for display, import_name in pkg_checks:
        try:
            importlib.import_module(import_name)
            CONSOLE.print(f"  {ok(display)}")
        except ImportError:
            CONSOLE.print(f"  {err_msg(f'{display} not found')}")
            result["missing_dep"] = display

    if provider == "gemini":
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
            CONSOLE.print(f"  {warn('API key found  [dim]→ you can update or keep it[/dim]')}")
        else:
            CONSOLE.print(f"  {warn('no API key found  [dim]→ we will set it up[/dim]')}")
    else:
        CONSOLE.print(
            f"  {ok('API key setup skipped  [dim](non-Gemini provider selected)[/dim]')}"
        )

    CONSOLE.print()
    time.sleep(0.3)
    return result


# ── Screen 3: API Key ─────────────────────────────────────────────────────────


def screen_api_key(detected: dict) -> str:
    provider = detected.get("embedding_provider", "gemini")
    if provider != "gemini":
        CONSOLE.clear()
        step_header(1, 3, "EMBEDDING PROVIDER")
        CONSOLE.print(
            f"  [{C['dim']}]Using provider: {provider}. Gemini API key step is skipped.[/]"
        )
        CONSOLE.print()
        time.sleep(0.4)
        return ""

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

    # Persist watched directories so daemon can start the filesystem watcher.
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from vector_embedded_finder import config as vef_config

        vef_config.ensure_vef_dirs()
        watched = [str(f.expanduser().resolve()) for f in folders]
        vef_config.WATCHED_DIRS_FILE.write_text(json.dumps(watched, indent=2))
    except Exception as e:
        CONSOLE.print(f"  {warn(f'Could not save watched dirs config: {str(e)[:80]}')}")

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

    if api_key:
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
    retry_wait = 8  # seconds for first rate-limit hit (doubles each time, max 60)

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
                        retry_wait = min(retry_wait * 2, 60)
                    else:
                        stats["errors"] += 1
                        break

            progress.advance(task)

    return stats


# ── Screen 5b: Ollama Check ───────────────────────────────────────────────────


def screen_ollama() -> None:
    CONSOLE.clear()
    step_header(4, 8, "LOCAL AI (OPTIONAL)")

    CONSOLE.print(f"  [{C['dim']}]Recall can caption images and transcribe audio/video locally[/]")
    CONSOLE.print(f"  [{C['dim']}]using Ollama (vision model) + Whisper (speech-to-text).[/]")
    CONSOLE.print()

    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            vlm_keywords = ("llava", "moondream", "bakllava", "minicpm-v")
            vlm_found = next(
                (m["name"] for m in models if any(kw in m["name"].lower() for kw in vlm_keywords)),
                None,
            )
            if vlm_found:
                CONSOLE.print(f"  {ok(f'Ollama running · vision model: {vlm_found}')}")
            else:
                CONSOLE.print(f"  {warn('Ollama running but no vision model found')}")
                CONSOLE.print(f"  [{C['dim']}]  → Run: ollama pull moondream  (tiny, fast)[/]")
        else:
            CONSOLE.print(f"  {warn('Ollama not running — image captioning disabled')}")
            CONSOLE.print(f"  [{C['dim']}]  → Install: https://ollama.ai  then: ollama pull moondream[/]")
    except Exception:
        CONSOLE.print(f"  {warn('Ollama not found — image captioning disabled')}")
        CONSOLE.print(f"  [{C['dim']}]  → Install Ollama from https://ollama.ai[/]")

    try:
        import faster_whisper  # noqa: F401
        CONSOLE.print(f"  {ok('faster-whisper available — audio transcription enabled')}")
    except ImportError:
        try:
            import whisper  # noqa: F401
            CONSOLE.print(f"  {ok('whisper available — audio transcription enabled')}")
        except ImportError:
            CONSOLE.print(f"  {warn('No STT backend — audio transcription disabled')}")
            CONSOLE.print(f"  [{C['dim']}]  → Run: pip install faster-whisper[/]")

    CONSOLE.print()
    questionary.press_any_key_to_continue("  Press any key to continue…", style=QSTYLE).ask()


# ── Screen 5c: Gmail / Google Calendar ────────────────────────────────────────


def screen_google() -> bool:
    """Returns True if Google auth was completed."""
    CONSOLE.clear()
    step_header(5, 8, "GMAIL & GOOGLE CALENDAR")

    CONSOLE.print(f"  [{C['dim']}]Connect Google to search emails and calendar events.[/]")
    CONSOLE.print()

    connect = questionary.confirm(
        "Connect Gmail and Google Calendar?",
        default=False,
        style=QSTYLE,
        qmark=">",
    ).ask()

    if not connect:
        CONSOLE.print(f"  [{C['dim']}]Skipped — you can run vef-setup again to connect later.[/]")
        CONSOLE.print()
        time.sleep(0.4)
        return False

    # Check for OAuth client file
    from pathlib import Path as _Path
    creds_dir = _Path.home() / ".vef" / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    oauth_client = creds_dir / "gmail_oauth_client.json"
    legacy_oauth_client = creds_dir / "gmail_oauth_client.json.json"

    if not oauth_client.exists() and legacy_oauth_client.exists():
        try:
            legacy_oauth_client.rename(oauth_client)
            CONSOLE.print(
                f"  [{C['dim']}]Found legacy OAuth filename and renamed it to gmail_oauth_client.json.[/]"
            )
            CONSOLE.print()
        except OSError:
            oauth_client = legacy_oauth_client

    if not oauth_client.exists():
        CONSOLE.print()
        CONSOLE.print(f"  [{C['warn']}]You need a Google OAuth 2.0 client credentials file.[/]")
        CONSOLE.print(f"  [{C['dim']}]Steps:[/]")
        CONSOLE.print(f"  [{C['dim']}]  1. Go to https://console.cloud.google.com[/]")
        CONSOLE.print(f"  [{C['dim']}]  2. Create a project → APIs & Services → Credentials[/]")
        CONSOLE.print(f"  [{C['dim']}]  3. Create OAuth 2.0 Client ID → Desktop app[/]")
        CONSOLE.print(f"  [{C['dim']}]  4. Download JSON → save to:[/]")
        CONSOLE.print(f"  [bold]     {oauth_client}[/bold]")
        CONSOLE.print()
        try:
            webbrowser.open("https://console.cloud.google.com/apis/credentials")
        except Exception:
            pass

        questionary.press_any_key_to_continue(
            "  Paste the file there, then press any key…", style=QSTYLE
        ).ask()

        if not oauth_client.exists():
            CONSOLE.print(f"  {warn('File not found — skipping Google auth')}")
            CONSOLE.print()
            return False

    CONSOLE.print()
    CONSOLE.print(f"  [{C['dim']}]Opening browser for Google auth…[/]")
    CONSOLE.print()

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from vector_embedded_finder.connectors.gmail import GmailConnector
        from vector_embedded_finder.connectors.gcal import GCalConnector
        c = GCalConnector()
        c.authenticate()
        gmail_ready = GmailConnector().is_authenticated()
        gcal_ready = GCalConnector().is_authenticated()
        if gmail_ready and gcal_ready:
            CONSOLE.print(f"  {ok('Google auth successful — Gmail + Calendar connected')}")
            CONSOLE.print(f"  [{C['dim']}]Run [bold]vef-daemon sync gmail[/bold] to force first email sync now.[/]")
            CONSOLE.print()
            time.sleep(0.4)
            return True
        CONSOLE.print(
            f"  {warn('OAuth flow finished but credentials were not saved correctly. Re-run auth after confirming gmail_oauth_client.json is valid.')}"
        )
        CONSOLE.print()
        return False
    except Exception as e:
        CONSOLE.print(f"  {err_msg(f'Auth failed: {str(e)[:100]}')}")
        CONSOLE.print(
            f"  [{C['dim']}]If browser auth failed, manually verify [bold]{oauth_client}[/bold] and run:[/]"
        )
        CONSOLE.print("  [bold]python -m vector_embedded_finder.connectors.gmail authenticate[/bold]")
        CONSOLE.print()
        return False


# ── Screen 5d: cal.ai ─────────────────────────────────────────────────────────


def screen_calai() -> bool:
    CONSOLE.clear()
    step_header(6, 8, "CAL.AI")

    CONSOLE.print(f"  [{C['dim']}]Connect cal.ai to search your scheduled meetings.[/]")
    CONSOLE.print()

    connect = questionary.confirm(
        "Connect cal.ai?",
        default=False,
        style=QSTYLE,
        qmark=">",
    ).ask()

    if not connect:
        CONSOLE.print(f"  [{C['dim']}]Skipped.[/]")
        time.sleep(0.3)
        return False

    CONSOLE.print()
    CONSOLE.print(f"  [{C['accent']}]→ Get your API key at:[/] [underline]https://app.cal.com/settings/developer/api-keys[/underline]")
    CONSOLE.print()
    try:
        webbrowser.open("https://app.cal.com/settings/developer/api-keys")
    except Exception:
        pass

    key = questionary.password("Paste your cal.ai API key:", style=QSTYLE, qmark=">").ask()
    if not key or not key.strip():
        CONSOLE.print(f"  {warn('No key entered — skipping')}")
        return False

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from vector_embedded_finder.connectors.calai import CalAIConnector
        c = CalAIConnector()
        c.set_api_key(key.strip())
        CONSOLE.print(f"  {ok('cal.ai API key saved')}")
        time.sleep(0.3)
        return True
    except Exception as e:
        CONSOLE.print(f"  {err_msg(str(e)[:100])}")
        return False


# ── Screen 5e: LMS ────────────────────────────────────────────────────────────


def screen_lms() -> bool:
    CONSOLE.clear()
    step_header(7, 8, "LEARNING MANAGEMENT SYSTEM")

    lms = questionary.select(
        "Which LMS do you use?",
        choices=["Canvas", "Schoology", "Neither"],
        style=QSTYLE,
        qmark=">",
    ).ask()

    if lms == "Neither":
        CONSOLE.print(f"  [{C['dim']}]Skipped.[/]")
        time.sleep(0.3)
        return False

    if lms == "Canvas":
        CONSOLE.print()
        CONSOLE.print(f"  [{C['dim']}]Canvas API token: Account → Settings → Approved Integrations → New Access Token[/]")
        CONSOLE.print()
        base_url = questionary.text(
            "Canvas base URL (e.g. https://canvas.instructure.com):",
            style=QSTYLE, qmark=">",
        ).ask()
        token = questionary.password("Canvas API token:", style=QSTYLE, qmark=">").ask()

        if not base_url or not token:
            CONSOLE.print(f"  {warn('Missing info — skipping')}")
            return False
        try:
            sys.path.insert(0, str(REPO_ROOT))
            from vector_embedded_finder.connectors.canvas import CanvasConnector
            c = CanvasConnector()
            c.set_credentials(token.strip(), base_url.strip())
            CONSOLE.print(f"  {ok('Canvas credentials saved')}")
            time.sleep(0.3)
            return True
        except Exception as e:
            CONSOLE.print(f"  {err_msg(str(e)[:100])}")
            return False

    # Schoology
    CONSOLE.print()
    CONSOLE.print(f"  [{C['dim']}]Schoology: Settings → API Access → Consumer Key/Secret[/]")
    CONSOLE.print()
    base_url = questionary.text(
        "Schoology API base URL (default: https://api.schoology.com/v1):",
        default="https://api.schoology.com/v1",
        style=QSTYLE, qmark=">",
    ).ask()
    key = questionary.password("Consumer key:", style=QSTYLE, qmark=">").ask()
    secret = questionary.password("Consumer secret:", style=QSTYLE, qmark=">").ask()

    if not key or not secret:
        CONSOLE.print(f"  {warn('Missing credentials — skipping')}")
        return False
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from vector_embedded_finder.connectors.schoology import SchoologyConnector
        c = SchoologyConnector()
        c.set_credentials(key.strip(), secret.strip(), base_url.strip())
        CONSOLE.print(f"  {ok('Schoology credentials saved')}")
        time.sleep(0.3)
        return True
    except Exception as e:
        CONSOLE.print(f"  {err_msg(str(e)[:100])}")
        return False


# ── Screen 5f: Initial connector sync ─────────────────────────────────────────


def screen_connector_sync(api_key: str, google_ok: bool, calai_ok: bool, lms_ok: bool) -> dict:
    """Run initial sync for all connected sources with a unified progress bar."""
    if not (google_ok or calai_ok or lms_ok):
        return {}

    CONSOLE.clear()
    step_header(8, 8, "INITIAL SYNC")

    os.environ["GEMINI_API_KEY"] = api_key
    sys.path.insert(0, str(REPO_ROOT))

    stats: dict[str, dict] = {}

    with Progress(
        SpinnerColumn(style=f"bold {C['dim']}"),
        BarColumn(bar_width=36, style=C["dim"], complete_style=f"bold {C['primary']}", finished_style=f"bold {C['success']}"),
        TaskProgressColumn(),
        TextColumn("[dim]{task.description}[/dim]"),
        TimeRemainingColumn(),
        console=CONSOLE,
        refresh_per_second=8,
    ) as progress:

        if google_ok:
            from vector_embedded_finder.connectors.gmail import GmailConnector
            from vector_embedded_finder.connectors.gcal import GCalConnector

            gmail_task = progress.add_task("Gmail sync…", total=None)
            try:
                gc = GmailConnector()
                if gc.is_authenticated():
                    results = gc.sync(since=None, progress_cb=lambda c, t, _: progress.update(gmail_task, total=t, completed=c, description=f"Gmail {c}/{t}"))
                    embedded = sum(1 for r in results if r.get("status") == "embedded")
                    stats["gmail"] = {"embedded": embedded, "total": len(results)}
                    progress.update(gmail_task, description=f"Gmail ✓ {embedded} threads")
            except Exception as e:
                progress.update(gmail_task, description=f"Gmail error: {str(e)[:50]}")

            gcal_task = progress.add_task("Calendar sync…", total=None)
            try:
                gcc = GCalConnector()
                if gcc.is_authenticated():
                    results = gcc.sync(since=None, progress_cb=lambda c, t, _: progress.update(gcal_task, total=t, completed=c, description=f"Calendar {c}/{t}"))
                    embedded = sum(1 for r in results if r.get("status") == "embedded")
                    stats["gcal"] = {"embedded": embedded, "total": len(results)}
                    progress.update(gcal_task, description=f"Calendar ✓ {embedded} events")
            except Exception as e:
                progress.update(gcal_task, description=f"Calendar error: {str(e)[:50]}")

        if calai_ok:
            from vector_embedded_finder.connectors.calai import CalAIConnector
            calai_task = progress.add_task("cal.ai sync…", total=None)
            try:
                cc = CalAIConnector()
                if cc.is_authenticated():
                    results = cc.sync(since=None, progress_cb=lambda c, t, _: progress.update(calai_task, total=t, completed=c, description=f"cal.ai {c}/{t}"))
                    embedded = sum(1 for r in results if r.get("status") == "embedded")
                    stats["calai"] = {"embedded": embedded, "total": len(results)}
                    progress.update(calai_task, description=f"cal.ai ✓ {embedded} bookings")
            except Exception as e:
                progress.update(calai_task, description=f"cal.ai error: {str(e)[:50]}")

        if lms_ok:
            lms_task = progress.add_task("LMS sync…", total=None)
            for ConnClass, name in [
                ("canvas", "Canvas"),
                ("schoology", "Schoology"),
            ]:
                try:
                    if ConnClass == "canvas":
                        from vector_embedded_finder.connectors.canvas import CanvasConnector
                        lc = CanvasConnector()
                    else:
                        from vector_embedded_finder.connectors.schoology import SchoologyConnector
                        lc = SchoologyConnector()
                    if lc.is_authenticated():
                        results = lc.sync(since=None, progress_cb=lambda c, t, _: progress.update(lms_task, total=t, completed=c, description=f"{name} {c}/{t}"))
                        embedded = sum(1 for r in results if r.get("status") == "embedded")
                        stats[ConnClass] = {"embedded": embedded, "total": len(results)}
                        progress.update(lms_task, description=f"{name} ✓ {embedded} items")
                except Exception as e:
                    progress.update(lms_task, description=f"{name} error: {str(e)[:50]}")

    CONSOLE.print()
    CONSOLE.print(f"  [{C['success']}]Connector sync complete.[/]")
    CONSOLE.print()
    time.sleep(0.5)
    return stats


# ── Screen 6: Done + Raycast Card ─────────────────────────────────────────────


def screen_done(detected: dict, stats: dict[str, int]) -> None:
    CONSOLE.clear()
    CONSOLE.print()
    provider = str(detected.get("embedding_provider", "gemini")).strip().lower() or "gemini"

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
    card.append('"Memory Search"', style="bold")
    card.append("  →  ", style=C["dim"])
    card.append("Preferences\n\n", style=C["dim"])

    fields = [
        ("Python Package Path", repo_path,    "bold"),
        ("Python Binary",       python_path,  "bold"),
        (
            "Embedding Provider",
            provider,
            "bold",
        ),
        (
            "Gemini API Key",
            f"(set — see {ENV_FILE.name} in repo root)" if provider == "gemini" else "(not required)",
            C["warn"] if provider == "gemini" else C["dim"],
        ),
    ]
    for label, value, color in fields:
        card.append(f"  {label}\n", style="dim")
        card.append(f"  {value}\n\n", style=color)

    CONSOLE.print(Panel(
        card,
        title="[bold]  RAYCAST SETUP  [/]",
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
        CONSOLE.print(f"  [{C['dim']}]Tip: copy paths above manually into Raycast preferences.[/]")

    CONSOLE.print()
    CONSOLE.print(f"  [{C['dim']}]You're all set. Open Raycast and search for 'Recall' to try it.[/]")
    CONSOLE.print()


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    try:
        screen_splash()
        detected = screen_detect()

        if "missing_dep" in detected:
            CONSOLE.print(f"  [{C['warn']}]Fix missing dependency first:[/]")
            CONSOLE.print("  [bold]pip install -e .[/bold]")
            CONSOLE.print("  Then re-run: [bold]python setup_wizard.py[/bold]\n")
            sys.exit(1)

        api_key = screen_api_key(detected)
        folders = screen_folders()
        stats = screen_index(folders, api_key)
        screen_ollama()
        google_ok = screen_google()
        calai_ok = screen_calai()
        lms_ok = screen_lms()
        screen_connector_sync(api_key, google_ok, calai_ok, lms_ok)
        screen_done(detected, stats)

    except KeyboardInterrupt:
        CONSOLE.print(f"\n\n  [{C['dim']}]Setup cancelled.[/]\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
