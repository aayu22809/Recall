import { useState } from "react";
import { Loader2, RefreshCw, Unplug, X, Zap } from "lucide-react";
import clsx from "clsx";

import { Button } from "../../components/Button";
import { invoke } from "../../lib/ipc";
import {
  useConnectGoogleMutation,
  useConnectorStatus,
  useDisconnectMutation,
  useSyncMutation,
} from "../../lib/daemon";
import { sourceLabel, sourceTint } from "../../lib/theme";

const GOOGLE_SCOPES: Record<string, string[]> = {
  gmail: ["https://www.googleapis.com/auth/gmail.readonly"],
  gcal: ["https://www.googleapis.com/auth/calendar.readonly"],
  gdrive: ["https://www.googleapis.com/auth/drive.readonly"],
};

const SOURCE_ORDER = ["gmail", "gcal", "gdrive", "calai", "canvas", "schoology", "notion"];

const CONNECT_FORMS: Record<
  string,
  {
    fields: { name: string; label: string; placeholder?: string; secret?: boolean }[];
  }
> = {
  notion: {
    fields: [{ name: "api_key", label: "Integration token", placeholder: "ntn_…", secret: true }],
  },
  calai: {
    fields: [{ name: "api_key", label: "API key", placeholder: "cal_…", secret: true }],
  },
  canvas: {
    fields: [
      { name: "base_url", label: "Canvas URL", placeholder: "https://canvas.instructure.com" },
      { name: "token", label: "Access token", secret: true },
    ],
  },
  schoology: {
    fields: [
      { name: "base_url", label: "Schoology API URL", placeholder: "https://api.schoology.com/v1" },
      { name: "consumer_key", label: "Consumer key" },
      { name: "consumer_secret", label: "Consumer secret", secret: true },
    ],
  },
};

function relativeTime(unix_s: number): string {
  if (!unix_s) return "never";
  const delta = Date.now() / 1000 - unix_s;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86400)}d ago`;
}

export function Sources() {
  const status = useConnectorStatus();
  const connect = useConnectGoogleMutation();
  const disconnect = useDisconnectMutation();
  const sync = useSyncMutation();
  const [formSource, setFormSource] = useState<string | null>(null);

  return (
    <div className="px-8 py-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-[20px] font-semibold tracking-tight">Sources</h2>
          <p className="mt-1 text-[13px] text-fg-muted">
            Each connector is opt-in and read-only. Disconnect anytime —
            indexed data stays until you clear it.
          </p>
        </div>
        <Button
          variant="secondary"
          onClick={() => sync.mutate(undefined)}
          disabled={sync.isPending}
        >
          <RefreshCw size={13} className={sync.isPending ? "animate-spin" : ""} />
          Sync all
        </Button>
      </header>

      <ul className="divide-y divide-border rounded-panel border border-border bg-bg-panel">
        {SOURCE_ORDER.map((id) => {
          const c = status.data?.[id];
          const authed = c?.authenticated ?? false;
          const isGoogle = id in GOOGLE_SCOPES;
          return (
            <li key={id} className="flex items-center gap-4 px-4 py-3.5">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: sourceTint(id) }}
              />
              <div className="flex-1">
                <div className="flex items-center gap-2 text-[14px] text-fg">
                  {sourceLabel(id)}
                  <StatusBadge authed={authed} />
                </div>
                <div className="font-mono text-[11px] text-fg-muted">
                  {authed
                    ? connectorLine(c ?? { last_sync: 0, interval_s: 900, last_result: {} })
                    : "not configured"}
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                {authed ? (
                  <>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => sync.mutate(id)}
                      disabled={sync.isPending}
                    >
                      <RefreshCw size={11} />
                      Sync
                    </Button>
                    {isGoogle && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          connect.mutate({ source: id, scopes: GOOGLE_SCOPES[id] })
                        }
                        disabled={connect.isPending}
                      >
                        <Zap size={11} />
                        Re-auth
                      </Button>
                    )}
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => disconnect.mutate(id)}
                      disabled={disconnect.isPending}
                    >
                      <Unplug size={11} />
                      Disconnect
                    </Button>
                  </>
                ) : isGoogle ? (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() =>
                      connect.mutate({ source: id, scopes: GOOGLE_SCOPES[id] })
                    }
                    disabled={connect.isPending}
                  >
                    {connect.isPending ? (
                      <Loader2 size={11} className="animate-spin" />
                    ) : (
                      <Zap size={11} />
                    )}
                    Connect
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setFormSource(id)}
                  >
                    <Zap size={11} />
                    Connect
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {formSource && (
        <ConnectPanel
          source={formSource}
          onClose={() => setFormSource(null)}
          onSaved={() => {
            setFormSource(null);
            status.refetch();
          }}
        />
      )}
    </div>
  );
}

function ConnectPanel({
  source,
  onClose,
  onSaved,
}: {
  source: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const spec = CONNECT_FORMS[source];
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!spec) return null;

  async function save() {
    try {
      setSaving(true);
      setError(null);
      const payload: Record<string, string> = {};
      for (const field of spec.fields) {
        const value = values[field.name]?.trim();
        if (!value) throw new Error(`${field.label} required`);
        payload[field.name] = value;
      }
      await invoke("write_credential", { source, payload });
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/55 px-6">
      <div className="w-full max-w-[460px] rounded-panel border border-border bg-bg-panel shadow-floating">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <div className="text-[14px] font-medium text-fg">Connect {sourceLabel(source)}</div>
            <div className="font-mono text-[11px] text-fg-dim">credentials stay on this Mac</div>
          </div>
          <button
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-panel text-fg-muted hover:bg-bg-hover hover:text-fg"
          >
            <X size={14} />
          </button>
        </div>
        <div className="space-y-3 px-4 py-4">
          {spec.fields.map((field) => (
            <label key={field.name} className="block">
              <div className="mb-1.5 text-[11px] text-fg-muted">{field.label}</div>
              <input
                type={field.secret ? "password" : "text"}
                value={values[field.name] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.value }))}
                placeholder={field.placeholder}
                className="h-9 w-full rounded-panel border border-border bg-bg-input px-3 font-mono text-[12px] text-fg outline-none transition-colors placeholder:text-fg-dim focus:border-accent/60"
              />
            </label>
          ))}
          {error && (
            <div className="rounded-panel border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger">
              {error}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={save} disabled={saving}>
            {saving ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
            Save
          </Button>
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ authed }: { authed: boolean }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-chip px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        authed
          ? "bg-success/10 text-success"
          : "bg-bg-hover text-fg-dim",
      )}
    >
      {authed ? "connected" : "off"}
    </span>
  );
}

function connectorLine(state: { last_sync: number; interval_s: number; last_result: Record<string, unknown> }) {
  const result = String(state.last_result?.status ?? "configured");
  const detail =
    result === "error"
      ? "last error"
      : result === "partial"
        ? "partial"
        : result === "ok"
          ? "last sync"
          : result;
  return `${detail} ${relativeTime(state.last_sync)} · every ${Math.round((state.interval_s ?? 900) / 60)}m`;
}
