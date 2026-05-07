"""Keychain-backed storage for connector credentials."""

from __future__ import annotations

import getpass
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from . import config

logger = logging.getLogger(__name__)

SERVICE_PREFIX = "com.recall.credentials"
DEFAULT_ACCOUNT = getpass.getuser()


def service_name(source: str) -> str:
    return f"{SERVICE_PREFIX}.{source}"


def _security_available() -> bool:
    return config.sys_platform_is_macos() and shutil.which("security") is not None


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _iter_service_names(source: str, aliases: Iterable[str]) -> list[str]:
    names = [source, *aliases]
    return [service_name(name) for name in names]


def get_secret(
    source: str,
    *,
    aliases: Iterable[str] = (),
    account: str = DEFAULT_ACCOUNT,
) -> str | None:
    if not _security_available():
        return None

    for name in _iter_service_names(source, aliases):
        result = _run_security(["find-generic-password", "-a", account, "-s", name, "-w"])
        if result.returncode == 0:
            return result.stdout.rstrip("\n")
    return None


def set_secret(source: str, secret: str, *, account: str = DEFAULT_ACCOUNT) -> None:
    if not _security_available():
        raise RuntimeError("macOS Keychain unavailable")

    result = _run_security(["add-generic-password", "-U", "-a", account, "-s", service_name(source), "-w", secret])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Failed to store credentials for {source}")


def delete_secret(source: str, *, account: str = DEFAULT_ACCOUNT) -> None:
    if not _security_available():
        return
    _run_security(["delete-generic-password", "-a", account, "-s", service_name(source)])


def _load_legacy_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text())
    return payload if isinstance(payload, dict) else None


def _delete_legacy_file(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("Could not delete legacy credential file %s: %s", path, exc)


def load_json(
    source: str,
    *,
    legacy_path: Path | None = None,
    aliases: Iterable[str] = (),
    account: str = DEFAULT_ACCOUNT,
) -> dict[str, Any] | None:
    raw = get_secret(source, aliases=aliases, account=account)
    if raw:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None

    payload = _load_legacy_json(legacy_path)
    if payload is None:
        return None

    if _security_available():
        set_secret(source, json.dumps(payload, sort_keys=True), account=account)
        _delete_legacy_file(legacy_path)

    return payload


def save_json(
    source: str,
    payload: dict[str, Any],
    *,
    legacy_path: Path | None = None,
    account: str = DEFAULT_ACCOUNT,
) -> None:
    if _security_available():
        set_secret(source, json.dumps(payload, sort_keys=True), account=account)
        _delete_legacy_file(legacy_path)
        return

    if legacy_path is None:
        raise RuntimeError("No fallback credential path available")
    config.ensure_runtime_dirs()
    legacy_path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def migrate_legacy_credentials() -> dict[str, Any]:
    migrated: list[str] = []
    skipped: list[str] = []

    specs = {
        "gmail": config.GMAIL_CREDENTIALS_FILE,
        "canvas": config.CANVAS_CREDENTIALS_FILE,
        "calai": config.CALAI_CREDENTIALS_FILE,
        "schoology": config.SCHOOLOGY_CREDENTIALS_FILE,
        "notion": config.NOTION_CREDENTIALS_FILE,
    }

    for source, path in specs.items():
        if not path.exists():
            continue
        try:
            payload = _load_legacy_json(path)
            if payload is None:
                skipped.append(source)
                continue
            save_json(source, payload, legacy_path=path)
            migrated.append(source)
        except Exception as exc:
            skipped.append(source)
            logger.warning("Credential migration failed for %s: %s", source, exc)

    return {"migrated": migrated, "skipped": skipped}
