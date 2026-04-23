/**
 * Source-aware result card for the Recall search grid.
 *
 * Renders a different icon, subtitle, and action set depending on
 * the `source` and `media_category` fields of the search result.
 */

// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React from "react";
import { Grid, ActionPanel, Action, Icon, Color, Image, open } from "@raycast/api";
import { existsSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";
import { environment } from "@raycast/api";
import type { SearchResult } from "../lib/runner";

// ── Thumbnail helpers ─────────────────────────────────────────────────────────

const THUMB_DIR = join(environment.supportPath, "thumbnails");

function getVideoThumbnail(filePath: string, fileId: string): string | null {
  const thumbPath = join(THUMB_DIR, `${fileId}.jpg`);
  if (existsSync(thumbPath)) return thumbPath;
  try {
    execSync(
      `ffmpeg -y -i "${filePath}" -ss 00:00:01 -frames:v 1 -q:v 2 "${thumbPath}" 2>/dev/null`,
      { timeout: 5000, encoding: "utf-8" },
    );
    return existsSync(thumbPath) ? thumbPath : null;
  } catch {
    return null;
  }
}

// ── Icon mapping ──────────────────────────────────────────────────────────────

interface IconSpec {
  source: Icon;
  tintColor: Color;
}

function getResultIcon(result: SearchResult): IconSpec {
  // Source takes priority over media category for non-file sources
  switch (result.source) {
    case "gmail":
      return { source: Icon.Envelope, tintColor: Color.Red };
    case "gcal":
    case "calai":
      return { source: Icon.Calendar, tintColor: Color.Blue };
    case "canvas":
    case "schoology":
      return { source: Icon.Book, tintColor: Color.Orange };
  }

  // Fallback: media category
  switch (result.media_category) {
    case "image":
      return { source: Icon.Image, tintColor: Color.Blue };
    case "audio":
      return { source: Icon.Microphone, tintColor: Color.Purple };
    case "video":
      return { source: Icon.Video, tintColor: Color.Red };
    case "document":
      return { source: Icon.Document, tintColor: Color.Orange };
    case "text":
      return { source: Icon.Text, tintColor: Color.Green };
    case "email":
      return { source: Icon.Envelope, tintColor: Color.Red };
    case "calendar_event":
      return { source: Icon.Calendar, tintColor: Color.Blue };
    case "lms_item":
      return { source: Icon.Book, tintColor: Color.Orange };
    default:
      return { source: Icon.QuestionMarkCircle, tintColor: Color.SecondaryText };
  }
}

// ── Subtitle builders ─────────────────────────────────────────────────────────

function buildSubtitle(result: SearchResult, rank: number): string {
  const score = `${(result.similarity * 100).toFixed(1)}%`;
  const prefix = `#${rank} · ${score}`;

  switch (result.source) {
    case "gmail": {
      const from = result.metadata?.["from"] as string | undefined;
      const date = (result.metadata?.["date"] as string | undefined)?.slice(0, 16) ?? "";
      return `${prefix} · ${from ?? ""} · ${date}`;
    }
    case "gcal":
    case "calai": {
      const start = result.metadata?.["start"] as string | undefined;
      return `${prefix} · ${start?.slice(0, 16) ?? ""}`;
    }
    case "canvas":
    case "schoology": {
      const course = result.metadata?.["course_name"] as string | undefined;
      const due = result.metadata?.["due_at"] as string | undefined;
      return `${prefix} · ${course ?? ""}${due ? ` · due ${due.slice(0, 10)}` : ""}`;
    }
    default:
      return `${prefix}`;
  }
}

// ── Action builders ───────────────────────────────────────────────────────────

function buildActions(result: SearchResult): React.ReactNode {
  const actions: React.ReactNode[] = [];

  switch (result.source) {
    case "gmail": {
      const threadId = result.metadata?.["thread_id"] as string | undefined;
      if (threadId) {
        const url = `https://mail.google.com/mail/u/0/#all/${threadId}`;
        actions.push(
          <Action
            key="open-gmail"
            title="Open in Gmail"
            icon={Icon.Globe}
            onAction={() => open(url)}
          />,
        );
      }
      break;
    }
    case "gcal": {
      const eventId = result.metadata?.["event_id"] as string | undefined;
      if (eventId) {
        const url = `https://calendar.google.com/calendar/r/eventedit/${eventId}`;
        actions.push(
          <Action
            key="open-gcal"
            title="Open in Calendar"
            icon={Icon.Globe}
            onAction={() => open(url)}
          />,
        );
      }
      break;
    }
    case "canvas":
    case "schoology": {
      const url = result.metadata?.["url"] as string | undefined;
      if (url) {
        actions.push(
          <Action
            key="open-lms"
            title={`Open in ${result.source === "canvas" ? "Canvas" : "Schoology"}`}
            icon={Icon.Globe}
            onAction={() => open(url)}
          />,
        );
      }
      break;
    }
    default: {
      const fp = result.file_path;
      if (fp && existsSync(fp)) {
        actions.push(
          <Action.Open key="open-file" title="Open File" target={fp} />,
          <Action.ShowInFinder key="show-finder" path={fp} />,
        );
      }
    }
  }

  actions.push(
    <Action.CopyToClipboard
      key="copy-path"
      title="Copy Path / URL"
      content={result.file_path || result.preview || ""}
      shortcut={{ modifiers: ["cmd"], key: "c" }}
    />,
  );

  if (result.preview) {
    actions.push(
      <Action.CopyToClipboard
        key="copy-preview"
        title="Copy Preview Text"
        content={result.preview}
        shortcut={{ modifiers: ["cmd", "shift"], key: "c" }}
      />,
    );
  }

  return <ActionPanel>{actions}</ActionPanel>;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface ResultCardProps {
  result: SearchResult;
  rank: number;
}

export function ResultCard({ result, rank }: ResultCardProps) {
  const icon = getResultIcon(result);

  // Determine visual content
  let contentSource: string | null = null;
  const fp = result.file_path;
  const fileExists = fp ? existsSync(fp) : false;

  if (fileExists) {
    if (result.media_category === "image") {
      contentSource = fp;
    } else if (result.media_category === "video") {
      contentSource = getVideoThumbnail(fp, result.id);
    }
  }

  // Grid.Item content is Image.ImageLike (source + optional tintColor)
  const content: Image.ImageLike = contentSource
    ? { source: contentSource as Image.Source }
    : { source: icon.source, tintColor: icon.tintColor };

  const subtitle = buildSubtitle(result, rank);

  return (
    <Grid.Item
      key={result.id || `result-${rank}`}
      content={content}
      title={result.file_name || result.metadata?.["subject"] as string || "Result"}
      subtitle={subtitle}
      actions={buildActions(result)}
    />
  );
}
