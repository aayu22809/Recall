import { useEffect, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";

import { Button } from "../../components/Button";
import { useIndexStatus, useProgress, useSyncMutation, useWatchedDirs } from "../../lib/daemon";

export function SyncStep({ onAdvance }: { onAdvance: () => void }) {
  const [started, setStarted] = useState(false);
  const [done, setDone] = useState(false);
  const sync = useSyncMutation();
  const progress = useProgress();
  const index = useIndexStatus();
  const dirs = useWatchedDirs();

  async function start() {
    setStarted(true);
    await sync.mutateAsync(undefined);
  }

  useEffect(() => {
    if (!started) return;
    const hasFolders = (dirs.data?.dirs ?? []).length > 0;
    if (!hasFolders) {
      setDone(true);
      return;
    }
    if (!index.data?.running && (progress.data?.total_indexed ?? 0) > 0) {
      setDone(true);
    }
  }, [started, dirs.data, index.data, progress.data]);

  return (
    <div className="max-w-[560px]">
      <h2 className="text-[22px] font-semibold tracking-tight">First sync</h2>
      <p className="mt-2 text-[13px] text-fg-muted">
        This starts file indexing and any connected source syncs together. The
        progress here is real daemon state, not a placeholder.
      </p>

      <div className="mt-8 border border-border bg-bg-panel p-5">
        {!started ? (
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-[14px] text-fg">Ready to index.</div>
              <div className="text-[12px] text-fg-muted">
                Watched folders: {(dirs.data?.dirs ?? []).length}
              </div>
            </div>
            <Button variant="primary" onClick={start} disabled={sync.isPending}>
              Start indexing
            </Button>
          </div>
        ) : done ? (
          <div className="flex items-center gap-2 text-success">
            <CheckCircle2 size={14} />
            <span className="text-[14px]">Index ready.</span>
            <span className="ml-auto font-mono text-[11px] text-fg-muted">
              {progress.data?.total_indexed ?? 0} indexed
            </span>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Loader2 size={14} className="animate-spin text-accent" />
              <span className="text-[14px] text-fg">Working…</span>
              <span className="ml-auto font-mono text-[11px] text-fg-muted">
                {progress.data?.queued ?? 0} queued
              </span>
            </div>
            <div className="grid grid-cols-4 gap-2 font-mono text-[11px] text-fg-muted">
              <div>{index.data?.processed ?? 0} processed</div>
              <div>{index.data?.embedded ?? 0} embedded</div>
              <div>{index.data?.skipped ?? 0} skipped</div>
              <div>{index.data?.errors ?? 0} errors</div>
            </div>
            {index.data?.active_path && (
              <div className="truncate font-mono text-[11px] text-fg-dim">
                {index.data.active_path}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="mt-8">
        <Button variant="primary" onClick={onAdvance} disabled={!done}>
          Open Recall
        </Button>
      </div>
    </div>
  );
}
