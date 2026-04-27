import { useEffect, useState } from "react";
import { Folder, FolderPlus, X } from "lucide-react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import clsx from "clsx";

import { Button } from "../../components/Button";
import {
  useAddWatchedDirMutation,
  useIndexWatchedDirsMutation,
  useRemoveWatchedDirMutation,
  useWatchedDirs,
  useWatchedDirsStats,
} from "../../lib/daemon";

export function Folders() {
  const dirs = useWatchedDirs();
  const stats = useWatchedDirsStats();
  const add = useAddWatchedDirMutation();
  const index = useIndexWatchedDirsMutation();
  const remove = useRemoveWatchedDirMutation();
  const [hovering, setHovering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function block(e: DragEvent) {
      e.preventDefault();
    }
    async function onDrop(e: DragEvent) {
      e.preventDefault();
      setHovering(false);
      const files = Array.from(e.dataTransfer?.files ?? []) as Array<File & { path?: string }>;
      const directories = files
        .map((file) => file.path)
        .filter((value): value is string => Boolean(value));
      if (directories.length === 0) {
        setError("Drop one or more folders from Finder.");
        return;
      }
      try {
        setError(null);
        for (const path of directories) {
          await add.mutateAsync(path);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
    window.addEventListener("dragover", block);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragover", block);
      window.removeEventListener("drop", onDrop);
    };
  }, [add]);

  async function pickFolder() {
    const selected = await openDialog({
      directory: true,
      multiple: true,
    });
    if (!selected) return;
    const arr = Array.isArray(selected) ? selected : [selected];
    for (const path of arr) await add.mutateAsync(path);
  }

  function statsFor(path: string): number {
    const m = stats.data?.stats?.find((s) => s.path === path);
    return m?.count ?? 0;
  }

  return (
    <div className="px-8 py-8">
      <header className="mb-6">
        <h2 className="text-[20px] font-semibold tracking-tight">Watched folders</h2>
        <p className="mt-1 text-[13px] text-fg-muted">
          Files inside these folders are auto-indexed within ~10s of being
          added or modified.
        </p>
      </header>

      <button
        onDragEnter={() => setHovering(true)}
        onDragLeave={() => setHovering(false)}
        onClick={pickFolder}
        className={clsx(
          "flex w-full items-center justify-center gap-2.5 rounded-panel border border-dashed py-10 text-[13px] transition-colors",
          hovering
            ? "border-accent bg-accent-soft text-fg"
            : "border-border bg-bg-panel text-fg-muted hover:border-border-strong hover:text-fg",
        )}
      >
        <FolderPlus size={14} />
        Drop a folder here, or click to pick.
      </button>
      {error && (
        <div className="mt-3 border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger">
          {error}
        </div>
      )}

      <ul className="mt-6 divide-y divide-border rounded-panel border border-border bg-bg-panel">
        {(dirs.data?.dirs ?? []).length === 0 && (
          <li className="px-4 py-6 text-center text-[12px] text-fg-muted">
            No folders watched yet.
          </li>
        )}
        {(dirs.data?.dirs ?? []).map((p) => (
          <li key={p} className="flex items-center gap-3 px-4 py-3">
            <Folder size={14} className="text-fg-muted" />
            <div className="min-w-0 flex-1">
              <div className="truncate font-mono text-[12px] text-fg">{p}</div>
              <div className="font-mono text-[10px] text-fg-dim">
                {statsFor(p)} indexed
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => remove.mutate(p)}
              disabled={remove.isPending}
            >
              <X size={11} />
              Remove
            </Button>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex justify-end">
        <Button variant="secondary" onClick={() => index.mutate()} disabled={index.isPending}>
          <FolderPlus size={11} />
          Index watched folders now
        </Button>
      </div>
    </div>
  );
}
