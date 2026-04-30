import { useState } from "react";
import { Copy, ExternalLink, RotateCcw } from "lucide-react";

import { Button } from "../../components/Button";
import { invoke } from "../../lib/ipc";
import {
  useConnectorStatus,
  useDaemonRestartMutation,
  useDaemonStatus,
  useIndexStatus,
  useLogTail,
  useProgress,
  useStats,
} from "../../lib/daemon";
import { sourceLabel } from "../../lib/theme";

function relativeTime(unix_s: number): string {
  if (!unix_s) return "never";
  const delta = Date.now() / 1000 - unix_s;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)}m`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h`;
  return `${Math.round(delta / 86400)}d`;
}

export function Activity() {
  const stats = useStats();
  const progress = useProgress();
  const index = useIndexStatus();
  const connectors = useConnectorStatus();
  const logs = useLogTail(200);
  const daemon = useDaemonStatus();
  const restart = useDaemonRestartMutation();
  const [logFilter, setLogFilter] = useState("");
  const logLines = (logs.data ?? "")
    .split("\n")
    .filter((line) => !logFilter.trim() || line.toLowerCase().includes(logFilter.toLowerCase()));

  return (
    <div className="px-8 py-8">
      <header className="mb-6">
        <h2 className="text-[20px] font-semibold tracking-tight">Activity</h2>
        <p className="mt-1 text-[13px] text-fg-muted">
          Live counters and per-connector sync state.
        </p>
      </header>

      <div className="grid grid-cols-3 gap-3">
        <Tile
          label="Indexed"
          value={(stats.data?.count ?? 0).toLocaleString()}
          accent
        />
        <Tile
          label="Queued"
          value={(progress.data?.queued ?? 0).toString()}
        />
        <Tile
          label="Index jobs"
          value={index.data?.running ? "running" : "idle"}
        />
      </div>

      <div className="mt-4 flex items-center gap-3 rounded-panel border border-border bg-bg-panel px-4 py-3 text-[12px]">
        <span className="font-mono text-fg-muted">daemon</span>
        <span>{daemon.status}</span>
        {daemon.message && <span className="text-danger">{daemon.message}</span>}
        {daemon.log_path && (
          <span className="truncate font-mono text-[11px] text-fg-dim">{daemon.log_path}</span>
        )}
        <div className="ml-auto">
          <Button variant="ghost" size="sm" onClick={() => restart.mutate()} disabled={restart.isPending}>
            <RotateCcw size={11} />
            Restart daemon
          </Button>
        </div>
      </div>

      <h3 className="mt-8 text-[13px] font-medium tracking-tight text-fg-muted">
        Connectors
      </h3>
      <ul className="mt-2 divide-y divide-border rounded-panel border border-border bg-bg-panel">
        {Object.entries(connectors.data ?? {}).map(([id, c]) => (
          <li key={id} className="flex items-center gap-3 px-4 py-2.5 font-mono text-[11px]">
            <span className="w-24 text-fg-muted">{sourceLabel(id)}</span>
            <span className="w-20 text-fg-muted">
              {c.authenticated ? "connected" : "off"}
            </span>
            <span className="w-20 text-fg-dim">{relativeTime(c.last_sync)}</span>
            <span className="ml-auto text-fg-dim">
              every {Math.round(c.interval_s / 60)}m
            </span>
          </li>
        ))}
        {(!connectors.data || Object.keys(connectors.data).length === 0) && (
          <li className="px-4 py-6 text-center text-[12px] text-fg-muted">
            No connectors yet.
          </li>
        )}
      </ul>

      <div className="mt-8">
        <div className="mb-2 flex items-center justify-between gap-3">
          <h3 className="text-[13px] font-medium tracking-tight text-fg-muted">
            Daemon log
          </h3>
          <div className="flex items-center gap-2">
            <input
              value={logFilter}
              onChange={(e) => setLogFilter(e.target.value)}
              placeholder="Filter logs"
              className="h-7 w-44 rounded-panel border border-border bg-bg-input px-2.5 font-mono text-[11px] text-fg outline-none placeholder:text-fg-dim focus:border-accent/60"
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={async () => {
                const text = await invoke<string>("read_log_tail", { lines: 200 });
                await navigator.clipboard.writeText(text);
              }}
            >
              <Copy size={11} />
              Copy
            </Button>
            <Button variant="ghost" size="sm" onClick={() => invoke("open_log_dir")}>
              <ExternalLink size={11} />
              Open
            </Button>
          </div>
        </div>
        <pre className="h-[220px] overflow-auto rounded-panel border border-border bg-bg-panel p-3 font-mono text-[11px] leading-relaxed text-fg-muted">
          {logLines.length ? logLines.join("\n") : "No daemon log lines yet."}
        </pre>
      </div>
    </div>
  );
}

function Tile({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-panel border border-border bg-bg-panel px-4 py-3">
      <div className="font-mono text-[10px] uppercase tracking-wider text-fg-dim">
        {label}
      </div>
      <div
        className={
          accent
            ? "mt-1 font-mono text-[24px] text-accent"
            : "mt-1 font-mono text-[24px] text-fg"
        }
      >
        {value}
      </div>
    </div>
  );
}
