/**
 * VEF runner — all searches go via the persistent daemon over HTTP.
 * Falls back to auto-starting the daemon if it is not running.
 */

import { spawnSync, spawn } from "child_process";
import { getPreferenceValues } from "@raycast/api";

// ── Preferences ──────────────────────────────────────────────────────────────

export interface Preferences {
  /** Absolute path to the directory that contains vector_embedded_finder/ */
  pythonPackagePath: string;
  /** Path to python3 binary. Defaults to "python3". */
  pythonPath?: string;
  /** Gemini API key — stored in macOS Keychain via Raycast password preference */
  geminiApiKey?: string;
}

// ── Error types ───────────────────────────────────────────────────────────────

export type VefErrorCode =
  | "NOT_INSTALLED"    // package not importable from the given path
  | "PYTHON_NOT_FOUND" // python binary not found
  | "AUTH_ERROR"       // GEMINI_API_KEY missing or invalid
  | "RATE_LIMIT"       // 429 / quota exceeded
  | "TIMEOUT"          // request timed out
  | "DAEMON_ERROR"     // daemon returned a non-2xx response
  | "UNKNOWN";         // any other failure

export class VefRunnerError extends Error {
  constructor(
    public readonly code: VefErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "VefRunnerError";
  }
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface SearchResult {
  id: string;
  similarity: number;
  file_path: string;
  file_name: string;
  media_category: string;
  timestamp: string;
  description: string;
  source: string;
  preview: string;
  metadata?: Record<string, unknown>;
}

export interface ConnectorStatus {
  authenticated: boolean;
  last_sync: number;
  last_sync_iso: string | null;
  interval_s: number;
  last_result?: {
    status?: string;
    reason?: string;
    error?: string;
  };
}

export type ConnectorStatusMap = Record<string, ConnectorStatus>;

export interface ProgressInfo {
  indexing: boolean;
  queued: number;
  total_indexed: number;
}

// ── Daemon coordinates ────────────────────────────────────────────────────────

const DAEMON_HOST = "127.0.0.1";
const DAEMON_PORT = 19847;
const BASE_URL = `http://${DAEMON_HOST}:${DAEMON_PORT}`;

// ── Helpers ───────────────────────────────────────────────────────────────────

function abortAfter(ms: number): AbortSignal {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), ms);
  controller.signal.addEventListener("abort", () => clearTimeout(id), { once: true });
  return controller.signal;
}

function resolvePrefs(): Preferences {
  return getPreferenceValues<Preferences>();
}

function resolvePython(prefs: Preferences): string {
  if (prefs.pythonPath?.trim()) return prefs.pythonPath.trim();
  const which = spawnSync("which", ["python3"], { encoding: "utf-8" });
  if (which.status === 0 && which.stdout.trim()) return which.stdout.trim();
  return "python3";
}

async function _pollHealth(timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      // /health is constant-time on the daemon, so a generous per-probe
      // abort is safe and avoids false negatives under macOS CPU contention.
      const resp = await fetch(`${BASE_URL}/health`, { signal: abortAfter(2000) });
      if (resp.ok) return true;
    } catch {
      // not ready yet
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

/**
 * Ensure the daemon is running, starting it if necessary.
 * Polls /health for up to 10 seconds after spawning.
 */
async function ensureDaemon(): Promise<void> {
  // Fast path: already up
  try {
    const resp = await fetch(`${BASE_URL}/health`, { signal: abortAfter(3000) });
    if (resp.ok) return;
  } catch {
    // fall through to start
  }

  // Race guard: if another process is warming up, avoid double-spawn.
  if (await _pollHealth(2000)) return;

  const prefs = resolvePrefs();
  const python = resolvePython(prefs);
  const packagePath = prefs.pythonPackagePath?.trim() || "";

  const env: Record<string, string> = { ...process.env } as Record<string, string>;
  if (prefs.geminiApiKey?.trim()) {
    env["GEMINI_API_KEY"] = prefs.geminiApiKey.trim();
  }
  if (packagePath) {
    const existing = env["PYTHONPATH"] || "";
    env["PYTHONPATH"] = existing ? `${packagePath}:${existing}` : packagePath;
  }

  // Spawn daemon detached so it outlives Raycast.
  // Use async spawn (not spawnSync) to avoid blocking Raycast's main thread.
  let spawnError: string | null = null;
  try {
    const proc = spawn(
      python,
      ["-m", "vector_embedded_finder.daemon", "_serve"],
      {
        detached: true,
        stdio: "ignore",
        env,
        cwd: packagePath || undefined,
      },
    );
    proc.unref(); // allow parent (Raycast) to exit independently
    proc.on("error", (err) => {
      spawnError = err.message;
    });
  } catch (err: unknown) {
    spawnError = err instanceof Error ? err.message : String(err);
  }

  // Poll /health for up to 10 seconds (daemon takes ~1-2s to warm up)
  const ready = await _pollHealth(10000);
  if (!ready) {
    const hint = spawnError ? ` (${spawnError})` : "";
    throw new VefRunnerError(
      "DAEMON_ERROR",
      `Daemon failed to start within 10 s${hint}. Run "vef-daemon start" manually.`,
    );
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export interface RunSearchOptions {
  nResults?: number;
  sources?: string[] | null;
}

function formatDetail(detail: unknown): string {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg?: unknown }).msg ?? "");
        }
        return JSON.stringify(item);
      })
      .filter(Boolean);
    return msgs.join("; ");
  }
  if (typeof detail === "object") return JSON.stringify(detail);
  return String(detail);
}

/**
 * Run a semantic search via the VEF daemon.
 *
 * @throws {VefRunnerError} with appropriate code on any failure
 */
export async function runSearch(
  query: string,
  nResultsOrOptions: number | RunSearchOptions = 20,
): Promise<SearchResult[]> {
  if (!query.trim()) return [];

  const opts: RunSearchOptions =
    typeof nResultsOrOptions === "number"
      ? { nResults: nResultsOrOptions }
      : nResultsOrOptions;

  const nResults = opts.nResults ?? 20;
  const sources = opts.sources ?? null;

  await ensureDaemon();

  const body: Record<string, unknown> = { query, n_results: nResults };
  if (sources && sources.length > 0) body.sources = sources;

  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: abortAfter(30000),
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("TimeoutError") || msg.includes("timed out") || msg.includes("ETIMEDOUT")) {
      throw new VefRunnerError("TIMEOUT", "Search timed out after 30 s");
    }
    throw new VefRunnerError("UNKNOWN", `Fetch failed: ${msg}`);
  }

  if (!resp.ok) {
    let detail = "";
    try {
      const payload = (await resp.json()) as { detail?: unknown };
      detail = formatDetail(payload.detail);
    } catch {
      // ignore parse error
    }
    const err = `Daemon returned HTTP ${resp.status}${detail ? `: ${detail}` : ""}`;
    if (resp.status === 401 || resp.status === 403) {
      throw new VefRunnerError("AUTH_ERROR", err);
    }
    if (resp.status === 429) {
      throw new VefRunnerError("RATE_LIMIT", err);
    }
    throw new VefRunnerError("DAEMON_ERROR", err);
  }

  const results = (await resp.json()) as SearchResult[];
  return results;
}

/**
 * Validate that the daemon is reachable and the DB has items.
 * Returns the item count in the vector store.
 *
 * @throws {VefRunnerError} with appropriate code on any failure
 */
export async function validateSetup(): Promise<{ count: number }> {
  await ensureDaemon();

  // Use /stats (which reports count) rather than /health (liveness-only).
  // /stats may be slow when chromadb is backlogged, so give it room.
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}/stats`, { signal: abortAfter(5000) });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new VefRunnerError("UNKNOWN", `Stats check failed: ${msg}`);
  }

  if (!resp.ok) {
    throw new VefRunnerError("DAEMON_ERROR", `Daemon stats returned HTTP ${resp.status}`);
  }

  const data = (await resp.json()) as { status?: string; count?: number };
  return { count: typeof data.count === "number" ? data.count : 0 };
}

/**
 * Fetch the list of indexed sources from the daemon.
 */
export async function fetchSources(): Promise<string[]> {
  try {
    await ensureDaemon();
    const resp = await fetch(`${BASE_URL}/sources`, { signal: abortAfter(2000) });
    if (!resp.ok) return [];
    const data = (await resp.json()) as { sources: string[] };
    return data.sources;
  } catch {
    return [];
  }
}

/**
 * Fetch per-connector status from the daemon.
 */
export async function fetchConnectorStatus(): Promise<ConnectorStatusMap> {
  await ensureDaemon();
  const resp = await fetch(`${BASE_URL}/connector-status`, { signal: abortAfter(3000) });
  if (!resp.ok) {
    throw new VefRunnerError("DAEMON_ERROR", `Connector status request failed: HTTP ${resp.status}`);
  }
  return (await resp.json()) as ConnectorStatusMap;
}

/**
 * Fetch indexing progress from the daemon.
 */
export async function fetchProgress(): Promise<ProgressInfo> {
  await ensureDaemon();
  const resp = await fetch(`${BASE_URL}/progress`, { signal: abortAfter(3000) });
  if (!resp.ok) {
    throw new VefRunnerError("DAEMON_ERROR", `Progress request failed: HTTP ${resp.status}`);
  }
  return (await resp.json()) as ProgressInfo;
}

/**
 * Trigger immediate connector sync. Returns immediately (daemon handles in_progress).
 */
export async function triggerSync(source?: string): Promise<{ status: string; last_sync: Record<string, unknown> }> {
  await ensureDaemon();
  const payload = source ? { source } : {};
  const resp = await fetch(`${BASE_URL}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: abortAfter(30000),
  });
  if (!resp.ok) {
    let detail = "";
    try {
      const payloadErr = (await resp.json()) as { detail?: unknown };
      detail = formatDetail(payloadErr.detail);
    } catch {
      // ignore
    }
    throw new VefRunnerError("DAEMON_ERROR", `Sync request failed: HTTP ${resp.status}${detail ? `: ${detail}` : ""}`);
  }
  return (await resp.json()) as { status: string; last_sync: Record<string, unknown> };
}

/**
 * Check whether a connector sync is actively running.
 */
export async function fetchSyncRunning(): Promise<boolean> {
  try {
    const resp = await fetch(`${BASE_URL}/sync-running`, { signal: abortAfter(3000) });
    if (!resp.ok) return false;
    const data = (await resp.json()) as { running: boolean };
    return data.running;
  } catch {
    return false;
  }
}

/**
 * Fetch list of watched directories.
 */
export async function fetchWatchedDirs(): Promise<string[]> {
  await ensureDaemon();
  const resp = await fetch(`${BASE_URL}/watched-dirs`, { signal: abortAfter(3000) });
  if (!resp.ok) return [];
  const data = (await resp.json()) as { dirs: string[] };
  return data.dirs;
}

/**
 * Add a directory to the watched list.
 */
export async function addWatchedDir(path: string): Promise<string[]> {
  await ensureDaemon();
  const resp = await fetch(`${BASE_URL}/watched-dirs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
    signal: abortAfter(5000),
  });
  if (!resp.ok) throw new VefRunnerError("DAEMON_ERROR", `Failed to add directory: HTTP ${resp.status}`);
  const data = (await resp.json()) as { dirs: string[] };
  return data.dirs;
}

/**
 * Remove a directory from the watched list.
 */
export async function removeWatchedDir(path: string): Promise<string[]> {
  await ensureDaemon();
  const resp = await fetch(`${BASE_URL}/watched-dirs`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
    signal: abortAfter(5000),
  });
  if (!resp.ok) throw new VefRunnerError("DAEMON_ERROR", `Failed to remove directory: HTTP ${resp.status}`);
  const data = (await resp.json()) as { dirs: string[] };
  return data.dirs;
}

/**
 * Save API keys / config to daemon (persists to ~/.vef/.env).
 */
export async function saveConfigure(cfg: {
  gemini_api_key?: string;
  canvas_api_key?: string;
  canvas_base_url?: string;
  schoology_consumer_key?: string;
  schoology_consumer_secret?: string;
}): Promise<void> {
  await ensureDaemon();
  const resp = await fetch(`${BASE_URL}/configure`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
    signal: abortAfter(5000),
  });
  if (!resp.ok) throw new VefRunnerError("DAEMON_ERROR", `Configure failed: HTTP ${resp.status}`);
}

/**
 * Run trayce connect <source> in Terminal via AppleScript.
 */
export async function connectInTerminal(source: string): Promise<void> {
  const { execFile } = await import("node:child_process");
  await new Promise<void>((resolve, reject) => {
    execFile(
      "osascript",
      ["-e", `tell application "Terminal" to do script "trayce connect ${source}"`],
      (err) => (err ? reject(err) : resolve()),
    );
  });
}

/**
 * Trigger index of a local directory path in Terminal.
 */
export async function indexFolderInTerminal(path: string): Promise<void> {
  const { execFile } = await import("node:child_process");
  const escaped = path.replace(/"/g, '\\"');
  await new Promise<void>((resolve, reject) => {
    execFile(
      "osascript",
      ["-e", `tell application "Terminal" to do script "trayce index \\"${escaped}\\""`],
      (err) => (err ? reject(err) : resolve()),
    );
  });
}
