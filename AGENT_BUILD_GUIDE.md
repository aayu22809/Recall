# Recall — Agent Build Guide

> **Purpose:** This document is the authoritative single-file instruction set for an AI agent implementing the Recall performance overhaul. Read it fully before writing any code. It synthesizes the design rationale, codebase map, task-by-task instructions, constraints, and verification criteria.

---

## What You Are Building

Recall is a local semantic search app. Files on the user's Mac are embedded into a local ChromaDB vector database. A Raycast extension provides a fast search UI. The stack is Python (backend) + TypeScript (Raycast).

**Two problems to fix:**

| Problem | Root Cause | Target |
|---------|-----------|--------|
| Indexing 2,000 files takes ~21 hours | Sequential processing, binary blobs uploaded to Gemini API, free-tier rate limits | < 60 min on capable Mac, < 5 hrs on low-end |
| Each search takes 1–5 seconds | New Python subprocess spawned per query (500ms startup + 300ms imports + 300ms Gemini API) | < 500ms |

**The fix (approved design — do not deviate):**
1. **Convert media to text at ingest time** — local AI generates captions/transcripts for images, video, audio. Text is then embedded via the Gemini text API (tiny payloads, much faster). Falls back to binary embedding when local AI is unavailable.
2. **Run ingest concurrently** — `ThreadPoolExecutor` with configurable parallelism replaces the serial loop.
3. **Persistent search daemon** — FastAPI server stays alive, keeps ChromaDB + Gemini client warm. Raycast connects over HTTP instead of spawning a subprocess.

---

## Codebase Map

### Files to create (new)
```
vector_embedded_finder/captioner.py   — local AI captioning module
vector_embedded_finder/daemon.py      — FastAPI search server + CLI
```

### Files to modify
```
vector_embedded_finder/ingest.py      — add concurrency + captioner integration
vector_embedded_finder/config.py      — add MAX_CONCURRENT_INGEST constant
raycast/src/lib/runner.ts             — replace spawnSync with HTTP fetch
pyproject.toml                        — add fastapi, uvicorn deps + vef-daemon script
```

### Files that must NOT be changed
```
vector_embedded_finder/embedder.py    — never touch this
vector_embedded_finder/store.py       — never touch this
vector_embedded_finder/search.py      — never touch this
vector_embedded_finder/utils.py       — only update if SHA-256 check requires it
vector_embedded_finder/config.py      — only add MAX_CONCURRENT_INGEST
raycast/src/search-memory.tsx         — zero changes (UI must be untouched)
raycast/src/open-memory.tsx           — zero changes (UI must be untouched)
```

### Existing key APIs (do not break these)
```python
# embedder.py — use these as-is
embedder.embed_text(text: str) -> list[float]
embedder.embed_query(query: str) -> list[float]
embedder.embed_image(path: Path) -> list[float]
embedder.embed_audio(path: Path) -> list[float]
embedder.embed_video(path: Path) -> list[float]
embedder.embed_pdf(path: Path) -> list[float]

# store.py — use these as-is
store.add(doc_id, embedding, metadata, document="") -> None
store.exists(doc_id: str) -> bool
store.search(query_embedding, n_results, where=None) -> dict
store.count() -> int
store._get_collection()  # warm-up call

# search.py — use as-is for daemon
search.search(query: str, n_results: int = 5, media_type: str | None = None) -> list[dict]

# config.py — use as-is
config.SUPPORTED_EXTENSIONS  # dict: category -> set of extensions
config.get_media_category(ext: str) -> str | None
config.get_api_key() -> str
```

---

## Track A — Ingest Speed

Build in this order: T-001 → (T-002, T-003, T-004 in parallel) → T-005 → T-006 → T-007
Also build independently (no deps): T-008, T-009, T-010, T-011, T-012

### T-001 · Capability detection · `captioner.py` (new file)

Create `vector_embedded_finder/captioner.py`. Start with this structure:

```python
"""Local AI captioning for media files.

Provides image/video captioning (Ollama VLM) and audio transcription
(Whisper). Gracefully falls back — never crashes the ingest pipeline.
"""
from __future__ import annotations

import base64
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
VISION_KEYWORDS = ("llava", "moondream", "bakllava", "minicpm-v")
```

**`detect_capabilities()` must:**
- Probe Ollama: `GET http://localhost:11434/api/tags` with 3s timeout. Look for any model whose name contains a VISION_KEYWORD. Store the first match as `vlm_model`.
- Probe STT: try `import faster_whisper`, then `import whisper`. If either succeeds, `stt_available = True`.
- Cache result in `_capabilities: Capabilities | None = None` module-level variable. Second call returns cached object without re-probing.
- Never raise. Wrap everything in `try/except Exception`. Return `Capabilities(False, False)` on any error.
- Total detection must complete within 5 seconds.

```python
@dataclass
class Capabilities:
    vlm_available: bool = False
    stt_available: bool = False
    vlm_model: str = ""

_capabilities: Capabilities | None = None

def detect_capabilities() -> Capabilities: ...
```

**Validation:** `python -c "from vector_embedded_finder.captioner import detect_capabilities; c = detect_capabilities(); print(c); c2 = detect_capabilities(); assert c is c2, 'not cached'"` must run without error (even with no Ollama installed).

---

### T-002 · Image captioning · `captioner.py`

Add `CaptionError(Exception)` class (define once, use everywhere).

**`caption_image(path: Path) -> str`:**
- If `not detect_capabilities().vlm_available` → raise `CaptionError("no VLM available")`
- Read image bytes, base64-encode
- POST to `{OLLAMA_BASE}/api/generate`:
  ```json
  {
    "model": "<vlm_model from detection>",
    "prompt": "Describe this image in detail. Include people, objects, locations, activities, colors, and any text visible.",
    "images": ["<base64>"],
    "stream": false
  }
  ```
- On HTTP error or exception → raise `CaptionError`
- If response `["response"]` is empty or < 10 chars → raise `CaptionError("empty response from VLM")`
- Return response text stripped

Use `httpx` for HTTP calls (add to pyproject.toml if not already present — it's already in the plan). Timeout: 120s (captioning can be slow on CPU).

---

### T-003 · Video captioning · `captioner.py`

**`caption_video(path: Path) -> str`:**
- If `not detect_capabilities().vlm_available` → raise `CaptionError("no VLM available")`
- Extract one PNG frame at 1s using ffmpeg:
  ```python
  proc = subprocess.run(
      ["ffmpeg", "-y", "-i", str(path), "-ss", "00:00:01",
       "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
      capture_output=True, timeout=30
  )
  if proc.returncode != 0 or not proc.stdout:
      raise CaptionError(f"ffmpeg failed")
  ```
- Base64-encode `proc.stdout` and send to Ollama same as `caption_image`
- Raise `CaptionError` on any failure

**Important:** Video captioning (visual frames) and audio transcription (T-004) are **independent operations**. For a video file, the ingest pipeline may call both. This function only handles the visual side.

---

### T-004 · Audio transcription · `captioner.py`

**`transcribe_audio(path: Path) -> str`:**
- If `not detect_capabilities().stt_available` → raise `CaptionError("no STT available")`
- For **audio files**: transcribe directly
- For **video files**: extract audio track first:
  ```python
  proc = subprocess.run(
      ["ffmpeg", "-y", "-i", str(path), "-vn", "-ar", "16000",
       "-ac", "1", "-f", "wav", "pipe:1"],
      capture_output=True, timeout=120
  )
  # write proc.stdout to a temp file, then transcribe
  ```
- Try `faster_whisper` first:
  ```python
  from faster_whisper import WhisperModel
  model = WhisperModel("base", device="auto")
  segments, _ = model.transcribe(str(audio_path))
  text = " ".join(s.text.strip() for s in segments).strip()
  ```
- Fall back to `whisper` (openai-whisper):
  ```python
  import whisper
  model = whisper.load_model("base")
  result = model.transcribe(str(audio_path))
  text = result.get("text", "").strip()
  ```
- Empty result → raise `CaptionError("empty transcript")`
- Return plain string

---

### T-005 · Fallback sentinel · `captioner.py`

**`caption_file(path: Path) -> str | None`** — unified entry point called by ingest:

```python
def caption_file(path: Path) -> str | None:
    """Return text caption/transcript, or None if unavailable.
    Never raises. All errors caught and logged."""
    try:
        category = _get_category(path)
        if category == "image":
            try:
                return caption_image(path)
            except CaptionError as e:
                logger.debug("Image caption failed %s: %s", path.name, e)
                return None
        if category == "audio":
            try:
                return transcribe_audio(path)
            except CaptionError as e:
                logger.debug("Audio transcription failed %s: %s", path.name, e)
                return None
        if category == "video":
            visual, audio = None, None
            try:
                visual = caption_video(path)
            except CaptionError:
                pass
            if detect_capabilities().stt_available:
                try:
                    audio = transcribe_audio(path)
                except CaptionError:
                    pass
            if visual and audio:
                return f"{visual}\n\nAudio: {audio}"
            return visual or audio or None
        return None  # text/document/unknown
    except Exception as e:
        logger.error("Unexpected error captioning %s: %s", path, e)
        return None
```

Helper:
```python
from . import config

def _get_category(path: Path) -> str | None:
    return config.get_media_category(path.suffix.lower())
```

**Critical constraint:** `embedder.embed_image`, `embedder.embed_audio`, `embedder.embed_video`, and `embedder.embed_pdf` are **never called from captioner.py**. The captioner only produces text. The ingest pipeline decides which embed function to call.

---

### T-006 · Caption → embed_text contract · `captioner.py`

Add helper used by ingest to get caption + embedding in one call:

```python
from . import embedder

def get_caption_and_embedding(path: Path, category: str) -> tuple[str, list[float]] | None:
    """Return (caption, embedding) if local captioning succeeded, else None.
    
    When not None: embedding = embed_text(caption) — NOT a binary embed.
    When None: caller should use the appropriate binary embed function.
    """
    caption = caption_file(path)
    if caption is None:
        return None
    embedding = embedder.embed_text(caption)
    return caption, embedding
```

---

### T-007 · Wire captioner into ingest · `ingest.py`

Modify `vector_embedded_finder/ingest.py`. The core change is in `ingest_file`:

**Before** the existing embedding block for image/audio/video, add:
```python
if category in ("image", "audio", "video"):
    from . import captioner
    result = captioner.get_caption_and_embedding(path, category)
    if result is not None:
        caption_text, embedding = result
        doc_text = caption_text[:500]
        metadata["description"] = caption_text
    else:
        # No local caption — fall back to binary embedding
        if category == "image":
            embedding = embedder.embed_image(path)
        elif category == "audio":
            embedding = embedder.embed_audio(path)
        elif category == "video":
            embedding = embedder.embed_video(path)
        doc_text = description or f"{category.capitalize()}: {path.name}"
```

**Text and document paths:** completely unchanged — do not add any captioner code to those branches.

**Public signature stays identical:**
```python
def ingest_file(path: str | Path, source: str = "manual", description: str = "") -> dict:
```

---

### T-008 · Concurrent ingest · `ingest.py` + `config.py`

**Add to `config.py`:**
```python
import os
MAX_CONCURRENT_INGEST = int(os.environ.get("VEF_CONCURRENCY", "10"))
```

**Replace `ingest_directory`'s serial loop:**
```python
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from . import config

def ingest_directory(path, source="manual", recursive=True, progress_callback=None) -> list[dict]:
    path = Path(path).resolve()
    pattern = "**/*" if recursive else "*"
    files = [f for f in sorted(path.glob(pattern)) if f.is_file() and utils.is_supported(f)]
    total = len(files)
    results: list[dict | None] = [None] * total
    callback_lock = threading.Lock()
    completed_count = 0

    def process_file(i: int, file_path: Path) -> tuple[int, dict]:
        try:
            result = ingest_file(file_path, source=source)
        except Exception as e:
            result = {"status": "error", "path": str(file_path), "error": str(e)}
        return i, result

    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_INGEST) as executor:
        futures = {executor.submit(process_file, i, fp): i for i, fp in enumerate(files)}
        for future in as_completed(futures):
            idx, result = future.result()
            results[idx] = result
            with callback_lock:
                nonlocal completed_count  # won't work with nonlocal in closure; use list
                pass
            if progress_callback:
                # progress_callback is called with (files_completed_so_far, total, result)
                # Use a counter — but it must be thread-safe
                progress_callback(futures[future] + 1, total, result)  # approximate order

    return results
```

**Correct thread-safe version:**
```python
def ingest_directory(path, source="manual", recursive=True, progress_callback=None) -> list[dict]:
    path = Path(path).resolve()
    pattern = "**/*" if recursive else "*"
    files = [f for f in sorted(path.glob(pattern)) if f.is_file() and utils.is_supported(f)]
    total = len(files)
    results: list[dict | None] = [None] * total
    completed = [0]
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_INGEST) as executor:
        future_to_idx = {executor.submit(ingest_file, fp, source): i for i, fp in enumerate(files)}
        for future in as_completed(future_to_idx):
            i = future_to_idx[future]
            try:
                result = future.result()
            except Exception as e:
                result = {"status": "error", "path": str(files[i]), "error": str(e)}
            results[i] = result
            if progress_callback:
                with lock:
                    completed[0] += 1
                    n = completed[0]
                progress_callback(n, total, result)

    return results  # type: ignore[return-value]
```

**Key constraints:**
- `total` is computed before any processing starts (from `len(files)`)
- A single file error does NOT abort the batch
- Existing callers that pass only `path` and `source` continue to work

---

### T-009 · Deduplication skip (verify + fix) · `ingest.py`

The existing code already has dedup via `store.exists(doc_id)`. Verify:
1. `doc_id = utils.file_hash(path)` — confirm `utils.file_hash` uses SHA-256 (it does; verify with `hashlib.sha256`)
2. The dedup check comes **before** the captioner call — the order in `ingest_file` must be:
   ```
   1. Resolve path, check exists
   2. Check is_supported
   3. Compute SHA-256 hash (doc_id)
   4. store.exists(doc_id) → return skipped if true
   5. [Then: captioner / embedding / store.add]
   ```
3. Skipped result shape: `{"status": "skipped", "reason": "already embedded", "id": doc_id, "path": str(path)}`

---

### T-010 · Progress callback (verify) · `ingest.py`

After the concurrent refactor, verify:
- Callback is called for **every** file: embedded, skipped, and errored
- Callback signature: `(completed_so_far: int, total: int, result: dict)`
- `total` is the full count known before first call
- Thread-safe: use a lock around the counter (see T-008 implementation above)

---

### T-011 · Backward-compat signatures (verify) · `ingest.py`

After all changes, these must still work exactly:
```python
ingest_file("~/photo.jpg")
ingest_file(Path("/tmp/doc.pdf"), source="watch")
ingest_file("/tmp/doc.pdf", source="watch", description="My document")
ingest_directory("~/Documents/")
ingest_directory("~/Documents/", source="auto", recursive=False, progress_callback=cb)
```

Return keys for success: `status, id, path, category`
Return keys for skip: `status, reason, id, path`
Return keys for error: `status, path, error`

---

### T-012 · Error envelope (verify) · `ingest.py`

Exceptions inside `ingest_file` must be caught and returned as:
```python
{"status": "error", "path": str(file_path), "error": str(exception)}
```
`ingest_directory` must return exactly `len(files)` entries. One per file.

---

## Track B — Search Speed

Build in this order:
- Lane 1: T-013 → T-019 → T-025
- Lane 2: T-014 → T-020
- Lane 3: T-015 → T-017 → T-018
- Lane 4: T-022 (independent)
- Merge: T-021 (needs T-017 + T-020) → T-027

### T-013 · FastAPI search endpoint · `daemon.py` (new file)

Create `vector_embedded_finder/daemon.py`:

```python
"""Persistent search daemon — keeps ChromaDB and Gemini client warm.

Usage:
    vef-daemon start [--background]
    vef-daemon stop
    vef-daemon status
"""
from __future__ import annotations
import os, signal, sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import search, store, embedder

PID_DIR = Path.home() / ".vef"
PID_FILE = PID_DIR / "daemon.pid"
HOST = "127.0.0.1"
PORT = 19847

app = FastAPI()

class SearchRequest(BaseModel):
    query: str
    n_results: int = 5

@app.post("/search")
async def search_endpoint(req: SearchRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")
    try:
        results = search.search(req.query, n_results=req.n_results)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Result fields:** The `search.search()` function already returns dicts with: `id, similarity, file_path, file_name, media_category, timestamp, description, source, preview`. These must pass through unchanged to the Raycast `SearchResult` TypeScript interface.

---

### T-014 · Health endpoint · `daemon.py`

```python
@app.get("/health")
async def health_endpoint():
    try:
        count = store.count()
        return {"status": "ok", "count": count}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})
```

Must respond within 500ms. It will, because ChromaDB connection is pre-warmed at startup (T-016).

---

### T-015 · Localhost binding · `daemon.py`

When starting uvicorn, **always** use `host="127.0.0.1"` and `port=19847`. Never `0.0.0.0`.

Before calling `uvicorn.run`, check port availability:
```python
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((HOST, PORT))
    except OSError:
        print(f"Error: port {PORT} is already in use.", file=sys.stderr)
        sys.exit(1)
```

---

### T-016 · Warm resources at startup · `daemon.py`

Add a FastAPI startup event that pre-initializes ChromaDB and Gemini:

```python
@app.on_event("startup")
async def startup():
    try:
        store._get_collection()   # opens ChromaDB connection
        embedder._get_client()    # initializes Gemini client
    except Exception:
        pass  # Don't crash startup if resources temporarily unavailable
```

This ensures the **first** `/search` request after the daemon starts doesn't pay the initialization cost.

---

### T-017 · CLI start + PID file · `daemon.py`

Add `cli()` function at the bottom of `daemon.py`:

```python
def _write_pid():
    PID_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

def _remove_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

def _check_running() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)   # signal 0 = check if process exists
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        _remove_pid()
        return None

def cli():
    import argparse
    parser = argparse.ArgumentParser(prog="vef-daemon")
    sub = parser.add_subparsers(dest="cmd")
    start_p = sub.add_parser("start")
    start_p.add_argument("--background", action="store_true",
                         help="Start detached (used by Raycast auto-start)")
    sub.add_parser("stop")
    sub.add_parser("status")
    args = parser.parse_args()

    if args.cmd == "start" or args.cmd is None:
        existing = _check_running()
        if existing:
            print(f"Daemon already running (PID {existing})", file=sys.stderr)
            sys.exit(1)

        if getattr(args, "background", False):
            import subprocess
            subprocess.Popen(
                [sys.executable, "-m", "vector_embedded_finder.daemon", "start"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

        signal.signal(signal.SIGTERM, lambda *_: (_remove_pid(), sys.exit(0)))
        signal.signal(signal.SIGINT, lambda *_: (_remove_pid(), sys.exit(0)))
        _write_pid()
        try:
            # Check port first
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((HOST, PORT))
                except OSError:
                    _remove_pid()
                    print(f"Error: port {PORT} is already in use.", file=sys.stderr)
                    sys.exit(1)
            uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
        finally:
            _remove_pid()

    elif args.cmd == "stop":
        pid = _check_running()
        if not pid:
            print("Daemon not running")
            return
        os.kill(pid, signal.SIGTERM)
        print(f"Stopped daemon (PID {pid})")

    elif args.cmd == "status":
        pid = _check_running()
        if pid:
            count = "unknown"
            try:
                import json, urllib.request
                with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=2) as r:
                    count = json.loads(r.read()).get("count", "?")
            except Exception:
                pass
            print(f"Running (PID {pid}, {count} items indexed)")
        else:
            print("Not running")

if __name__ == "__main__":
    cli()
```

Also add module entry point support. Create `vector_embedded_finder/__main__.py` if it doesn't exist, OR simply ensure `python -m vector_embedded_finder.daemon start` works via the `if __name__ == "__main__": cli()` at the bottom.

---

### T-018 · Graceful shutdown · `daemon.py`

The signal handlers in T-017 already cover SIGTERM and SIGINT. Verify:
- Signal handler calls `_remove_pid()` before `sys.exit(0)`
- The `finally: _remove_pid()` block in the `start` path runs on normal exit too
- uvicorn's own shutdown handling completes in-flight requests before the process exits

No additional code needed beyond what T-017 already includes.

---

### T-019 · HTTP-based `runSearch` · `runner.ts`

**Rewrite `raycast/src/lib/runner.ts` completely.** Keep all exported types unchanged. Replace `spawnSync` internals with `fetch`.

```typescript
import { spawnSync, spawn } from "child_process";
import { getPreferenceValues } from "@raycast/api";

// ── Types (unchanged — do not modify these) ───────────────────────────────────
export interface Preferences {
  pythonPackagePath: string;
  pythonPath?: string;
  geminiApiKey?: string;
}

export type VefErrorCode =
  | "NOT_INSTALLED" | "PYTHON_NOT_FOUND" | "AUTH_ERROR"
  | "RATE_LIMIT" | "TIMEOUT" | "UNKNOWN";

export class VefRunnerError extends Error {
  constructor(public readonly code: VefErrorCode, message: string) {
    super(message);
    this.name = "VefRunnerError";
  }
}

export interface SearchResult {
  id: string; similarity: number; file_path: string; file_name: string;
  media_category: string; timestamp: string; description: string;
  source: string; preview: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────
const DAEMON_URL = "http://127.0.0.1:19847";

// ── Helpers ───────────────────────────────────────────────────────────────────
function resolvePrefs(): Preferences { return getPreferenceValues<Preferences>(); }

function resolvePython(prefs: Preferences): string {
  if (prefs.pythonPath?.trim()) return prefs.pythonPath.trim();
  const r = spawnSync("which", ["python3"], { encoding: "utf-8" });
  return r.status === 0 && r.stdout.trim() ? r.stdout.trim() : "python3";
}

async function isDaemonRunning(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 500);
    const res = await fetch(`${DAEMON_URL}/health`, { signal: ctrl.signal });
    return res.ok;
  } catch { return false; }
}

async function startDaemon(prefs: Preferences): Promise<void> {
  const python = resolvePython(prefs);
  const script = `import sys; sys.path.insert(0, '${prefs.pythonPackagePath}'); \
sys.argv = ['vef-daemon', 'start', '--background']; \
from vector_embedded_finder.daemon import cli; cli()`;
  spawn(python, ["-c", script], { detached: true, stdio: "ignore" }).unref();
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 200));
    if (await isDaemonRunning()) return;
  }
  throw new VefRunnerError("TIMEOUT", "Daemon did not start within 5 seconds");
}

async function ensureDaemon(prefs: Preferences): Promise<void> {
  if (!(await isDaemonRunning())) await startDaemon(prefs);
}

// ── Public API ────────────────────────────────────────────────────────────────
export async function runSearch(query: string, nResults = 20): Promise<SearchResult[]> {
  if (!query.trim()) return [];
  const prefs = resolvePrefs();
  await ensureDaemon(prefs);
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), 3000);
  try {
    const res = await fetch(`${DAEMON_URL}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, n_results: nResults }),
      signal: ctrl.signal,
    });
    clearTimeout(id);
    if (!res.ok) throw new VefRunnerError("UNKNOWN", `${res.status}: ${(await res.text()).slice(0, 200)}`);
    return (await res.json()) as SearchResult[];
  } catch (e) {
    clearTimeout(id);
    if (e instanceof VefRunnerError) throw e;
    const msg = String((e as Error).message ?? "");
    if (msg.includes("abort") || msg.includes("timeout")) throw new VefRunnerError("TIMEOUT", "Search timed out after 3s");
    throw new VefRunnerError("UNKNOWN", msg);
  }
}

export async function validateSetup(): Promise<{ count: number }> {
  const prefs = resolvePrefs();
  await ensureDaemon(prefs);
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), 3000);
  try {
    const res = await fetch(`${DAEMON_URL}/health`, { signal: ctrl.signal });
    clearTimeout(id);
    if (!res.ok) throw new VefRunnerError("UNKNOWN", `Health check failed (${res.status})`);
    const data = await res.json() as { status: string; count: number };
    return { count: data.count ?? 0 };
  } catch (e) {
    clearTimeout(id);
    if (e instanceof VefRunnerError) throw e;
    const msg = String((e as Error).message ?? "");
    if (msg.includes("ECONNREFUSED") || msg.includes("connect") || msg.includes("abort")) {
      throw new VefRunnerError("NOT_INSTALLED", "Cannot connect to Recall daemon");
    }
    throw new VefRunnerError("UNKNOWN", msg);
  }
}
```

**CRITICAL:** After rewriting runner.ts, TypeScript-compile the Raycast extension:
```bash
cd raycast && npm run build
```
Fix any type errors before declaring done.

---

### T-020 · HTTP `validateSetup` (included in T-019 above)

Already covered — see the `validateSetup` function in T-019.

---

### T-021 · Auto-start daemon (included in T-019 above)

`ensureDaemon()` and `startDaemon()` in T-019 cover this. The key behaviors:
- Check health with 500ms timeout before every search
- If not running: spawn with `--background` flag, poll for 5 seconds at 200ms intervals
- Timeout error if daemon not up within 5s

---

### T-022 · Error mapping (included in T-019 above)

Error mapping in the `catch` blocks of T-019:
- Connection refused / abort → `NOT_INSTALLED`
- HTTP 4xx/5xx → `UNKNOWN` with response body
- AbortController timeout → `TIMEOUT`
- `VefRunnerError` and `VefErrorCode` type definitions: **do not modify them**

---

### T-025 · No UI changes verification

After completing T-019, verify:
```bash
grep -n "runSearch\|validateSetup" raycast/src/search-memory.tsx
grep -n "runSearch\|validateSetup" raycast/src/open-memory.tsx
```

Both files call `runSearch` and `validateSetup` with `await` already. The async change is transparent. **Zero code changes** to these files. If TypeScript compile fails due to async mismatch, the issue is in `runner.ts`, not the UI files.

---

## Dependencies — Update `pyproject.toml`

Add to `[project.dependencies]`:
```toml
"fastapi>=0.110.0",
"uvicorn[standard]>=0.29.0",
"httpx>=0.27.0",
```

Add to `[project.scripts]`:
```toml
vef-daemon = "vector_embedded_finder.daemon:cli"
```

Add optional group for local AI:
```toml
[project.optional-dependencies]
setup = ["rich>=13.0", "questionary>=2.0"]
local-ai = ["faster-whisper>=1.0.0"]
```

---

## Indexing on First Run — Special Consideration

The user's core complaint is that first-run indexing takes 21 hours. The concurrent ingest (T-008) is the primary fix, but there are additional optimizations for the first run specifically:

**Why first run is slow:**
1. Every file needs an embedding API call (no dedup hits yet)
2. Binary uploads to Gemini are large (images, audio, video)
3. Free-tier Gemini API: ~15 RPM

**How the fix addresses this:**
- Local VLM captions: images/video become text descriptions → tiny API payload → much faster
- Concurrency: 10 parallel embed calls instead of 1
- Combined effect: 10x-50x faster for well-captioned media

**For incremental runs (user downloads a new file):**
- Dedup check (SHA-256) skips already-indexed files without any API call
- Only the new file is processed
- With daemon running: no subprocess startup cost

**Incremental indexing is not yet automatic** (no filesystem watcher in scope). The user must trigger `ingest_directory` manually or via Raycast. This is explicitly out of scope — do not add a watcher.

---

## Verification Checklist

Run these checks after implementing each track. All must pass before the track is complete.

### Track A

```bash
# 1. captioner module imports cleanly
python -c "from vector_embedded_finder.captioner import detect_capabilities, caption_file; print('OK')"

# 2. capability detection works without Ollama
python -c "
from vector_embedded_finder.captioner import detect_capabilities
c = detect_capabilities()
print(f'VLM={c.vlm_available}, STT={c.stt_available}')
c2 = detect_capabilities()
assert c is c2, 'FAIL: not cached'
print('PASS: caching works')
"

# 3. caption_file returns None gracefully with no local AI
python -c "
from pathlib import Path
from vector_embedded_finder.captioner import caption_file
# Create a dummy test image path
result = caption_file(Path('/nonexistent/test.jpg'))
assert result is None, f'Expected None, got {result}'
print('PASS: returns None gracefully')
"

# 4. ingest.py syntax check
python -m py_compile vector_embedded_finder/ingest.py && echo "PASS: ingest.py syntax OK"

# 5. ingest public API still works
python -c "
from vector_embedded_finder.ingest import ingest_file, ingest_directory
import inspect
sig = inspect.signature(ingest_file)
assert 'source' in sig.parameters
assert 'description' in sig.parameters
print('PASS: ingest_file signature OK')
"

# 6. config has MAX_CONCURRENT_INGEST
python -c "
from vector_embedded_finder import config
assert hasattr(config, 'MAX_CONCURRENT_INGEST')
print(f'PASS: MAX_CONCURRENT_INGEST = {config.MAX_CONCURRENT_INGEST}')
"
```

### Track B

```bash
# 1. daemon.py syntax check
python -m py_compile vector_embedded_finder/daemon.py && echo "PASS: daemon.py syntax OK"

# 2. daemon imports cleanly
python -c "from vector_embedded_finder.daemon import app, cli; print('PASS: daemon imports OK')"

# 3. daemon starts and health check works (requires package installed)
pip install -e . -q
vef-daemon start &
DAEMON_PID=$!
sleep 3
curl -s http://127.0.0.1:19847/health | python -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'; print(f'PASS: health OK, {d[\"count\"]} items')"
kill $DAEMON_PID

# 4. Raycast TypeScript compiles
cd raycast && npm run build && echo "PASS: TypeScript build OK"

# 5. Search under 500ms (daemon running)
vef-daemon start &
sleep 2
time curl -s -X POST http://127.0.0.1:19847/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "n_results": 3}'
vef-daemon stop
```

---

## Common Mistakes to Avoid

1. **Do not call `captioner.py` functions from `embedder.py`** — these are one-way. Captioner calls embedder (via `embed_text`). Embedder never calls captioner.

2. **Do not call `caption_file` before the dedup check in `ingest_file`** — always check `store.exists(doc_id)` first. Captioning is expensive; don't do it for files already in the store.

3. **Do not use `0.0.0.0` in the daemon** — localhost only. This is a single-user local tool.

4. **Do not make `runSearch` synchronous again** — it must be `async`. The Raycast UI files already handle async correctly.

5. **Do not modify `search-memory.tsx` or `open-memory.tsx`** — these are verified to work with the async runner as-is.

6. **Do not remove `pythonPackagePath` from Raycast preferences** — the daemon auto-start code needs it to locate the Python package.

7. **Do not add error handling for happy paths** — the captioner already handles all its own errors. `ingest_file` and `ingest_directory` do not need additional try/except wrappers around captioner calls.

8. **Do not change the Gemini embedding model or vector dimensions** — `config.EMBEDDING_MODEL` and `config.EMBEDDING_DIMENSIONS` are set. All embeddings must use the same model/dimensions or ChromaDB will reject them.

---

## File Reference Quick Guide

| What you need | Where to find it |
|---------------|-----------------|
| Supported file extensions by category | `config.SUPPORTED_EXTENSIONS` dict + `config.get_media_category(ext)` |
| How to embed text | `embedder.embed_text(text)` |
| How to embed a query (search time) | `embedder.embed_query(query)` |
| How to check if file is in store | `store.exists(doc_id)` |
| How to add to store | `store.add(doc_id, embedding, metadata, document)` |
| How to search | `search.search(query, n_results)` — returns list of dicts |
| How to count items | `store.count()` |
| How to hash a file | `utils.file_hash(path)` → SHA-256 hex string |
| How to get MIME type | `utils.mime_type(path)` |
| How to check if file is supported | `utils.is_supported(path)` |
| Gemini API key | `config.get_api_key()` |
| ChromaDB storage path | `config.CHROMA_DIR` |

---

## Acceptance Summary

The implementation is complete when:

1. `detect_capabilities()` runs in < 5s and never raises
2. An image file with local Ollama available produces a text caption that gets stored as `description` in the vector store
3. `ingest_directory("~/Documents/")` for 2000 files runs in < 2 hours on an M4 Pro with Ollama
4. `vef-daemon start` starts a server at `localhost:19847`
5. `curl http://127.0.0.1:19847/health` returns `{"status":"ok","count":<n>}`
6. From Raycast with daemon running, a search query returns results in < 500ms
7. Killing the daemon and triggering a search from Raycast: daemon auto-restarts, search completes in < 6s
8. `ingest_file` and `ingest_directory` return the same dict shapes as before
9. `cd raycast && npm run build` passes with no TypeScript errors
10. `search-memory.tsx` and `open-memory.tsx` have zero code changes
