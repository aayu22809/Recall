# Daemon HTTP API

The daemon binds `127.0.0.1:19847` (override with `VEF_PORT`). Every endpoint accepts and returns JSON.

> **Liveness vs readiness.** `GET /health` is constant-time — use it for probes. `GET /stats` calls `chromadb.count()` and can take 100s of milliseconds when the DB is backlogged. Do not use `/stats` for liveness.

## Authentication

None. The server only listens on `127.0.0.1`, and OS-level process isolation is the trust boundary.

## Endpoints

### `GET /health`

Liveness. Constant-time.

```console
$ curl -s localhost:19847/health
{"status": "ok"}
```

Timeout guidance: 2 s is generous. If this endpoint is slow, something is deeply wrong (CPU saturation, GC stall).

### `GET /stats`

Readiness + indexed-document count. May be slow under heavy ingest.

```console
$ curl -s localhost:19847/stats
{"status": "ok", "count": 1086}
```

### `POST /search`

Top-K semantic search with optional source filter.

```console
$ curl -s -XPOST localhost:19847/search \
    -H 'content-type: application/json' \
    -d '{"query": "golden gate bridge photo", "n_results": 3}'
```

Request:

```json
{
  "query": "string (required)",
  "n_results": 10,
  "sources": ["files", "gmail", "gcal", "gdrive", "calai", "canvas", "schoology", "notion"]
}
```

Response — array of:

```json
{
  "id": "sha256:...",
  "distance": 0.132,
  "metadata": {
    "path": "/Users/aayu/Photos/IMG_4281.HEIC",
    "source": "files",
    "mime": "image/heic",
    "caption": "Golden Gate Bridge at sunset…",
    "modified": 1713456789
  }
}
```

Lower `distance` = more similar (cosine).

### `POST /ingest`

Single-file ingest (async inside the daemon).

```console
$ curl -s -XPOST localhost:19847/ingest \
    -H 'content-type: application/json' \
    -d '{"path": "/Users/aayu/Downloads/report.pdf", "source": "files"}'
{"status": "embedded", "chunks": 4, "ms": 742}
```

Statuses: `embedded`, `duplicate` (sha256 already indexed), `skipped` (unsupported extension), `error`.

### `POST /sync`

Trigger a background connector sync. Returns immediately.

```console
$ curl -s -XPOST localhost:19847/sync \
    -H 'content-type: application/json' -d '{"source": "gmail"}'
{"status": "started", "last_sync": {"gmail": {...}, ...}}
```

Omit the body to sync every configured connector:

```console
$ curl -s -XPOST localhost:19847/sync
{"status": "started", "last_sync": {...}}
```

If a sync is already holding the global lock, returns `{"status": "in_progress"}`.

### `GET /sync-running`

```json
{"running": false}
```

### `GET /connector-status`

```json
{
  "gmail":     {"authenticated": true,  "last_sync": 1713457820.1, "last_sync_iso": "2026-04-22T00:10:20+00:00", "interval_s": 900, "last_result": {"new": 24, "took_s": 8.4}},
  "gcal":      {"authenticated": true,  "last_sync": 1713456100.0, "interval_s": 1800, ...},
  "calai":     {"authenticated": false, "last_sync": 0, ...},
  ...
}
```

### `GET /progress`

In-flight ingest counters.

```json
{"indexing": true, "queued": 7, "total_indexed": 1086}
```

### `GET /sources`

Known source tags (populated as data is ingested).

```json
{"sources": ["calai", "canvas", "files", "gcal", "gdrive", "gmail", "notion"]}
```

### `GET /watched-dirs`

```json
{"dirs": ["/Users/aayu/Documents", "/Users/aayu/Projects"]}
```

### `POST /watched-dirs`

Add a watched folder.

```console
$ curl -s -XPOST localhost:19847/watched-dirs \
    -H 'content-type: application/json' \
    -d '{"path": "~/Desktop/inbox"}'
{"dirs": ["/Users/aayu/Documents", "/Users/aayu/Projects", "/Users/aayu/Desktop/inbox"]}
```

Path is expanded (`~`) and resolved before being written to `~/.vef/watched_dirs.json`.

### `DELETE /watched-dirs`

```console
$ curl -s -XDELETE localhost:19847/watched-dirs \
    -H 'content-type: application/json' \
    -d '{"path": "/Users/aayu/Desktop/inbox"}'
```

### `POST /configure`

Persist API keys into `~/.vef/.env` (survives daemon restarts).

```json
{
  "gemini_api_key": "AIza...",
  "canvas_api_key": "1234~abcd",
  "canvas_base_url": "https://canvas.instructure.com",
  "schoology_consumer_key":    "...",
  "schoology_consumer_secret": "..."
}
```

Response:

```json
{"ok": true}
```

## Error shape

All error responses use FastAPI's default envelope:

```json
{"detail": "human-readable or validation-error array"}
```

Non-2xx status codes:

| Code | Meaning |
|---|---|
| 400 | Invalid source name, missing required field |
| 422 | Pydantic validation error (body shape) |
| 500 | Embedder error, ChromaDB error, generic unhandled exception |

## Throughput and guardrails

None. The daemon is local; there is no per-client throttling. Internally, ingestion is bounded by `VEF_CONCURRENCY` (default 10) and CPU/RAM guards.

## Observability

- **Logs**: `~/.vef/daemon.log` (rotated, 2 MB × 3 backups).
- **Repeated warnings** (e.g. ChromaDB "count failed") are throttled to once per 60 s to avoid log spam.
- Tail it live:

  ```console
  $ tail -F ~/.vef/daemon.log
  ```
