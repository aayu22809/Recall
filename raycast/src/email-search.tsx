import { Action, ActionPanel, Icon, List } from "@raycast/api";
import { useEffect, useRef, useState } from "react";
import { runSearch, type SearchResult } from "./lib/runner";

function gmailThreadUrl(result: SearchResult): string | null {
  const threadId = (result.metadata?.["thread_id"] as string | undefined) ?? "";
  if (!threadId) return null;
  return `https://mail.google.com/mail/u/0/#all/${threadId}`;
}

function subtitle(result: SearchResult): string {
  const from = (result.metadata?.["from"] as string | undefined) ?? "";
  const date = (result.metadata?.["date"] as string | undefined) ?? result.timestamp;
  const sender = from || "Unknown sender";
  const day = date ? String(date).slice(0, 16) : "Unknown date";
  return `${sender} • ${day}`;
}

export default function EmailSearch() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!query.trim()) {
      setResults([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    timerRef.current = setTimeout(async () => {
      try {
        const rows = await runSearch(query, { nResults: 30, sources: ["gmail"] });
        setResults(rows);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 300);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [query]);

  return (
    <List
      isLoading={loading}
      searchBarPlaceholder="Search Gmail threads..."
      onSearchTextChange={setQuery}
      throttle
    >
      {results.length === 0 && query.trim() ? (
        <List.EmptyView icon={Icon.Envelope} title="No email matches" />
      ) : null}
      {results.map((result) => {
        const url = gmailThreadUrl(result);
        return (
          <List.Item
            key={result.id}
            icon={Icon.Envelope}
            title={result.file_name || "(no subject)"}
            subtitle={subtitle(result)}
            accessories={[{ text: `${(result.similarity * 100).toFixed(1)}%` }]}
            actions={
              <ActionPanel>
                {url ? <Action.OpenInBrowser title="Open Thread" url={url} /> : null}
                <Action.CopyToClipboard title="Copy Preview" content={result.preview || result.description || ""} />
              </ActionPanel>
            }
          />
        );
      })}
    </List>
  );
}
