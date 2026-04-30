import { useEffect, useState } from "react";
import clsx from "clsx";
import { ArrowUpRight, FileText, Folder, Image as ImageIcon, Mail, Search as SearchIcon } from "lucide-react";

import { Chip } from "../../components/Chip";
import { EmptyState } from "../../components/EmptyState";
import { KeyHint } from "../../components/KeyHint";
import { invoke } from "../../lib/ipc";
import { useIndexStatus, useProgress, useSearch, useSources, useStats } from "../../lib/daemon";
import type { SearchResult } from "../../lib/daemon";
import { sourceLabel, sourceTint } from "../../lib/theme";
import { ResultPreview } from "./ResultPreview";

export function Search() {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [filter, setFilter] = useState<string[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);

  const sourcesList = useSources();
  const stats = useStats();
  const progress = useProgress();
  const index = useIndexStatus();
  const search = useSearch(debounced, filter);
  const indexingReady =
    (stats.data?.count ?? 0) > 0 || progress.data?.indexing || index.data?.running;

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(query), 140);
    return () => window.clearTimeout(t);
  }, [query]);

  useEffect(() => setActiveIdx(0), [search.data]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!search.data?.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIdx((i) => Math.min(i + 1, search.data.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        const r = search.data[activeIdx];
        if (r) invoke("open_path", { path: r.file_path });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [search.data, activeIdx]);

  const showFilters = (sourcesList.data ?? []).length > 0;
  const active = search.data?.[activeIdx];

  return (
    <div className="flex h-full">
      <div className="flex flex-1 flex-col">
        <header className="flex flex-col gap-3 border-b border-border px-6 py-5">
          <div className="flex items-center gap-2.5">
            <SearchIcon size={16} className="text-fg-muted" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${stats.data?.count ?? 0} indexed items…`}
              className="flex-1 bg-transparent text-[18px] tracking-tight text-fg outline-none placeholder:text-fg-dim"
            />
            <span className="font-mono text-[11px] text-fg-dim">
              <KeyHint keys={["⌘", "↩"]} /> open
            </span>
          </div>
          {showFilters && (
            <div className="flex flex-wrap items-center gap-1.5">
              {(sourcesList.data ?? []).map((s) => {
                const on = filter.includes(s);
                return (
                  <Chip
                    key={s}
                    active={on}
                    tint={sourceTint(s)}
                    onClick={() =>
                      setFilter((prev) =>
                        prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
                      )
                    }
                  >
                    {sourceLabel(s)}
                  </Chip>
                );
              })}
              {filter.length > 0 && (
                <Chip onClick={() => setFilter([])}>clear</Chip>
              )}
            </div>
          )}
        </header>

        <div className="flex-1 overflow-y-auto">
          {debounced.trim() === "" ? (
            <EmptyState
              title={indexingReady ? "Type to search." : "Indexing not started."}
              hint={
                indexingReady
                  ? "Try a phrase like 'lease agreement' or 'photo from last summer'."
                  : "Add a watched folder or run the first index from Setup or Folders."
              }
            />
          ) : !indexingReady ? (
            <EmptyState
              title="Search is waiting on your first index."
              hint="Add a watched folder, then run indexing."
            />
          ) : search.isLoading ? (
            <ResultsSkeleton />
          ) : !search.data?.length ? (
            <EmptyState
              title="No matches yet."
              hint="Try a different phrase or sync a connector in Sources."
            />
          ) : (
            <ul className="divide-y divide-border">
              {search.data.map((r, i) => (
                <ResultRow
                  key={r.id}
                  result={r}
                  active={i === activeIdx}
                  onHover={() => setActiveIdx(i)}
                  onOpen={() => invoke("open_path", { path: r.file_path })}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
      {active && (
        <aside className="hidden w-[360px] shrink-0 border-l border-border bg-bg-panel xl:block">
          <ResultPreview result={active} />
        </aside>
      )}
    </div>
  );
}

function ResultRow({
  result,
  active,
  onHover,
  onOpen,
}: {
  result: SearchResult;
  active: boolean;
  onHover: () => void;
  onOpen: () => void;
}) {
  const sim = Math.max(0, Math.min(1, result.similarity));
  return (
    <li
      onMouseEnter={onHover}
      onClick={onOpen}
      className={clsx(
        "group relative flex cursor-pointer items-center gap-3 px-6 py-3 transition-colors",
        active ? "bg-bg-hover" : "hover:bg-bg-hover/60",
      )}
    >
      <SourceIcon source={result.source} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[14px] text-fg">{result.file_name || "(untitled)"}</div>
        <div className="truncate font-mono text-[11px] text-fg-dim">
          {result.file_path}
        </div>
        {result.preview && (
          <div className="mt-1 line-clamp-1 text-[12px] text-fg-muted">
            {result.preview}
          </div>
        )}
      </div>
      <div className="flex flex-col items-end gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">
          {sourceLabel(result.source)}
        </span>
        <div className="h-0.5 w-16 overflow-hidden rounded-full bg-bg">
          <div
            className="h-full rounded-full bg-accent"
            style={{ width: `${sim * 100}%` }}
          />
        </div>
      </div>
      <ArrowUpRight
        size={12}
        className={clsx(
          "text-fg-dim transition-opacity",
          active ? "opacity-100 text-accent" : "opacity-0 group-hover:opacity-100",
        )}
      />
    </li>
  );
}

function SourceIcon({ source }: { source: string }) {
  const icons: Record<string, React.ReactNode> = {
    files: <Folder size={16} />,
    gmail: <Mail size={16} />,
    gcal: <FileText size={16} />,
    gdrive: <FileText size={16} />,
    notion: <FileText size={16} />,
    canvas: <FileText size={16} />,
    schoology: <FileText size={16} />,
    calai: <FileText size={16} />,
  };
  return (
    <span
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-panel border border-border bg-bg-panel"
      style={{ color: sourceTint(source) }}
    >
      {icons[source] ?? <ImageIcon size={16} />}
    </span>
  );
}

function ResultsSkeleton() {
  return (
    <ul className="divide-y divide-border">
      {Array.from({ length: 6 }).map((_, i) => (
        <li
          key={i}
          className="flex items-center gap-3 px-6 py-3"
        >
          <div className="h-9 w-9 shrink-0 rounded-panel border border-border shimmer" />
          <div className="flex-1 space-y-1.5">
            <div className="h-3 w-1/3 rounded-chip shimmer" />
            <div className="h-2.5 w-1/2 rounded-chip shimmer" />
          </div>
        </li>
      ))}
    </ul>
  );
}
