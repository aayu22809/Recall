import { spawnSync } from "child_process";
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
  | "TIMEOUT"          // process timed out
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

// ── Static Python script ──────────────────────────────────────────────────────
//
// SECURITY: This string is 100% static — no user data is interpolated.
// User input enters Python ONLY via sys.argv:
//   sys.argv[1] = package path
//   sys.argv[2] = query string   (for search)
//   sys.argv[3] = n_results      (for search)
//
// The script writes a JSON envelope:
//   {"ok": true,  "results": [...]}  on success
//   {"ok": false, "error_code": "AUTH_ERROR"|"RATE_LIMIT"|"NOT_INSTALLED"|"UNKNOWN",
//    "error": "human-readable message"}  on failure

const SEARCH_SCRIPT = `
import json, sys

pkg_path = sys.argv[1]
query    = sys.argv[2]
n        = int(sys.argv[3])

try:
    sys.path.insert(0, pkg_path)
    from vector_embedded_finder import search as vef_search
except ImportError as exc:
    print(json.dumps({"ok": False, "error_code": "NOT_INSTALLED", "error": str(exc)}))
    sys.exit(0)

try:
    results = vef_search(query, n_results=n)
    print(json.dumps({"ok": True, "results": results}))
except Exception as exc:
    err = str(exc)
    if "api_key" in err.lower() or "invalid" in err.lower() or "unauthenticated" in err.lower():
        print(json.dumps({"ok": False, "error_code": "AUTH_ERROR", "error": err}))
    elif "quota" in err.lower() or "rate" in err.lower() or "429" in err:
        print(json.dumps({"ok": False, "error_code": "RATE_LIMIT", "error": err}))
    else:
        print(json.dumps({"ok": False, "error_code": "UNKNOWN", "error": err}))
`;

const VALIDATE_SCRIPT = `
import json, sys

pkg_path = sys.argv[1]

try:
    sys.path.insert(0, pkg_path)
    from vector_embedded_finder import store
except ImportError as exc:
    print(json.dumps({"ok": False, "error_code": "NOT_INSTALLED", "error": str(exc)}))
    sys.exit(0)

try:
    count = store.count()
    print(json.dumps({"ok": True, "count": count}))
except Exception as exc:
    err = str(exc)
    if "api_key" in err.lower() or "invalid" in err.lower() or "unauthenticated" in err.lower():
        print(json.dumps({"ok": False, "error_code": "AUTH_ERROR", "error": err}))
    else:
        print(json.dumps({"ok": False, "error_code": "UNKNOWN", "error": err}))
`;

// ── Helpers ───────────────────────────────────────────────────────────────────

function resolvePrefs(): Preferences {
  return getPreferenceValues<Preferences>();
}

function resolvePython(prefs: Preferences): string {
  if (prefs.pythonPath?.trim()) return prefs.pythonPath.trim();
  // Try to find python3 on PATH
  const which = spawnSync("which", ["python3"], { encoding: "utf-8" });
  if (which.status === 0 && which.stdout.trim()) return which.stdout.trim();
  return "python3"; // fallback — will get PYTHON_NOT_FOUND if absent
}

function buildEnv(prefs: Preferences): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  if (prefs.geminiApiKey?.trim()) {
    env["GEMINI_API_KEY"] = prefs.geminiApiKey.trim();
  }
  return env;
}

function parseEnvelope(raw: string, script: "search" | "validate"): unknown {
  // Find the last line that looks like JSON (the script prints exactly one JSON line)
  const lines = raw.trim().split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line.startsWith("{")) {
      try {
        return JSON.parse(line);
      } catch {
        throw new VefRunnerError("UNKNOWN", `Malformed JSON in ${script} output:\n${line.slice(0, 500)}`);
      }
    }
  }
  throw new VefRunnerError("UNKNOWN", `No JSON in ${script} output:\n${raw.trim().slice(0, 500)}`);
}

// ── Public API ────────────────────────────────────────────────────────────────

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
}

/**
 * Run a semantic search via the Python vef library.
 *
 * @throws {VefRunnerError} with appropriate code on any failure
 */
export function runSearch(query: string, nResults = 20): SearchResult[] {
  if (!query.trim()) return [];

  const prefs = resolvePrefs();
  const python = resolvePython(prefs);
  const env = buildEnv(prefs);

  const proc = spawnSync(
    python,
    ["-c", SEARCH_SCRIPT, prefs.pythonPackagePath, query, String(nResults)],
    { env, encoding: "utf-8", timeout: 20_000 },
  );

  if (proc.error) {
    const msg = proc.error.message ?? "";
    if (msg.includes("ENOENT") || msg.includes("not found")) {
      throw new VefRunnerError("PYTHON_NOT_FOUND", `Python not found: ${python}`);
    }
    if (msg.includes("ETIMEDOUT") || proc.signal === "SIGTERM") {
      throw new VefRunnerError("TIMEOUT", "Search timed out after 20 s");
    }
    throw new VefRunnerError("UNKNOWN", msg);
  }

  if (proc.signal === "SIGTERM") {
    throw new VefRunnerError("TIMEOUT", "Search timed out after 20 s");
  }

  if (!proc.stdout?.trim()) {
    const stderr = proc.stderr?.trim() ?? "";
    throw new VefRunnerError("UNKNOWN", `Search produced no output${stderr ? `: ${stderr.slice(0, 300)}` : ""}`);
  }

  const envelope = parseEnvelope(proc.stdout ?? "", "search") as {
    ok: boolean;
    results?: SearchResult[];
    error_code?: string;
    error?: string;
  };

  if (!envelope.ok) {
    const code = (envelope.error_code ?? "UNKNOWN") as VefErrorCode;
    throw new VefRunnerError(code, envelope.error ?? "Unknown error");
  }

  return envelope.results ?? [];
}

/**
 * Validate that the Python package is importable and the DB is reachable.
 * Returns the item count in the vector store.
 *
 * @throws {VefRunnerError} with appropriate code on any failure
 */
export function validateSetup(): { count: number } {
  const prefs = resolvePrefs();
  const python = resolvePython(prefs);
  const env = buildEnv(prefs);

  const proc = spawnSync(
    python,
    ["-c", VALIDATE_SCRIPT, prefs.pythonPackagePath],
    { env, encoding: "utf-8", timeout: 10_000 },
  );

  if (proc.error) {
    const msg = proc.error.message ?? "";
    if (msg.includes("ENOENT") || msg.includes("not found")) {
      throw new VefRunnerError("PYTHON_NOT_FOUND", `Python not found: ${python}`);
    }
    if (msg.includes("ETIMEDOUT") || proc.signal === "SIGTERM") {
      throw new VefRunnerError("TIMEOUT", "Validation timed out");
    }
    throw new VefRunnerError("UNKNOWN", msg);
  }

  if (!proc.stdout?.trim()) {
    const stderr = proc.stderr?.trim() ?? "";
    throw new VefRunnerError("UNKNOWN", `Search produced no output${stderr ? `: ${stderr.slice(0, 300)}` : ""}`);
  }

  const envelope = parseEnvelope(proc.stdout ?? "", "validate") as {
    ok: boolean;
    count?: number;
    error_code?: string;
    error?: string;
  };

  if (!envelope.ok) {
    const code = (envelope.error_code ?? "UNKNOWN") as VefErrorCode;
    throw new VefRunnerError(code, envelope.error ?? "Unknown error");
  }

  return { count: envelope.count ?? 0 };
}
