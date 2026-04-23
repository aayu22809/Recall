#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "python3 is required but not found on PATH." >&2
  exit 1
fi

"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")
PY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/pyproject.toml" && -d "${SCRIPT_DIR}/vector_embedded_finder" ]]; then
  SRC_DIR="${SCRIPT_DIR}"
else
  SRC_DIR="${HOME}/.trayce-src/Recall"
  mkdir -p "${HOME}/.trayce-src"
  if [[ -d "${SRC_DIR}/.git" ]]; then
    git -C "${SRC_DIR}" pull --ff-only
  else
    git clone --depth=1 https://github.com/aayu22809/Recall.git "${SRC_DIR}"
  fi
fi

echo "Installing Recall from ${SRC_DIR} ..."
"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install -e "${SRC_DIR}[setup]"

echo
echo "Running setup wizard ..."
if ! vef-setup; then
  "${PYTHON_BIN}" -m setup_wizard
fi

echo
echo "Starting daemon ..."
if ! vef-daemon start; then
  "${PYTHON_BIN}" -m vector_embedded_finder.daemon start
fi

TRAYCE_BIN="$(command -v trayce || true)"
if [[ -n "${TRAYCE_BIN}" ]]; then
  if [[ -w "/usr/local/bin" ]]; then
    ln -sf "${TRAYCE_BIN}" /usr/local/bin/trayce
  elif [[ ! -e "/usr/local/bin/trayce" ]]; then
    echo "Note: /usr/local/bin is not writable; skipping trayce symlink."
  fi
else
  USER_BASE="$("${PYTHON_BIN}" -m site --user-base)"
  LOCAL_BIN="${USER_BASE}/bin"
  if [[ -d "${LOCAL_BIN}" ]]; then
    export PATH="${LOCAL_BIN}:${PATH}"
    if ! grep -q "${LOCAL_BIN}" "${HOME}/.zshrc" 2>/dev/null; then
      echo "export PATH=\"${LOCAL_BIN}:\$PATH\"" >> "${HOME}/.zshrc"
      echo "Added ${LOCAL_BIN} to ~/.zshrc"
    fi
  fi
fi

echo
echo "Install complete."
echo "Run: trayce status"
echo "Raycast extension: cd \"${SRC_DIR}/raycast\" && npm install && npm run build"
