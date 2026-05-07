from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vector_embedded_finder import keychain


def test_load_json_migrates_legacy_file_to_keychain(monkeypatch: Any, tmp_path: Path) -> None:
    legacy = tmp_path / "gmail.json"
    legacy.write_text(json.dumps({"token": "abc"}))

    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> Any:
        calls.append(args)
        if args[0] == "find-generic-password":
            return type("Result", (), {"returncode": 44, "stdout": "", "stderr": "not found"})()
        if args[0] == "add-generic-password":
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(keychain, "_security_available", lambda: True)
    monkeypatch.setattr(keychain, "_run_security", fake_run)

    payload = keychain.load_json("gmail", legacy_path=legacy)

    assert payload == {"token": "abc"}
    assert not legacy.exists()
    assert calls == [
        ["find-generic-password", "-a", keychain.DEFAULT_ACCOUNT, "-s", "com.recall.credentials.gmail", "-w"],
        [
            "add-generic-password",
            "-U",
            "-a",
            keychain.DEFAULT_ACCOUNT,
            "-s",
            "com.recall.credentials.gmail",
            "-w",
            json.dumps({"token": "abc"}, sort_keys=True),
        ],
    ]


def test_load_json_prefers_existing_keychain_secret(monkeypatch: Any, tmp_path: Path) -> None:
    legacy = tmp_path / "gmail.json"
    legacy.write_text(json.dumps({"token": "legacy"}))

    def fake_run(args: list[str]) -> Any:
        if args[0] == "find-generic-password":
            return type("Result", (), {"returncode": 0, "stdout": json.dumps({"token": "secure"}), "stderr": ""})()
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(keychain, "_security_available", lambda: True)
    monkeypatch.setattr(keychain, "_run_security", fake_run)

    payload = keychain.load_json("gmail", legacy_path=legacy)

    assert payload == {"token": "secure"}
    assert legacy.exists()
