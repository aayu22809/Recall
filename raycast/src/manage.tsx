import {
  Action,
  ActionPanel,
  Alert,
  Color,
  Form,
  Icon,
  List,
  Toast,
  confirmAlert,
  popToRoot,
  showToast,
  useNavigation,
} from "@raycast/api";
import { execFile } from "node:child_process";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  addWatchedDir,
  connectInTerminal,
  fetchConnectorStatus,
  fetchProgress,
  fetchSyncRunning,
  fetchWatchedDirs,
  indexFolderInTerminal,
  removeWatchedDir,
  saveConfigure,
  triggerSync,
  validateSetup,
  type ConnectorStatusMap,
  type ProgressInfo,
} from "./lib/runner";

// ── helpers ───────────────────────────────────────────────────────────────────

function relTime(iso: string | null): string {
  if (!iso) return "Never";
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const delta = Date.now() - ts;
  if (delta < 60_000) return "Just now";
  const mins = Math.floor(delta / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function chooseFolder(): Promise<string | null> {
  return new Promise((resolve) => {
    execFile(
      "osascript",
      ["-e", `POSIX path of (choose folder with prompt "Select folder to index:")`],
      (err, stdout) => {
        if (err) resolve(null);
        else resolve(stdout.trim().replace(/\/$/, ""));
      },
    );
  });
}

// ── Configure Form ────────────────────────────────────────────────────────────

function ConfigureForm() {
  const [geminiKey, setGeminiKey] = useState("");
  const [canvasKey, setCanvasKey] = useState("");
  const [canvasUrl, setCanvasUrl] = useState("");
  const [schoologyKey, setSchoologyKey] = useState("");
  const [schoologySecret, setSchoologySecret] = useState("");
  const [saving, setSaving] = useState(false);
  const { pop } = useNavigation();

  async function handleSubmit() {
    setSaving(true);
    const toast = await showToast({ style: Toast.Style.Animated, title: "Saving configuration…" });
    try {
      await saveConfigure({
        gemini_api_key: geminiKey || undefined,
        canvas_api_key: canvasKey || undefined,
        canvas_base_url: canvasUrl || undefined,
        schoology_consumer_key: schoologyKey || undefined,
        schoology_consumer_secret: schoologySecret || undefined,
      });
      toast.style = Toast.Style.Success;
      toast.title = "Configuration saved";
      pop();
    } catch (err) {
      toast.style = Toast.Style.Failure;
      toast.title = "Save failed";
      toast.message = err instanceof Error ? err.message : String(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Form
      isLoading={saving}
      navigationTitle="Configure Recall"
      actions={
        <ActionPanel>
          <Action.SubmitForm title="Save Configuration" onSubmit={handleSubmit} />
        </ActionPanel>
      }
    >
      <Form.Description title="Gemini" text="Embedding model API key (required for search)" />
      <Form.PasswordField
        id="geminiKey"
        title="Gemini API Key"
        placeholder="AIza…"
        value={geminiKey}
        onChange={setGeminiKey}
      />

      <Form.Separator />
      <Form.Description title="Canvas LMS" text="Canvas API key + base URL for course indexing" />
      <Form.PasswordField
        id="canvasKey"
        title="Canvas API Key"
        placeholder="Paste from Account → Approved Integrations"
        value={canvasKey}
        onChange={setCanvasKey}
      />
      <Form.TextField
        id="canvasUrl"
        title="Canvas Base URL"
        placeholder="https://canvas.instructure.com"
        value={canvasUrl}
        onChange={setCanvasUrl}
      />

      <Form.Separator />
      <Form.Description title="Schoology" text="OAuth consumer key + secret from App Center" />
      <Form.PasswordField
        id="schoologyKey"
        title="Consumer Key"
        value={schoologyKey}
        onChange={setSchoologyKey}
      />
      <Form.PasswordField
        id="schoologySecret"
        title="Consumer Secret"
        value={schoologySecret}
        onChange={setSchoologySecret}
      />

      <Form.Separator />
      <Form.Description
        title="Gmail / Google Calendar / Drive"
        text="These use OAuth. Use 'Connect' on the connector row to open the browser auth flow."
      />
    </Form>
  );
}

// ── Add Folder Form ───────────────────────────────────────────────────────────

function AddFolderForm({ onAdd }: { onAdd: (dirs: string[]) => void }) {
  const [path, setPath] = useState("");
  const [indexNow, setIndexNow] = useState(true);
  const [loading, setLoading] = useState(false);
  const { pop } = useNavigation();

  async function pickFolder() {
    const chosen = await chooseFolder();
    if (chosen) setPath(chosen);
  }

  async function handleSubmit() {
    if (!path.trim()) {
      await showToast({ style: Toast.Style.Failure, title: "Path required" });
      return;
    }
    setLoading(true);
    const toast = await showToast({ style: Toast.Style.Animated, title: "Adding folder…" });
    try {
      const dirs = await addWatchedDir(path.trim());
      onAdd(dirs);
      if (indexNow) {
        await indexFolderInTerminal(path.trim());
        toast.style = Toast.Style.Success;
        toast.title = "Folder added — indexing started in Terminal";
      } else {
        toast.style = Toast.Style.Success;
        toast.title = "Folder added to watch list";
      }
      pop();
    } catch (err) {
      toast.style = Toast.Style.Failure;
      toast.title = "Failed to add folder";
      toast.message = err instanceof Error ? err.message : String(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Form
      isLoading={loading}
      navigationTitle="Add Folder to Index"
      actions={
        <ActionPanel>
          <Action.SubmitForm title="Add Folder" onSubmit={handleSubmit} />
          <Action title="Browse…" icon={Icon.Finder} onAction={pickFolder} />
        </ActionPanel>
      }
    >
      <Form.TextField
        id="path"
        title="Folder Path"
        placeholder="/Users/you/Documents"
        value={path}
        onChange={setPath}
      />
      <Form.Checkbox
        id="indexNow"
        label="Start indexing now (opens Terminal)"
        value={indexNow}
        onChange={setIndexNow}
      />
    </Form>
  );
}

// ── Progress bar text ─────────────────────────────────────────────────────────

function progressBar(syncing: boolean, docs: number, prevDocs: number): string {
  const newDocs = docs - prevDocs;
  const base = `${docs.toLocaleString()} docs`;
  if (!syncing) return base;
  const delta = newDocs > 0 ? `  +${newDocs}` : "";
  return `⏳ ${base}${delta}`;
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function ManageRecall() {
  const { push } = useNavigation();
  const [loading, setLoading] = useState(true);
  const [daemonOk, setDaemonOk] = useState(false);
  const [docCount, setDocCount] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [progress, setProgress] = useState<ProgressInfo>({ indexing: false, queued: 0, total_indexed: 0 });
  const [connectors, setConnectors] = useState<ConnectorStatusMap>({});
  const [watchedDirs, setWatchedDirs] = useState<string[]>([]);
  const prevDocs = useRef(0);

  const loadAll = useCallback(async () => {
    try {
      const [health, connStatus, prog, dirs, syncRunning] = await Promise.all([
        validateSetup(),
        fetchConnectorStatus(),
        fetchProgress(),
        fetchWatchedDirs(),
        fetchSyncRunning(),
      ]);
      setDaemonOk(true);
      setDocCount(health.count ?? 0);
      setConnectors(connStatus);
      setProgress(prog);
      setWatchedDirs(dirs);
      setSyncing(syncRunning);
    } catch {
      setDaemonOk(false);
    } finally {
      setLoading(false);
    }
  }, []);

  // Fast poll when syncing, slow poll otherwise
  useEffect(() => {
    void loadAll();
    const interval = setInterval(() => void loadAll(), syncing ? 2000 : 15000);
    return () => clearInterval(interval);
  }, [loadAll, syncing]);

  // Track previous doc count for delta display
  useEffect(() => {
    prevDocs.current = docCount;
  });

  async function handleSyncAll() {
    const toast = await showToast({ style: Toast.Style.Animated, title: "Triggering sync…" });
    try {
      const result = await triggerSync();
      setSyncing(true);
      if (result.status === "error") {
        toast.style = Toast.Style.Failure;
        toast.title = "Sync failed";
      } else {
        toast.style = Toast.Style.Success;
        toast.title = result.status === "in_progress" ? "Background sync already running — watching…" : "Sync triggered";
      }
    } catch (err) {
      toast.style = Toast.Style.Failure;
      toast.title = "Sync failed";
      toast.message = err instanceof Error ? err.message : String(err);
    }
  }

  async function handleSyncConnector(source: string) {
    const toast = await showToast({ style: Toast.Style.Animated, title: `Syncing ${source}…` });
    try {
      await triggerSync(source);
      setSyncing(true);
      toast.style = Toast.Style.Success;
      toast.title = `Sync triggered for ${source}`;
    } catch (err) {
      toast.style = Toast.Style.Failure;
      toast.title = `Sync failed for ${source}`;
      toast.message = err instanceof Error ? err.message : String(err);
    }
  }

  async function handleConnect(source: string) {
    await showToast({ style: Toast.Style.Animated, title: `Opening auth for ${source}…` });
    try {
      await connectInTerminal(source);
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Failed to open Terminal",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleRemoveDir(dir: string) {
    const confirmed = await confirmAlert({
      title: "Remove Folder",
      message: `Stop watching "${dir}"?\n\nAlready-indexed files stay in the database.`,
      primaryAction: { title: "Remove", style: Alert.ActionStyle.Destructive },
    });
    if (!confirmed) return;
    try {
      const dirs = await removeWatchedDir(dir);
      setWatchedDirs(dirs);
      await showToast({ style: Toast.Style.Success, title: "Folder removed" });
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Remove failed",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  const connectorIcon = (name: string): Icon => {
    const icons: Record<string, Icon> = {
      gmail: Icon.Envelope,
      gcal: Icon.Calendar,
      gdrive: Icon.HardDrive,
      canvas: Icon.Book,
      calai: Icon.Clock,
      schoology: Icon.Person,
      notion: Icon.Document,
    };
    return icons[name] ?? Icon.Plug;
  };

  return (
    <List isLoading={loading} navigationTitle="Manage Recall">
      {/* ── System ── */}
      <List.Section title="System">
        <List.Item
          icon={daemonOk ? { source: Icon.CircleFilled, tintColor: Color.Green } : { source: Icon.CircleFilled, tintColor: Color.Red }}
          title={daemonOk ? "Daemon running" : "Daemon offline"}
          subtitle={progressBar(syncing, docCount, prevDocs.current)}
          accessories={[
            {
              text: syncing
                ? "Syncing connectors…"
                : progress.indexing
                  ? `Indexing (${progress.queued} queued)`
                  : "Idle",
              icon: syncing || progress.indexing ? Icon.ArrowClockwise : Icon.CheckCircle,
            },
          ]}
          actions={
            <ActionPanel>
              <Action title="Sync All Connectors" icon={Icon.ArrowClockwise} onAction={handleSyncAll} />
              <Action title="Configure API Keys" icon={Icon.Gear} onAction={() => push(<ConfigureForm />)} />
              <Action title="Refresh" icon={Icon.ArrowClockwise} onAction={() => void loadAll()} />
            </ActionPanel>
          }
        />
      </List.Section>

      {/* ── Connectors ── */}
      <List.Section title="Connectors">
        {Object.entries(connectors).map(([source, status]) => (
          <List.Item
            key={source}
            icon={{
              source: connectorIcon(source),
              tintColor: status.authenticated ? Color.Green : Color.SecondaryText,
            }}
            title={source}
            subtitle={status.authenticated ? `Last sync: ${relTime(status.last_sync_iso)}` : (status.last_result?.reason ?? "Not connected")}
            accessories={[
              {
                tag: {
                  value: status.authenticated ? "connected" : "connect",
                  color: status.authenticated ? Color.Green : Color.Orange,
                },
              },
            ]}
            actions={
              <ActionPanel>
                {status.authenticated ? (
                  <Action
                    title="Sync Now"
                    icon={Icon.ArrowClockwise}
                    onAction={() => void handleSyncConnector(source)}
                  />
                ) : (
                  <Action
                    title="Connect"
                    icon={Icon.Link}
                    onAction={() => void handleConnect(source)}
                  />
                )}
                <Action
                  title={status.authenticated ? "Re-authenticate" : "Connect"}
                  icon={Icon.Link}
                  onAction={() => void handleConnect(source)}
                />
                <Action title="Refresh" icon={Icon.ArrowClockwise} onAction={() => void loadAll()} />
              </ActionPanel>
            }
          />
        ))}
      </List.Section>

      {/* ── Indexed Folders ── */}
      <List.Section title="Indexed Folders">
        {watchedDirs.map((dir) => (
          <List.Item
            key={dir}
            icon={Icon.Folder}
            title={dir.replace(/\/Users\/[^/]+/, "~")}
            subtitle={dir}
            actions={
              <ActionPanel>
                <Action
                  title="Index Now"
                  icon={Icon.Download}
                  onAction={async () => {
                    await indexFolderInTerminal(dir);
                    await showToast({ style: Toast.Style.Success, title: "Indexing started in Terminal" });
                  }}
                />
                <Action
                  title="Remove from Watch List"
                  icon={Icon.Trash}
                  style={Action.Style.Destructive}
                  onAction={() => void handleRemoveDir(dir)}
                />
              </ActionPanel>
            }
          />
        ))}
        <List.Item
          icon={{ source: Icon.Plus, tintColor: Color.Blue }}
          title="Add Folder…"
          actions={
            <ActionPanel>
              <Action
                title="Add Folder"
                icon={Icon.Plus}
                onAction={() => push(<AddFolderForm onAdd={setWatchedDirs} />)}
              />
            </ActionPanel>
          }
        />
      </List.Section>

      {/* ── Quick Actions ── */}
      <List.Section title="Configuration">
        <List.Item
          icon={Icon.Gear}
          title="API Keys & Credentials"
          subtitle="Gemini, Canvas, Schoology"
          actions={
            <ActionPanel>
              <Action title="Configure" icon={Icon.Gear} onAction={() => push(<ConfigureForm />)} />
            </ActionPanel>
          }
        />
      </List.Section>
    </List>
  );
}
