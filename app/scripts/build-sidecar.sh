#!/usr/bin/env bash
# Build the recall-daemon sidecar binary that ships inside Recall.app.
#
# Usage: app/scripts/build-sidecar.sh [aarch64|x86_64]
#
# The output binary lands at:
#   app/src-tauri/binaries/recall-daemon-<triple>
#
# Tauri's bundler picks it up automatically by the "externalBin" entry in
# tauri.conf.json. Running this script before `tauri build` is required.
#
# After PyInstaller finishes, we deep-sign every nested .dylib/.so so that
# macOS's hardened runtime accepts the bundled .app. Signing is skipped if
# CODESIGN_IDENTITY is unset (useful for local debug builds).
#
# Requires:
#   - Python 3.11
#   - pip install pyinstaller
#   - The repo's `pyproject.toml` deps installed into the active venv

set -euo pipefail

ARCH="${1:-$(uname -m)}"
case "$ARCH" in
  aarch64|arm64) TRIPLE="aarch64-apple-darwin"; PY_ARCH="arm64" ;;
  x86_64|amd64)  TRIPLE="x86_64-apple-darwin"; PY_ARCH="x86_64" ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 1 ;;
esac

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_ROOT="$REPO_ROOT/app"
DIST_DIR="$APP_ROOT/src-tauri/binaries"
BUILD_DIR="$APP_ROOT/.build/sidecar-$TRIPLE"
SMOKE_DIR="$BUILD_DIR/smoke"

echo "[recall-daemon] building $TRIPLE → $DIST_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR" "$SMOKE_DIR"

# 1. PyInstaller one-file build of vector_embedded_finder.daemon.
cd "$REPO_ROOT"
python -m PyInstaller \
  --noconfirm \
  --onefile \
  --name "recall-daemon-$TRIPLE" \
  --target-architecture "$PY_ARCH" \
  --hidden-import "vector_embedded_finder.daemon" \
  --hidden-import "vector_embedded_finder.connectors.gmail" \
  --hidden-import "vector_embedded_finder.connectors.gcal" \
  --hidden-import "vector_embedded_finder.connectors.gdrive" \
  --hidden-import "vector_embedded_finder.connectors.calai" \
  --hidden-import "vector_embedded_finder.connectors.canvas" \
  --hidden-import "vector_embedded_finder.connectors.schoology" \
  --hidden-import "vector_embedded_finder.connectors.notion" \
  --collect-all chromadb \
  --collect-all rfc3987_syntax \
  --collect-data chromadb \
  --collect-data google \
  --collect-data faster_whisper \
  --workpath "$BUILD_DIR/work" \
  --specpath "$BUILD_DIR" \
  --distpath "$DIST_DIR" \
  vector_embedded_finder/_sidecar_entry.py

OUT="$DIST_DIR/recall-daemon-$TRIPLE"
[[ -x "$OUT" ]] || { echo "build failed: $OUT not present"; exit 2; }

# 2. Deep-sign nested dylibs/.so files + the binary itself.
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  echo "[recall-daemon] signing with $CODESIGN_IDENTITY"
  ENT="$APP_ROOT/src-tauri/entitlements.plist"
  codesign --force --deep --options runtime --timestamp \
           --entitlements "$ENT" \
           --sign "$CODESIGN_IDENTITY" "$OUT"
else
  echo "[recall-daemon] CODESIGN_IDENTITY not set — skipping signing (debug build)"
fi

SMOKE_OLLAMA_PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"
SMOKE_DAEMON_PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"
SMOKE_OLLAMA_LOG="$SMOKE_DIR/fake-ollama.log"
SMOKE_DAEMON_LOG="$SMOKE_DIR/daemon-smoke.log"

python3 - <<PY >"$SMOKE_OLLAMA_LOG" 2>&1 &
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/tags":
            payload = json.dumps({"models": [{"name": "nomic-embed-text"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return

HTTPServer(("127.0.0.1", ${SMOKE_OLLAMA_PORT}), Handler).serve_forever()
PY
FAKE_OLLAMA_PID=$!

cleanup() {
  kill "${DAEMON_PID:-}" "${FAKE_OLLAMA_PID:-}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

python3 - <<PY
import sys
import time
import urllib.request

url = "http://127.0.0.1:${SMOKE_OLLAMA_PORT}/api/tags"
deadline = time.time() + 10
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            if resp.status == 200:
                sys.exit(0)
    except Exception:
        time.sleep(0.1)
sys.exit(1)
PY

VEF_EMBEDDING_PROVIDER=ollama \
VEF_OLLAMA_BASE_URL="http://127.0.0.1:${SMOKE_OLLAMA_PORT}" \
VEF_OLLAMA_EMBED_URL="http://127.0.0.1:${SMOKE_OLLAMA_PORT}/api/embeddings" \
VEF_OLLAMA_EMBED_MODEL="nomic-embed-text" \
VEF_PORT="${SMOKE_DAEMON_PORT}" \
RECALL_PORT="${SMOKE_DAEMON_PORT}" \
"$OUT" _serve >"$SMOKE_DAEMON_LOG" 2>&1 &
DAEMON_PID=$!

if ! python3 - <<PY
import sys
import time
import urllib.request

url = "http://127.0.0.1:${SMOKE_DAEMON_PORT}/health"
deadline = time.time() + 20
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            if resp.status == 200:
                sys.exit(0)
    except Exception:
        time.sleep(0.25)
sys.exit(1)
PY
then
  echo "[recall-daemon] smoke test failed; daemon output:" >&2
  cat "$SMOKE_DAEMON_LOG" >&2 || true
  exit 1
fi

kill "$DAEMON_PID" >/dev/null 2>&1 || true
wait "$DAEMON_PID" || true
kill "$FAKE_OLLAMA_PID" >/dev/null 2>&1 || true
wait "$FAKE_OLLAMA_PID" || true
trap - EXIT

echo "[recall-daemon] done: $OUT"
