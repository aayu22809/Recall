// Constants mirrored from globals.css for places where TS needs the values
// (canvas drawing, dynamic gradients, etc.). Keep in sync.

export const tokens = {
  bg: "#111315",
  bgPanel: "#171a1d",
  bgHover: "#202428",
  bgInput: "#14171a",
  border: "#2a2f33",
  borderStrong: "#363d42",
  fg: "#edf0f2",
  fgMuted: "#9ca5ad",
  fgDim: "#6d767f",
  accent: "#6bc28b",
  success: "#4ade80",
  warn: "#f59e0b",
  danger: "#ef4444",
};

export const sourceMeta: Record<
  string,
  { label: string; description: string; tint: string }
> = {
  files: {
    label: "Files",
    description: "Watched folders on disk",
    tint: "#6bc28b",
  },
  gmail: {
    label: "Gmail",
    description: "Email threads, last 6 months",
    tint: "#ef4444",
  },
  gcal: {
    label: "Calendar",
    description: "Past 3mo + upcoming 6mo",
    tint: "#22c55e",
  },
  gdrive: {
    label: "Drive",
    description: "Docs, Sheets, Slides, files",
    tint: "#facc15",
  },
  calai: { label: "cal.ai", description: "Cal.com bookings", tint: "#38bdf8" },
  canvas: { label: "Canvas", description: "LMS courses, assignments", tint: "#f97316" },
  schoology: {
    label: "Schoology",
    description: "LMS courses, posts",
    tint: "#60a5fa",
  },
  notion: { label: "Notion", description: "Pages and databases", tint: "#e2e2e2" },
};

export function sourceTint(id: string): string {
  return sourceMeta[id]?.tint ?? "#6bc28b";
}

export function sourceLabel(id: string): string {
  return sourceMeta[id]?.label ?? id;
}
