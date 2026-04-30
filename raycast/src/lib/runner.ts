/**
 * Recall runner — all daemon traffic goes over the Unix domain socket.
 * Falls back to auto-starting the daemon if it is not running.
 */

import http from "http";
import os from "os";
import { spawnSync, spawn } from "child_process";
import { getPreferenceValues } from "@raycast/api";

export interface Preferences {
  pythonPackagePath: string;
  pythonPath?: string;
  geminiApiKey?: string;
}

export type VefErrorCode =
  | "NOT_INSTALLED"
  | "PYTHON_NOT_FOUND"
  | "AUTH_ERROR"
  | "RATE_LIMIT"
  | "TIMEOUT"
  | "DAEMON_ERROR"
  | "UNKNOWN";

export class VefRunnerError extends Error {
  constructor(
    public readonly code: VefErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "VefRunnerError";
  }
}

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

const SOCKET_PATH = process.env.RECALL_SOCKET_PATH?.trim() || `${os.homedir()}/.recall/recall.sock`;

function resolvePrefs(): Preferences {
  return getPreferenceValues<Preferences>();
}

function resolvePython(prefs: Preferences): string {
  if (prefs.pythonPath?.trim()) return prefs.pythonPath.trim();
  const which = spawnSync("which", ["python3"], { encoding: "utf-8" });
  if (which.status === 0 && which.stdout.trim()) return which.stdout.trim();
  return "python3";
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatDetail(detail: unknown): string {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => formatDetail(item)).filter(Boolean).join("; ");
  if (typeof detail === "object") return JSON.stringify(detail);
  return String(detail);
}

function requestJson<T>(
  path: string,
  options: { method?: string; body?: unknown; timeoutMs?: number } = {},
): Promise<{ statusCode: number; body: T }> {
  const method = options.method ?? "GET";
  const timeoutMs = options.timeoutMs ?? 5000;
  const bodyText = options.body === undefined ? undefined : JSON.stringify(options.body);

  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        socketPath: SOCKET_PATH,
        path,
        method,
        headers: bodyText
          ? {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(bodyText),
            }
          : undefined,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf-8");
          let parsed: unknown = {};
          if (raw.trim()) {
            try {
              parsed = JSON.parse(raw);
            } catch {
              parsed = raw;
            }
          }
          resolve({ statusCode: res.statusCode ?? 0, body: parsed as T });
        });
      },
    );

    const timer = setTimeout(() => {
      req.destroy(new Error(`Request timed out after ${timeoutMs} ms`));
    }, timeoutMs);

    req.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
    req.on("close", () => clearTimeout(timer));

    if (bodyText) req.write(bodyText);
    req.end();
  });
}

async function pollHealth(timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const resp = await requestJson<{ status?: string }>("/health", { timeoutMs: 2000 });
      if (resp.statusCode >= 200 && resp.statusCode < 300) return true;
    } catch {
      // keep polling
    }
    await delay(400);
  }
  return false;
}

async function ensureDaemon(): Promise<void> {
  try {
    const resp = await requestJson<{ status?: string }>("/health", { timeoutMs: 3000 });
    if (resp.statusCode >= 200 && resp.statusCode < 300) return;
  } catch {
    // fall through to start
  }

  if (await pollHealth(2000)) return;

  const prefs = resolvePrefs();
  const python = resolvePython(prefs);
  const packagePath = prefs.pythonPackagePath?.trim() || "";
  const env: Record<string, string> = { ...process.env } as Record<string, string>;
  if (prefs.geminiApiKey?.trim()) env["GEMINI_API_KEY"] = prefs.geminiApiKey.trim();
  if (packagePath) {
    const existing = env["PYTHONPATH"] || "";
    env["PYTHONPATH"] = existing ? `${packagePath}:${existing}` : packagePath;
  }

  let spawnError: string | null = null;
  try {
    const proc = spawn(python, ["-m", "vector_embedded_finder.daemon", "_serve"], {
      detached: true,
      stdio: "ignore",
      env,
      cwd: packagePath || undefined,
    });
    proc.unref();
    proc.on("error", (err) => {
      spawnError = err.message;
    });
  } catch (err: unknown) {
    spawnError = err instanceof Error ? err.message : String(err);
  }

  const ready = await pollHealth(15000);
  if (!ready) {
    const hint = spawnError ? ` (${spawnError})` : "";
    throw new VefRunnerError(
      "DAEMON_ERROR",
      `Daemon failed to start within 15 s${hint}. Run "vef-daemon start" manually.`,
    );
  }
}

export interface RunSearchOptions {
  nResults?: number;
  sources?: string[] | null;
}

export async function runSearch(
  query: string,
  nResultsOrOptions: number | RunSearchOptions = 20,
): Promise<SearchResult[]> {
  if (!query.trim()) return [];
  const opts: RunSearchOptions =
    typeof nResultsOrOptions === "number" ? { nResults: nResultsOrOptions } : nResultsOrOptions;
  await ensureDaemon();
  const resp = await requestJson<SearchResult[] | { detail?: unknown }>("/search", {
    method: "POST",
    body: {
      query,
      n_results: opts.nResults ?? 20,
      ...(opts.sources && opts.sources.length > 0 ? { sources: opts.sources } : {}),
    },
    timeoutMs: 30000,
  });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    const detail = formatDetail((resp.body as { detail?: unknown }).detail);
    const err = `Daemon returned HTTP ${resp.statusCode}${detail ? `: ${detail}` : ""}`;
    if (resp.statusCode === 401 || resp.statusCode === 403) throw new VefRunnerError("AUTH_ERROR", err);
    if (resp.statusCode === 429) throw new VefRunnerError("RATE_LIMIT", err);
    throw new VefRunnerError("DAEMON_ERROR", err);
  }
  return resp.body as SearchResult[];
}

export async function validateSetup(): Promise<{ count: number }> {
  await ensureDaemon();
  const resp = await requestJson<{ count?: number }>("/stats", { timeoutMs: 5000 });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Daemon stats returned HTTP ${resp.statusCode}`);
  }
  return { count: typeof resp.body.count === "number" ? resp.body.count : 0 };
}

export async function fetchSources(): Promise<string[]> {
  try {
    await ensureDaemon();
    const resp = await requestJson<{ sources?: string[] }>("/sources", { timeoutMs: 3000 });
    return resp.statusCode >= 200 && resp.statusCode < 300 ? resp.body.sources ?? [] : [];
  } catch {
    return [];
  }
}

export async function fetchConnectorStatus(): Promise<ConnectorStatusMap> {
  await ensureDaemon();
  const resp = await requestJson<ConnectorStatusMap>("/connector-status", { timeoutMs: 3000 });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Connector status request failed: HTTP ${resp.statusCode}`);
  }
  return resp.body;
}

export async function fetchProgress(): Promise<ProgressInfo> {
  await ensureDaemon();
  const resp = await requestJson<ProgressInfo>("/progress", { timeoutMs: 3000 });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Progress request failed: HTTP ${resp.statusCode}`);
  }
  return resp.body;
}

export async function triggerSync(source?: string): Promise<{ status: string; last_sync: Record<string, unknown> }> {
  await ensureDaemon();
  const resp = await requestJson<{ status: string; last_sync: Record<string, unknown>; detail?: unknown }>("/sync", {
    method: "POST",
    body: source ? { source } : {},
    timeoutMs: 30000,
  });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    const detail = formatDetail(resp.body.detail);
    throw new VefRunnerError(
      "DAEMON_ERROR",
      `Sync request failed: HTTP ${resp.statusCode}${detail ? `: ${detail}` : ""}`,
    );
  }
  return resp.body as { status: string; last_sync: Record<string, unknown> };
}

export async function fetchSyncRunning(): Promise<boolean> {
  try {
    const resp = await requestJson<{ running?: boolean }>("/sync-running", { timeoutMs: 3000 });
    return !!resp.body.running;
  } catch {
    return false;
  }
}

export async function fetchWatchedDirs(): Promise<string[]> {
  await ensureDaemon();
  const resp = await requestJson<{ dirs?: string[] }>("/watched-dirs", { timeoutMs: 3000 });
  return resp.statusCode >= 200 && resp.statusCode < 300 ? resp.body.dirs ?? [] : [];
}

export async function addWatchedDir(path: string): Promise<string[]> {
  await ensureDaemon();
  const resp = await requestJson<{ dirs?: string[] }>("/watched-dirs", {
    method: "POST",
    body: { path },
    timeoutMs: 5000,
  });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Failed to add directory: HTTP ${resp.statusCode}`);
  }
  return resp.body.dirs ?? [];
}

export async function removeWatchedDir(path: string): Promise<string[]> {
  await ensureDaemon();
  const resp = await requestJson<{ dirs?: string[] }>("/watched-dirs", {
    method: "DELETE",
    body: { path },
    timeoutMs: 5000,
  });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Failed to remove directory: HTTP ${resp.statusCode}`);
  }
  return resp.body.dirs ?? [];
}

export async function saveConfigure(cfg: {
  gemini_api_key?: string;
  canvas_api_key?: string;
  canvas_base_url?: string;
  schoology_consumer_key?: string;
  schoology_consumer_secret?: string;
}): Promise<void> {
  await ensureDaemon();
  const resp = await requestJson<{ ok?: boolean }>("/configure", {
    method: "POST",
    body: cfg,
    timeoutMs: 5000,
  });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Configure failed: HTTP ${resp.statusCode}`);
  }
}

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

export async function fetchModelStatus(): Promise<Record<string, unknown>> {
  await ensureDaemon();
  const resp = await requestJson<Record<string, unknown>>("/model-status", { timeoutMs: 5000 });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Model status failed: HTTP ${resp.statusCode}`);
  }
  return resp.body;
}

export async function fetchIndexStatus(): Promise<Record<string, unknown>> {
  await ensureDaemon();
  const resp = await requestJson<Record<string, unknown>>("/index-status", { timeoutMs: 5000 });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Index status failed: HTTP ${resp.statusCode}`);
  }
  return resp.body;
}

export async function fetchMigrationStatus(): Promise<Record<string, unknown>> {
  await ensureDaemon();
  const resp = await requestJson<Record<string, unknown>>("/migration-status", { timeoutMs: 5000 });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Migration status failed: HTTP ${resp.statusCode}`);
  }
  return resp.body;
}

export async function rebuildIndex(): Promise<Record<string, unknown>> {
  await ensureDaemon();
  const resp = await requestJson<Record<string, unknown>>("/rebuild-index", {
    method: "POST",
    body: {},
    timeoutMs: 30000,
  });
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw new VefRunnerError("DAEMON_ERROR", `Rebuild index failed: HTTP ${resp.statusCode}`);
  }
  return resp.body;
}
