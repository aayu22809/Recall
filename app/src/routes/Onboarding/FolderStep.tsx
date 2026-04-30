import { Folder, FolderPlus, Loader2, X } from "lucide-react";
import { open } from "@tauri-apps/plugin-dialog";

import { Button } from "../../components/Button";
import {
  useAddWatchedDirMutation,
  useRemoveWatchedDirMutation,
  useWatchedDirs,
} from "../../lib/daemon";

export function FolderStep({ onAdvance }: { onAdvance: () => void }) {
  const dirs = useWatchedDirs();
  const add = useAddWatchedDirMutation();
  const remove = useRemoveWatchedDirMutation();

  async function pick() {
    const selected = await open({
      directory: true,
      multiple: true,
      title: "Pick folders to index",
    });
    if (!selected) return;
    const items = Array.isArray(selected) ? selected : [selected];
    for (const path of items) {
      await add.mutateAsync(path);
    }
  }

  const paths = dirs.data?.dirs ?? [];

  return (
    <div className="max-w-[640px]">
      <h2 className="text-[22px] font-semibold tracking-tight">Watch folders</h2>
      <p className="mt-2 text-[13px] text-fg-muted">
        Add at least one folder you want indexed. Existing supported files will
        be queued immediately, and new files are watched after that.
      </p>

      <div className="mt-8 border border-border bg-bg-panel">
        <ul className="divide-y divide-border">
          {paths.length === 0 && (
            <li className="px-4 py-6 text-center text-[13px] text-fg-muted">
              No folders yet. Your documents folder is a good first pick.
            </li>
          )}
          {paths.map((path) => (
            <li key={path} className="flex items-center gap-2.5 px-3 py-2">
              <Folder size={14} className="text-fg-muted" />
              <span className="flex-1 truncate font-mono text-[12px] text-fg">
                {path}
              </span>
              <button
                onClick={() => remove.mutate(path)}
                className="rounded-chip p-1 text-fg-dim transition-colors hover:bg-bg-hover hover:text-danger"
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
        <div className="border-t border-border p-2">
          <Button variant="ghost" onClick={pick} className="w-full justify-center">
            {add.isPending ? <Loader2 size={14} className="animate-spin" /> : <FolderPlus size={14} />}
            Add folder
          </Button>
        </div>
      </div>

      <div className="mt-8">
        <Button variant="primary" onClick={onAdvance} disabled={paths.length === 0}>
          Continue
        </Button>
      </div>
    </div>
  );
}
