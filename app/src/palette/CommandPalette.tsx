import { Command } from "cmdk";
import { motion, AnimatePresence } from "framer-motion";
import {
  ActivitySquare,
  ExternalLink,
  FolderTree,
  Layers,
  PowerOff,
  RefreshCw,
  RotateCcw,
  Search as SearchIcon,
  Settings as SettingsIcon,
  Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";

import { invoke } from "../lib/ipc";
import { useConnectorStatus } from "../lib/daemon";
import { sourceLabel } from "../lib/theme";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onNavigate: (route: "setup" | "search" | "sources" | "folders" | "settings" | "activity") => void;
}

export function CommandPalette({ open, onOpenChange, onNavigate }: Props) {
  const { data: connectors } = useConnectorStatus();
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onOpenChange(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  const authedConnectors = Object.entries(connectors ?? {}).filter(
    ([, v]) => v.authenticated,
  );

  async function runAndClose<T>(label: string, fn: () => Promise<T>) {
    try {
      setBusy(label);
      await fn();
    } finally {
      setBusy(null);
      onOpenChange(false);
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.12 }}
          className="fixed inset-0 z-40 flex items-start justify-center bg-black/60 pt-[12vh]"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) onOpenChange(false);
          }}
        >
          <motion.div
            initial={{ y: -8, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -8, opacity: 0 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            className="w-[560px] overflow-hidden rounded-[10px] border border-border bg-bg-panel shadow-floating"
          >
            <Command label="Recall command palette">
              <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
                <SearchIcon size={14} className="text-fg-muted" />
                <Command.Input
                  autoFocus
                  placeholder="Type a command or search…"
                  className="flex-1 bg-transparent text-[13px] text-fg outline-none placeholder:text-fg-dim"
                />
                {busy && (
                  <span className="font-mono text-[11px] text-fg-muted">
                    {busy}…
                  </span>
                )}
              </div>
              <Command.List className="max-h-[60vh] overflow-y-auto p-1.5">
                <Command.Empty className="px-3 py-6 text-center text-[12px] text-fg-muted">
                  No results.
                </Command.Empty>

                <Command.Group heading="Navigate">
                  <Item
                    onSelect={() => {
                      onNavigate("setup");
                      onOpenChange(false);
                    }}
                    icon={<Wrench size={14} />}
                    label="Setup"
                  />
                  <Item
                    onSelect={() => {
                      onNavigate("search");
                      onOpenChange(false);
                    }}
                    icon={<SearchIcon size={14} />}
                    label="Search"
                    hint="⌘1"
                  />
                  <Item
                    onSelect={() => {
                      onNavigate("sources");
                      onOpenChange(false);
                    }}
                    icon={<Layers size={14} />}
                    label="Sources"
                    hint="⌘2"
                  />
                  <Item
                    onSelect={() => {
                      onNavigate("folders");
                      onOpenChange(false);
                    }}
                    icon={<FolderTree size={14} />}
                    label="Folders"
                    hint="⌘3"
                  />
                  <Item
                    onSelect={() => {
                      onNavigate("settings");
                      onOpenChange(false);
                    }}
                    icon={<SettingsIcon size={14} />}
                    label="Settings"
                    hint="⌘4"
                  />
                  <Item
                    onSelect={() => {
                      onNavigate("activity");
                      onOpenChange(false);
                    }}
                    icon={<ActivitySquare size={14} />}
                    label="Activity"
                    hint="⌘5"
                  />
                </Command.Group>

                <Command.Group heading="Actions">
                  <Item
                    onSelect={() =>
                      runAndClose("sync all", () => invoke("sync", { source: null }))
                    }
                    icon={<RefreshCw size={14} />}
                    label="Sync files and sources"
                  />
                  <Item
                    onSelect={() => runAndClose("restart daemon", () => invoke("daemon_restart"))}
                    icon={<RotateCcw size={14} />}
                    label="Restart daemon"
                  />
                  {authedConnectors.map(([id]) => (
                    <Item
                      key={id}
                      onSelect={() =>
                        runAndClose(`sync ${id}`, () => invoke("sync", { source: id }))
                      }
                      icon={<RefreshCw size={14} />}
                      label={`Sync ${sourceLabel(id)}`}
                    />
                  ))}
                  <Item
                    onSelect={() => runAndClose("open logs", () => invoke("open_log_dir"))}
                    icon={<ExternalLink size={14} />}
                    label="Open ~/.vef in Finder"
                  />
                  <Item
                    onSelect={() => runAndClose("quit", () => invoke("quit_app"))}
                    icon={<PowerOff size={14} />}
                    label="Quit Recall"
                  />
                </Command.Group>
              </Command.List>
            </Command>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Item({
  onSelect,
  icon,
  label,
  hint,
}: {
  onSelect: () => void;
  icon: React.ReactNode;
  label: string;
  hint?: string;
}) {
  return (
    <Command.Item
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-2.5 rounded-panel px-2.5 py-1.5 text-[13px] text-fg-muted aria-selected:bg-bg-hover aria-selected:text-fg"
    >
      <span className="text-fg-muted">{icon}</span>
      <span className="flex-1">{label}</span>
      {hint && (
        <span className="font-mono text-[10px] text-fg-dim">{hint}</span>
      )}
    </Command.Item>
  );
}
