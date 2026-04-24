import { Action, ActionPanel, Icon, List, Toast, showToast } from "@raycast/api";
import { execFile } from "node:child_process";
import { useCallback, useEffect, useState } from "react";
import {
  fetchConnectorStatus,
  fetchProgress,
  triggerSync,
  validateSetup,
  type ConnectorStatusMap,
  type ProgressInfo,
} from "./lib/runner";

function relTime(iso: string | null): string {
  if (!iso) return "Never";
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const deltaMs = Date.now() - ts;
  if (deltaMs < 60_000) return "Just now";
  const mins = Math.floor(deltaMs / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function openTerminalCommand(cmd: string): Promise<void> {
  const escaped = cmd.replace(/"/g, '\\"');
  return new Promise((resolve, reject) => {
    execFile("osascript", ["-e", `tell application "Terminal" to do script "${escaped}"`], (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

export default function SyncStatus() {
  const [loading, setLoading] = useState(false);
  const [daemonOk, setDaemonOk] = useState(false);
  const [docCount, setDocCount] = useState(0);
  const [connectors, setConnectors] = useState<ConnectorStatusMap>({});
  const [progress, setProgress] = useState<ProgressInfo>({ indexing: false, queued: 0, total_indexed: 0 });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [health, connectorStatus, progressInfo] = await Promise.all([
        validateSetup(),
        fetchConnectorStatus(),
        fetchProgress(),
      ]);
      setDaemonOk(true);
      setDocCount(health.count ?? 0);
      setConnectors(connectorStatus);
      setProgress(progressInfo);
    } catch {
      setDaemonOk(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 30_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <List isLoading={loading}>
      <List.Section title="Daemon">
        <List.Item
          icon={daemonOk ? Icon.CheckCircle : Icon.XMarkCircle}
          title={daemonOk ? "Running" : "Not reachable"}
          subtitle={`${docCount} indexed docs`}
          accessories={[{ text: progress.indexing ? `Indexing (${progress.queued} queued)` : "Idle" }]}
        />
      </List.Section>
      <List.Section title="Connectors">
        {Object.entries(connectors).map(([source, status]) => (
          <List.Item
            key={source}
            icon={status.authenticated ? Icon.CheckCircle : Icon.Circle}
            title={source}
            subtitle={status.authenticated ? "Authenticated" : "Not authenticated"}
            accessories={[{ text: `Last sync: ${relTime(status.last_sync_iso)}` }]}
            actions={
              <ActionPanel>
                <Action
                  title="Sync Now"
                  icon={Icon.ArrowClockwise}
                  onAction={async () => {
                    await showToast({ style: Toast.Style.Animated, title: `Syncing ${source}...` });
                    try {
                      await triggerSync(source);
                      await showToast({ style: Toast.Style.Success, title: `Synced ${source}` });
                      await load();
                    } catch (error) {
                      await showToast({
                        style: Toast.Style.Failure,
                        title: `Sync failed for ${source}`,
                        message: error instanceof Error ? error.message : String(error),
                      });
                    }
                  }}
                />
                <Action
                  title="Connect"
                  icon={Icon.Link}
                  onAction={async () => {
                    try {
                      await openTerminalCommand(`recall connect ${source}`);
                    } catch (error) {
                      await showToast({
                        style: Toast.Style.Failure,
                        title: "Failed to open Terminal",
                        message: error instanceof Error ? error.message : String(error),
                      });
                    }
                  }}
                />
                <Action title="Refresh" icon={Icon.ArrowClockwise} onAction={() => void load()} />
              </ActionPanel>
            }
          />
        ))}
      </List.Section>
    </List>
  );
}
