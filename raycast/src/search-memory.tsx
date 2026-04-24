import { useState, useRef, useCallback, useEffect } from "react";
import {
  Grid,
  Icon,
  showToast,
  Toast,
  openExtensionPreferences,
  ActionPanel,
  Action,
} from "@raycast/api";
import { mkdirSync } from "fs";
import { join } from "path";
import { environment } from "@raycast/api";
import { runSearch, validateSetup, fetchSources, VefRunnerError } from "./lib/runner";
import type { SearchResult } from "./lib/runner";
import { ResultCard } from "./components/ResultCard";

// Ensure thumbnail cache directory exists
try {
  mkdirSync(join(environment.supportPath, "thumbnails"), { recursive: true });
} catch {
  // ignore
}

// ── Source filter helpers ─────────────────────────────────────────────────────

const SOURCE_LABELS: Record<string, string> = {
  files: "Files",
  gmail: "Gmail",
  gcal: "Google Calendar",
  calai: "cal.ai",
  canvas: "Canvas",
  schoology: "Schoology",
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SearchMemory() {
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [setupError, setSetupError] = useState<string | null>(null);
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);
  const [availableSources, setAvailableSources] = useState<string[]>([]);

  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const cacheRef = useRef<Map<string, SearchResult[]>>(new Map());
  const validatedRef = useRef(false);

  // Fetch available sources on mount
  useEffect(() => {
    fetchSources().then((sources) => {
      if (sources.length > 0) setAvailableSources(sources);
    });
  }, []);

  const handleSearchChange = useCallback(
    (text: string) => {
      setSearchText(text);
      if (timerRef.current) clearTimeout(timerRef.current);

      if (!text.trim()) {
        setResults([]);
        setIsLoading(false);
        return;
      }

      // Validate once, asynchronously
      if (!validatedRef.current) {
        validatedRef.current = true;
        validateSetup().catch((err: VefRunnerError | Error) => {
          const msg = err instanceof VefRunnerError
            ? `${err.code}: ${err.message}`
            : err.message;
          setSetupError(msg);
          showToast({ style: Toast.Style.Failure, title: "Setup Error", message: msg });
        });
      }

      if (setupError) return;

      const cacheKey = `${text.trim().toLowerCase()}::${sourceFilter ?? "all"}`;
      const cached = cacheRef.current.get(cacheKey);
      if (cached) {
        setResults(cached);
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      timerRef.current = setTimeout(async () => {
        try {
          const sources = sourceFilter ? [sourceFilter] : null;
          const r = await runSearch(text, { nResults: 20, sources });
          cacheRef.current.set(cacheKey, r);
          setResults(r);
        } catch (err: unknown) {
          const e = err as VefRunnerError;
          if (e.code === "AUTH_ERROR") {
            showToast({
              style: Toast.Style.Failure,
              title: "Auth Error",
              message: "GEMINI_API_KEY missing or invalid — check extension preferences",
            });
          } else if (e.code === "RATE_LIMIT") {
            showToast({ style: Toast.Style.Failure, title: "Rate Limited", message: e.message });
          } else if (e.code === "TIMEOUT") {
            showToast({ style: Toast.Style.Failure, title: "Timeout", message: "Search took too long" });
          } else {
            showToast({ style: Toast.Style.Failure, title: "Search Failed", message: e.message });
          }
          setResults([]);
        } finally {
          setIsLoading(false);
        }
      }, 400);
    },
    [setupError, sourceFilter],
  );

  // Re-search when source filter changes
  useEffect(() => {
    if (searchText.trim()) {
      cacheRef.current.clear();
      handleSearchChange(searchText);
    }
  }, [sourceFilter]);

  if (setupError) {
    return (
      <Grid columns={4} searchBarPlaceholder="Search memory...">
        <Grid.EmptyView
          icon={Icon.ExclamationMark}
          title="Setup Required"
          description={setupError}
          actions={
            <ActionPanel>
              <Action title="Open Extension Preferences" icon={Icon.Gear} onAction={openExtensionPreferences} />
            </ActionPanel>
          }
        />
      </Grid>
    );
  }

  // Build source filter dropdown items
  const sourceAccessory = availableSources.length > 0 ? (
    <Grid.Dropdown
      tooltip="Filter by source"
      value={sourceFilter ?? ""}
      onChange={(v) => setSourceFilter(v || null)}
    >
      <Grid.Dropdown.Item title="All Sources" value="" />
      {availableSources.map((s) => (
        <Grid.Dropdown.Item key={s} title={sourceLabel(s)} value={s} />
      ))}
    </Grid.Dropdown>
  ) : undefined;

  return (
    <Grid
      columns={4}
      fit={Grid.Fit.Fill}
      inset={Grid.Inset.Zero}
      isLoading={isLoading}
      searchBarPlaceholder="Search memory…"
      searchBarAccessory={sourceAccessory}
      onSearchTextChange={handleSearchChange}
    >
      {searchText.trim() === "" && !isLoading ? (
        <Grid.EmptyView
          icon={Icon.MagnifyingGlass}
          title="Search your digital memory"
          description="Type to search files, emails, calendar events, and more"
          actions={
            <ActionPanel>
              <Action title="Open Extension Preferences" icon={Icon.Gear} onAction={openExtensionPreferences} />
            </ActionPanel>
          }
        />
      ) : null}

      {results.length === 0 && searchText.trim() !== "" && !isLoading ? (
        <Grid.EmptyView
          icon={Icon.MagnifyingGlass}
          title="No results found"
          description={
            sourceFilter
              ? `No matches in ${sourceLabel(sourceFilter)} — try All Sources`
              : "Try a different query"
          }
        />
      ) : null}

      {results.map((result, index) => (
        <ResultCard key={result.id || `result-${index}`} result={result} rank={index + 1} />
      ))}
    </Grid>
  );
}
