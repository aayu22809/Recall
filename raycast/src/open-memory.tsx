import { showHUD, open, openExtensionPreferences } from "@raycast/api";
import { existsSync } from "fs";
import { runSearch } from "./lib/runner";

export default async function OpenMemory(props: { arguments: { query: string } }) {
  const { query } = props.arguments;
  if (!query?.trim()) {
    await showHUD("⚠ No query provided");
    return;
  }

  try {
    const results = await runSearch(query, { nResults: 1 });

    if (results.length === 0) {
      await showHUD(`No matching result for: ${query}`);
      return;
    }

    const r = results[0];
    const score = (r.similarity * 100).toFixed(1);

    // Local file
    if (r.file_path && !r.file_path.includes("://") && existsSync(r.file_path)) {
      await open(r.file_path);
      await showHUD(`Opened: ${r.file_name} (${score}% match)`);
      return;
    }

    // Gmail
    if (r.source === "gmail" && r.metadata?.["thread_id"]) {
      const url = `https://mail.google.com/mail/u/0/#all/${r.metadata["thread_id"]}`;
      await open(url);
      await showHUD(`Opened Gmail: ${r.file_name} (${score}% match)`);
      return;
    }

    // Google Calendar
    if (r.source === "gcal" && r.metadata?.["event_id"]) {
      const url = `https://calendar.google.com/calendar/r/eventedit/${r.metadata["event_id"]}`;
      await open(url);
      await showHUD(`Opened Calendar: ${r.file_name} (${score}% match)`);
      return;
    }

    // Canvas / Schoology — open URL from metadata
    if ((r.source === "canvas" || r.source === "schoology") && r.metadata?.["url"]) {
      await open(r.metadata["url"] as string);
      await showHUD(`Opened ${r.source}: ${r.file_name} (${score}% match)`);
      return;
    }

    // Fallback: copy preview to clipboard
    await showHUD(`Found: ${r.file_name} (${score}% match) — no openable URL`);

  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("GEMINI_API_KEY") || msg.includes("AUTH_ERROR")) {
      await showHUD("⚠ API key missing — check extension preferences");
      await openExtensionPreferences();
    } else if (msg.includes("daemon") || msg.includes("DAEMON")) {
      await showHUD("⚠ Daemon not running — run 'vef-daemon start' in terminal");
    } else {
      await showHUD(`⚠ Search failed: ${msg.slice(0, 60)}`);
    }
  }
}
