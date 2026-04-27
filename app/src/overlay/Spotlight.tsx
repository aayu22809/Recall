import { motion, AnimatePresence } from "framer-motion";
import { ArrowRight, Search as SearchIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { invoke } from "../lib/ipc";
import type { SearchResult } from "../lib/daemon";
import { sourceLabel, sourceTint } from "../lib/theme";

export function Spotlight() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<number | null>(null);

  const expanded = query.trim().length > 0;

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      return;
    }
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(async () => {
      try {
        setLoading(true);
        const res = await invoke<SearchResult[]>("search", {
          args: { query: trimmed, n_results: 8 },
        });
        setResults(res);
        setActiveIdx(0);
      } finally {
        setLoading(false);
      }
    }, 140);
  }, [query]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        void hideSpotlight();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIdx((i) => Math.min(i + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const r = results[activeIdx];
        if (r) {
          invoke("open_path", { path: r.file_path });
          void hideSpotlight();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [results, activeIdx]);

  const list = useMemo(() => results.slice(0, 8), [results]);

  return (
    <div className="flex min-h-screen flex-col items-stretch bg-transparent">
      <motion.div
        layout
        transition={{ type: "spring", stiffness: 320, damping: 32 }}
        className="title-drag mx-auto flex w-full max-w-[640px] flex-col overflow-hidden rounded-[14px] border border-border bg-bg-panel/95 shadow-floating backdrop-blur-2xl"
      >
        <div className="title-no-drag flex items-center gap-3 px-4 py-3">
          <SearchIcon size={16} className="text-fg-muted" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Recall…"
            className="flex-1 bg-transparent text-[16px] text-fg outline-none placeholder:text-fg-dim"
          />
          {loading && (
            <span className="font-mono text-[11px] text-fg-muted">…</span>
          )}
        </div>
        <AnimatePresence initial={false}>
          {expanded && (
            <motion.ul
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="title-no-drag border-t border-border"
            >
              {list.length === 0 && !loading && (
                <li className="px-4 py-3 text-[13px] text-fg-muted">
                  No matches yet.
                </li>
              )}
              {list.map((r, i) => (
                <li
                  key={r.id}
                  onMouseEnter={() => setActiveIdx(i)}
                  onClick={() => {
                    invoke("open_path", { path: r.file_path });
                    void hideSpotlight();
                  }}
                  className={clsx(
                    "flex cursor-pointer items-center gap-3 px-4 py-2.5 text-[13px]",
                    i === activeIdx ? "bg-bg-hover" : "",
                  )}
                >
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: sourceTint(r.source) }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-fg">{r.file_name}</div>
                    <div className="truncate font-mono text-[11px] text-fg-dim">
                      {r.file_path}
                    </div>
                  </div>
                  <span className="shrink-0 font-mono text-[10px] text-fg-muted">
                    {sourceLabel(r.source)}
                  </span>
                  {i === activeIdx && (
                    <ArrowRight size={12} className="text-accent" />
                  )}
                </li>
              ))}
            </motion.ul>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
  async function hideSpotlight() {
    await getCurrentWindow().hide();
  }
