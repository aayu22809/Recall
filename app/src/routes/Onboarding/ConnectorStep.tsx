import { useState } from "react";
import clsx from "clsx";
import { CheckCircle2, ExternalLink, Loader2, Lock, Plug } from "lucide-react";

import { Button } from "../../components/Button";
import { invoke } from "../../lib/ipc";
import { sourceTint } from "../../lib/theme";

interface ConnectorDef {
  id: string;
  label: string;
  description: string;
  type: "google" | "token" | "form";
  scopes?: string[];
  fields?: { name: string; label: string; placeholder?: string; secret?: boolean }[];
  envMap?: Record<string, string>; // form-field → /configure key
}

const CONNECTORS: ConnectorDef[] = [
  {
    id: "gmail",
    label: "Gmail",
    description: "Email threads (read-only, last 6 months)",
    type: "google",
    scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
  },
  {
    id: "gcal",
    label: "Calendar",
    description: "Past 3 months + upcoming 6 months",
    type: "google",
    scopes: ["https://www.googleapis.com/auth/calendar.readonly"],
  },
  {
    id: "gdrive",
    label: "Drive",
    description: "Docs, Sheets, Slides, files",
    type: "google",
    scopes: ["https://www.googleapis.com/auth/drive.readonly"],
  },
  {
    id: "notion",
    label: "Notion",
    description: "Pages and databases shared with the integration",
    type: "token",
    fields: [{ name: "api_key", label: "Integration token", placeholder: "ntn_…", secret: true }],
  },
  {
    id: "calai",
    label: "cal.ai",
    description: "Bookings and meeting metadata",
    type: "token",
    fields: [{ name: "api_key", label: "API key", placeholder: "cal_…", secret: true }],
  },
  {
    id: "canvas",
    label: "Canvas",
    description: "Courses, assignments, modules",
    type: "form",
    fields: [
      { name: "base_url", label: "Canvas URL", placeholder: "https://canvas.instructure.com" },
      { name: "token", label: "Access token", secret: true },
    ],
    envMap: { base_url: "canvas_base_url", token: "canvas_api_key" },
  },
  {
    id: "schoology",
    label: "Schoology",
    description: "Courses, posts",
    type: "form",
    fields: [
      { name: "base_url", label: "Schoology API URL", placeholder: "https://api.schoology.com/v1" },
      { name: "consumer_key", label: "Consumer key" },
      { name: "consumer_secret", label: "Consumer secret", secret: true },
    ],
    envMap: {
      base_url: "schoology_base_url",
      consumer_key: "schoology_consumer_key",
      consumer_secret: "schoology_consumer_secret",
    },
  },
];

type ConnState = "idle" | "connecting" | "connected" | "error";

export function ConnectorStep({ onAdvance }: { onAdvance: () => void }) {
  const [state, setState] = useState<Record<string, ConnState>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [forms, setForms] = useState<Record<string, Record<string, string>>>({});
  const [open, setOpen] = useState<string | null>(null);

  function setField(id: string, name: string, value: string) {
    setForms((prev) => ({ ...prev, [id]: { ...prev[id], [name]: value } }));
  }

  async function connect(c: ConnectorDef) {
    setState((s) => ({ ...s, [c.id]: "connecting" }));
    try {
      if (c.type === "google") {
        await invoke("oauth_connect_google", {
          args: { source: c.id, scopes: c.scopes ?? [] },
        });
      } else if (c.type === "token") {
        const v = forms[c.id] ?? {};
        if (!v.api_key?.trim()) throw new Error("Token required");
        await invoke("write_credential", {
          source: c.id,
          payload: { api_key: v.api_key.trim() },
        });
      } else if (c.type === "form") {
        const v = forms[c.id] ?? {};
        const payload: Record<string, string> = {};
        for (const f of c.fields ?? []) {
          if (!v[f.name]?.trim()) throw new Error(`${f.label} required`);
          payload[f.name] = v[f.name].trim();
        }
        await invoke("write_credential", { source: c.id, payload });
        if (c.envMap) {
          const cfg: Record<string, string> = {};
          for (const [field, envKey] of Object.entries(c.envMap)) {
            if (payload[field]) cfg[envKey] = payload[field];
          }
          await invoke("configure", { payload: cfg });
        }
      }
      setState((s) => ({ ...s, [c.id]: "connected" }));
    } catch (e: unknown) {
      setErrors((m) => ({ ...m, [c.id]: e instanceof Error ? e.message : String(e) }));
      setState((s) => ({ ...s, [c.id]: "error" }));
    }
  }

  return (
    <div className="max-w-[640px]">
      <h2 className="text-[22px] font-semibold tracking-tight">
        Connect sources
      </h2>
      <p className="mt-2 text-[13px] text-fg-muted">
        Each source is opt-in. Read-only OAuth scopes for Google products. You
        can connect more later.
      </p>

      <div className="mt-3 flex items-start gap-2 rounded-panel border border-warn/30 bg-warn/5 p-3 text-[12px] text-warn">
        <Lock size={12} className="mt-0.5 shrink-0" />
        <div className="text-fg-muted">
          <span className="text-warn">Beta note —</span> while we finish Google's
          security review, Gmail re-authorization is required every 7 days.
          We'll auto-update the app when verification completes.
        </div>
      </div>

      <div className="mt-6 grid gap-2">
        {CONNECTORS.map((c) => {
          const st = state[c.id] ?? "idle";
          const isOpen = open === c.id && c.type !== "google";
          return (
            <div
              key={c.id}
              className="rounded-panel border border-border bg-bg-panel"
            >
              <div className="flex items-center gap-3 px-3 py-2.5">
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: sourceTint(c.id) }}
                />
                <div className="flex-1">
                  <div className="text-[13px] text-fg">{c.label}</div>
                  <div className="text-[11px] text-fg-muted">{c.description}</div>
                </div>
                <ConnectButton
                  state={st}
                  type={c.type}
                  onClick={() => {
                    if (c.type === "google") connect(c);
                    else setOpen(isOpen ? null : c.id);
                  }}
                />
              </div>
              {isOpen && (
                <div className="border-t border-border px-3 py-3">
                  {(c.fields ?? []).map((f) => (
                    <label key={f.name} className="mb-2 block last:mb-0">
                      <div className="mb-1 text-[11px] text-fg-muted">{f.label}</div>
                      <input
                        type={f.secret ? "password" : "text"}
                        value={forms[c.id]?.[f.name] ?? ""}
                        onChange={(e) => setField(c.id, f.name, e.target.value)}
                        placeholder={f.placeholder}
                        className={clsx(
                          "h-8 w-full rounded-panel border border-border bg-bg-input px-2.5 font-mono text-[12px] text-fg outline-none transition-colors placeholder:text-fg-dim",
                          "focus:border-accent/60",
                        )}
                      />
                    </label>
                  ))}
                  <div className="mt-3 flex items-center justify-end gap-2">
                    <Button variant="ghost" size="sm" onClick={() => setOpen(null)}>
                      Cancel
                    </Button>
                    <Button variant="primary" size="sm" onClick={() => connect(c)}>
                      Save
                    </Button>
                  </div>
                </div>
              )}
              {errors[c.id] && (
                <div className="border-t border-border px-3 py-2 text-[11px] text-danger">
                  {errors[c.id]}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-8 flex items-center gap-3">
        <Button variant="primary" onClick={onAdvance}>
          Continue
        </Button>
        <span className="text-[11px] text-fg-dim">
          You can connect more in Sources later.
        </span>
      </div>
    </div>
  );
}

function ConnectButton({
  state,
  type,
  onClick,
}: {
  state: ConnState;
  type: ConnectorDef["type"];
  onClick: () => void;
}) {
  if (state === "connected") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[12px] text-success">
        <CheckCircle2 size={12} />
        Connected
      </span>
    );
  }
  if (state === "connecting") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[12px] text-fg-muted">
        <Loader2 size={12} className="animate-spin" />
        Connecting…
      </span>
    );
  }
  return (
    <Button variant="secondary" size="sm" onClick={onClick}>
      {type === "google" ? <ExternalLink size={11} /> : <Plug size={11} />}
      Connect
    </Button>
  );
}
