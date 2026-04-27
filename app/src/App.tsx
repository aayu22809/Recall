import { useEffect, useState } from "react";
import clsx from "clsx";
import {
  ActivitySquare,
  CircleDot,
  FolderTree,
  Layers,
  Search as SearchIcon,
  Settings as SettingsIcon,
  Wrench,
} from "lucide-react";

import { Onboarding } from "./routes/Onboarding";
import { Search } from "./routes/Search";
import { Sources } from "./routes/Sources";
import { Folders } from "./routes/Folders";
import { Settings } from "./routes/Settings";
import { Activity } from "./routes/Activity";

import { CommandPalette } from "./palette/CommandPalette";
import {
  useConnectorStatus,
  useDaemonStatus,
  useIndexStatus,
  useProgress,
  useStats,
  useWatchedDirs,
} from "./lib/daemon";
import { invoke } from "./lib/ipc";
import { sourceLabel, sourceTint } from "./lib/theme";

type Route = "setup" | "search" | "sources" | "folders" | "settings" | "activity";
type DockTab = "activity" | "jobs" | "logs";

const NAV: { id: Route; label: string; icon: React.ComponentType<{ size?: number; className?: string }> }[] = [
  { id: "setup", label: "Setup", icon: Wrench },
  { id: "search", label: "Search", icon: SearchIcon },
  { id: "sources", label: "Sources", icon: Layers },
  { id: "folders", label: "Folders", icon: FolderTree },
  { id: "activity", label: "Activity", icon: ActivitySquare },
  { id: "settings", label: "Settings", icon: SettingsIcon },
];

export default function App() {
  const [route, setRoute] = useState<Route>("search");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [onboardingDone, setOnboardingDone] = useState<boolean | null>(null);
  const [dockTab, setDockTab] = useState<DockTab>("activity");

  const daemon = useDaemonStatus();
  const stats = useStats();
  const progress = useProgress();
  const index = useIndexStatus();
  const watchedDirs = useWatchedDirs();
  const connectors = useConnectorStatus();

  useEffect(() => {
    invoke<boolean>("onboarding_complete")
      .then((done) => {
        setOnboardingDone(done);
        if (!done) setRoute("setup");
      })
      .catch(() => setOnboardingDone(true));
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const isMeta = event.metaKey || event.ctrlKey;
      if (isMeta && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (onboardingDone === null) {
    return <SplashScreen />;
  }

  return (
    <div className="flex h-full bg-bg text-fg">
      <Dock route={route} onRoute={setRoute} onboardingDone={onboardingDone} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar route={route} daemon={daemon} stats={stats.data?.count ?? 0} />
        <div className="flex min-h-0 flex-1">
          <Explorer
            route={route}
            daemon={daemon}
            watchedDirs={watchedDirs.data?.dirs ?? []}
            connectorStates={connectors.data ?? {}}
            indexedCount={stats.data?.count ?? 0}
            activePath={index.data?.active_path ?? null}
            onboardingDone={onboardingDone}
          />
          <main className="min-w-0 flex-1">
            {!onboardingDone || route === "setup" ? (
              <Onboarding
                onComplete={async () => {
                  await invoke("onboarding_set_complete", { value: true });
                  setOnboardingDone(true);
                  setRoute("search");
                }}
              />
            ) : route === "search" ? (
              <Search />
            ) : route === "sources" ? (
              <Sources />
            ) : route === "folders" ? (
              <Folders />
            ) : route === "activity" ? (
              <Activity />
            ) : (
              <Settings />
            )}
          </main>
        </div>
        <BottomDock
          tab={dockTab}
          onTab={setDockTab}
          progress={progress.data}
          index={index.data}
          connectors={connectors.data ?? {}}
          daemon={daemon}
        />
        <StatusBar
          daemon={daemon}
          indexedCount={stats.data?.count ?? 0}
          index={index.data}
        />
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} onNavigate={setRoute} />
    </div>
  );
}

function Dock({
  route,
  onRoute,
  onboardingDone,
}: {
  route: Route;
  onRoute: (route: Route) => void;
  onboardingDone: boolean;
}) {
  return (
    <nav className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-border bg-[#131619] py-2">
      <div className="title-drag flex h-10 w-full items-center justify-center border-b border-border text-[11px] font-semibold uppercase tracking-[0.16em] text-fg-dim">
        rc
      </div>
      <div className="title-no-drag flex w-full flex-1 flex-col items-center gap-1 pt-2">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = route === id;
          const disabled = !onboardingDone && id !== "setup";
          return (
            <button
              key={id}
              onClick={() => !disabled && onRoute(id)}
              title={label}
              className={clsx(
                "flex h-10 w-10 items-center justify-center rounded-panel border transition-colors",
                active
                  ? "border-border-strong bg-bg-hover text-fg"
                  : "border-transparent text-fg-dim hover:border-border hover:bg-bg-hover hover:text-fg",
                disabled && "cursor-not-allowed opacity-40",
              )}
            >
              <Icon size={16} />
            </button>
          );
        })}
      </div>
    </nav>
  );
}

function TopBar({
  route,
  daemon,
  stats,
}: {
  route: Route;
  daemon: ReturnType<typeof useDaemonStatus>;
  stats: number;
}) {
  return (
    <div className="title-drag flex h-10 shrink-0 items-center justify-between border-b border-border px-4">
      <div className="flex items-center gap-3 pl-3">
        <span className="text-[13px] font-medium text-fg">{labelForRoute(route)}</span>
        <span className="font-mono text-[11px] text-fg-dim">{stats.toLocaleString()} indexed</span>
      </div>
      <div className="title-no-drag flex items-center gap-3">
        <DaemonPill daemon={daemon} />
        <kbd className="rounded-chip border border-border px-1.5 py-0.5 font-mono text-[11px] text-fg-muted">
          ⌘K
        </kbd>
      </div>
    </div>
  );
}

function Explorer({
  route,
  daemon,
  watchedDirs,
  connectorStates,
  indexedCount,
  activePath,
  onboardingDone,
}: {
  route: Route;
  daemon: ReturnType<typeof useDaemonStatus>;
  watchedDirs: string[];
  connectorStates: Record<string, { authenticated: boolean }>;
  indexedCount: number;
  activePath: string | null;
  onboardingDone: boolean;
}) {
  const connected = Object.entries(connectorStates).filter(([, state]) => state.authenticated);

  return (
    <aside className="w-[260px] shrink-0 border-r border-border bg-bg-panel/60">
      <div className="border-b border-border px-4 py-3">
        <div className="text-[12px] font-medium text-fg">Workspace</div>
        <div className="mt-1 text-[11px] text-fg-muted">
          {!onboardingDone ? "Finish setup to unlock search and sync." : "Local index, command-first workflow."}
        </div>
      </div>

      <PanelSection title="Daemon">
        <Row label="Status" value={daemon.status} />
        <Row label="Port" value={String(daemon.port)} />
        <Row label="Restarts" value={String(daemon.restart_count)} />
        {daemon.message && <div className="text-[11px] text-danger">{daemon.message}</div>}
      </PanelSection>

      <PanelSection title="Files">
        <Row label="Watched" value={String(watchedDirs.length)} />
        <Row label="Indexed" value={indexedCount.toLocaleString()} />
        {activePath ? (
          <div className="truncate font-mono text-[11px] text-fg-dim">{activePath}</div>
        ) : (
          <div className="text-[11px] text-fg-dim">No active file job.</div>
        )}
      </PanelSection>

      <PanelSection title={route === "sources" ? "Source state" : "Connected sources"}>
        {connected.length === 0 ? (
          <div className="text-[11px] text-fg-dim">No sources connected.</div>
        ) : (
          <ul className="space-y-1">
            {connected.slice(0, 6).map(([id]) => (
              <li key={id} className="flex items-center gap-2 text-[12px] text-fg-muted">
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: sourceTint(id) }} />
                <span>{sourceLabel(id)}</span>
              </li>
            ))}
          </ul>
        )}
      </PanelSection>
    </aside>
  );
}

function PanelSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-border px-4 py-3">
      <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-fg-dim">{title}</div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[12px]">
      <span className="text-fg-muted">{label}</span>
      <span className="font-mono text-fg">{value}</span>
    </div>
  );
}

function BottomDock({
  tab,
  onTab,
  progress,
  index,
  connectors,
  daemon,
}: {
  tab: DockTab;
  onTab: (tab: DockTab) => void;
  progress: ReturnType<typeof useProgress>["data"];
  index: ReturnType<typeof useIndexStatus>["data"];
  connectors: Record<string, { authenticated: boolean; last_result: Record<string, unknown> }>;
  daemon: ReturnType<typeof useDaemonStatus>;
}) {
  return (
    <div className="h-[180px] shrink-0 border-t border-border bg-[#14181b]">
      <div className="flex h-8 items-center gap-1 border-b border-border px-3">
        {(["activity", "jobs", "logs"] as DockTab[]).map((item) => (
          <button
            key={item}
            onClick={() => onTab(item)}
            className={clsx(
              "rounded-chip px-2 py-1 text-[11px] uppercase tracking-[0.12em]",
              tab === item ? "bg-bg-hover text-fg" : "text-fg-dim hover:text-fg-muted",
            )}
          >
            {item}
          </button>
        ))}
      </div>
      <div className="h-[calc(100%-2rem)] overflow-auto px-4 py-3">
        {tab === "activity" && (
          <div className="grid grid-cols-4 gap-4 font-mono text-[11px]">
            <Metric label="queued" value={String(progress?.queued ?? 0)} />
            <Metric label="processed" value={String(index?.processed ?? 0)} />
            <Metric label="embedded" value={String(index?.embedded ?? 0)} />
            <Metric label="errors" value={String(index?.errors ?? 0)} />
          </div>
        )}
        {tab === "jobs" && (
          <div className="space-y-2 text-[12px] text-fg-muted">
            <div>Daemon: {daemon.status}</div>
            <div>Index running: {index?.running ? "yes" : "no"}</div>
            <div className="font-mono text-[11px] text-fg-dim">
              {index?.active_path ?? "No active path"}
            </div>
          </div>
        )}
        {tab === "logs" && (
          <div className="space-y-1 text-[12px] text-fg-muted">
            {Object.entries(connectors).length === 0 ? (
              <div>No connector state yet.</div>
            ) : (
              Object.entries(connectors).map(([id, state]) => (
                <div key={id} className="flex items-center gap-2">
                  <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: sourceTint(id) }} />
                  <span>{sourceLabel(id)}</span>
                  <span className="font-mono text-[11px] text-fg-dim">
                    {state.authenticated ? String(state.last_result?.status ?? "configured") : "off"}
                  </span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-fg-dim">{label}</div>
      <div className="mt-1 text-[16px] text-fg">{value}</div>
    </div>
  );
}

function StatusBar({
  daemon,
  indexedCount,
  index,
}: {
  daemon: ReturnType<typeof useDaemonStatus>;
  indexedCount: number;
  index: ReturnType<typeof useIndexStatus>["data"];
}) {
  return (
    <div className="flex h-7 shrink-0 items-center justify-between border-t border-border bg-[#101315] px-3 font-mono text-[11px] text-fg-dim">
      <div className="flex items-center gap-4">
        <span>{daemon.status}</span>
        <span>{indexedCount.toLocaleString()} indexed</span>
        <span>{index?.running ? "indexing" : "idle"}</span>
      </div>
      <div className="flex items-center gap-4">
        <span>{index?.active_path ? "job active" : "no job"}</span>
        <span>⌥Space spotlight</span>
      </div>
    </div>
  );
}

function DaemonPill({ daemon }: { daemon: ReturnType<typeof useDaemonStatus> }) {
  const tone =
    daemon.status === "healthy"
      ? "text-success"
      : daemon.status === "starting"
        ? "text-warn"
        : daemon.status === "degraded"
          ? "text-warn"
          : "text-danger";
  return (
    <span className={clsx("inline-flex items-center gap-1.5", tone)}>
      <CircleDot size={10} />
      <span className="font-mono text-[11px]">{daemon.status}</span>
    </span>
  );
}

function SplashScreen() {
  return (
    <div className="flex h-full items-center justify-center bg-bg">
      <div className="animate-fade-in text-sm font-mono text-fg-muted">recall · loading</div>
    </div>
  );
}

function labelForRoute(route: Route) {
  return NAV.find((item) => item.id === route)?.label ?? "Recall";
}
