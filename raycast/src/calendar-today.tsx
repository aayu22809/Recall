import { Action, ActionPanel, Icon, List } from "@raycast/api";
import { useEffect, useMemo, useState } from "react";
import { runSearch, type SearchResult } from "./lib/runner";

function eventStart(result: SearchResult): string {
  const start = (result.metadata?.["start"] as string | undefined) ?? result.timestamp ?? "";
  return start;
}

function eventUrl(result: SearchResult): string | null {
  if (result.source === "gcal") {
    const eventId = (result.metadata?.["event_id"] as string | undefined) ?? "";
    if (eventId) return `https://calendar.google.com/calendar/r/eventedit/${eventId}`;
  }
  const url = (result.metadata?.["url"] as string | undefined) ?? "";
  return url || null;
}

function sectionTitle(start: string): string {
  if (!start) return "Unknown date";
  return start.slice(0, 10);
}

export default function CalendarToday() {
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    runSearch("upcoming events today this week", { sources: ["gcal", "calai"], nResults: 20 })
      .then((rows) => {
        if (!mounted) return;
        const sorted = [...rows].sort((a, b) => eventStart(a).localeCompare(eventStart(b)));
        setResults(sorted);
      })
      .catch(() => {
        if (!mounted) return;
        setResults([]);
      })
      .finally(() => {
        if (!mounted) return;
        setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const grouped = useMemo(() => {
    const map = new Map<string, SearchResult[]>();
    for (const row of results) {
      const key = sectionTitle(eventStart(row));
      const bucket = map.get(key) ?? [];
      bucket.push(row);
      map.set(key, bucket);
    }
    return [...map.entries()];
  }, [results]);

  return (
    <List isLoading={loading} searchBarPlaceholder="Upcoming events">
      {grouped.length === 0 && !loading ? (
        <List.EmptyView icon={Icon.Calendar} title="No upcoming events found" />
      ) : null}
      {grouped.map(([day, rows]) => (
        <List.Section key={day} title={day}>
          {rows.map((result) => {
            const start = eventStart(result);
            const url = eventUrl(result);
            return (
              <List.Item
                key={result.id}
                icon={Icon.Calendar}
                title={result.file_name || "Untitled event"}
                subtitle={start ? start.slice(11, 16) : ""}
                accessories={[{ text: result.source }]}
                actions={
                  <ActionPanel>
                    {url ? <Action.OpenInBrowser title="Open Event" url={url} /> : null}
                    <Action.CopyToClipboard title="Copy Event Summary" content={result.description || result.preview || ""} />
                  </ActionPanel>
                }
              />
            );
          })}
        </List.Section>
      ))}
    </List>
  );
}
