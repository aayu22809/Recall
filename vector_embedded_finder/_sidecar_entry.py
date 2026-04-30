"""PyInstaller entry point for the bundled recall-daemon sidecar.

Tauri spawns this binary with `_serve` as its first argument. We delegate
straight to `vector_embedded_finder.daemon.main()` so the entry behaves
identically to the `vef-daemon` console script. The only reason this file
exists separately is that PyInstaller's `--onefile` mode wants a real .py
file as the root entry — pointing it at a `python -m` import path is
unreliable across PyInstaller versions.
"""

from __future__ import annotations

import sys


def _main() -> None:
    from vector_embedded_finder.daemon import main as daemon_main

    if not sys.argv[1:]:
        sys.argv.append("_serve")
    daemon_main()


if __name__ == "__main__":
    _main()
