# Recall — macOS app (v0.4.0)

A native Tauri 2 shell over the Recall Python daemon. All onboarding, OAuth,
sync control, and search happen inside the app. Zero terminal, zero Cloud
Console.

```
app/
├── src-tauri/        # Rust shell (daemon lifecycle, OAuth, keychain, tray, hotkey)
├── src/              # React 19 + Tailwind UI (Zed-style)
└── scripts/          # PyInstaller bundle script
```

## Local dev

```bash
# from app/
npm install
npm run tauri:dev
```

The Rust shell will:

1. Probe `127.0.0.1:19847` for an existing recall-daemon. If absent, spawn
   the bundled sidecar (in dev: from a venv on PATH; in production: from the
   PyInstaller binary at `src-tauri/binaries/recall-daemon-<triple>`).
2. Open the main window. Onboarding shows on first launch.
3. Register ⌥Space as a global hotkey for the spotlight overlay.

### Google OAuth credentials

Set these env vars in your shell or in a `.cargo/config.toml` build entry
before `npm run tauri:dev`:

```
RECALL_GOOGLE_CLIENT_ID=<your client id>.apps.googleusercontent.com
RECALL_GOOGLE_CLIENT_SECRET=<your client secret>
```

The client must be of type **Desktop app** in Google Cloud Console with
Gmail / Calendar / Drive read-only scopes enabled.

For v0.4.0 release builds the project distributes a Recall-team OAuth client
in Google "Testing" mode (≤100 hand-picked beta users, 7-day refresh expiry)
while we complete Google's security review.

## Production build

```bash
# 1. Bundle the Python daemon into a single sidecar binary.
app/scripts/build-sidecar.sh aarch64
app/scripts/build-sidecar.sh x86_64

# 2. Build, sign, notarize.
export CODESIGN_IDENTITY="Developer ID Application: <name> (TEAMID)"
npm run tauri:build -- --target universal-apple-darwin
xcrun notarytool submit ./src-tauri/target/universal-apple-darwin/release/bundle/dmg/Recall_0.4.0_universal.dmg \
  --keychain-profile recall-notary --wait
xcrun stapler staple ./src-tauri/target/universal-apple-darwin/release/bundle/dmg/Recall_0.4.0_universal.dmg
```

## Verification (no terminal, no Cloud Console)

After installing the signed DMG on a fresh Mac account:

1. Open. Gatekeeper accepts.
2. Onboarding: provider key → folder → connectors (one-tap Google, paste
   tokens for Notion / Canvas / Schoology) → first sync.
3. Press ⌥Space anywhere on macOS → spotlight overlay → search → enter →
   file opens.
4. Disconnect + re-auth Gmail entirely from the Sources panel.

If those four flows pass without you ever opening Terminal or
console.cloud.google.com, v0.4.0 is shippable.
