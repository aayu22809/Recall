import { useState } from "react";
import clsx from "clsx";
import { ExternalLink, KeyRound, Server, ShieldCheck } from "lucide-react";

import { Button } from "../../components/Button";
import { invoke } from "../../lib/ipc";

const TABS = [
  { id: "embeddings", label: "Embeddings", icon: KeyRound },
  { id: "daemon", label: "Daemon", icon: Server },
  { id: "privacy", label: "Privacy", icon: ShieldCheck },
] as const;

type Tab = (typeof TABS)[number]["id"];

export function Settings() {
  const [tab, setTab] = useState<Tab>("embeddings");

  return (
    <div className="flex h-full">
      <aside className="w-[180px] shrink-0 border-r border-border bg-bg-panel p-3">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={clsx(
                "flex w-full items-center gap-2.5 rounded-panel px-2.5 py-1.5 text-left text-[13px] transition-colors",
                active
                  ? "bg-bg-hover text-fg"
                  : "text-fg-muted hover:bg-bg-hover hover:text-fg",
              )}
            >
              <Icon size={13} className={active ? "text-accent" : ""} />
              {t.label}
            </button>
          );
        })}
      </aside>
      <main className="flex-1 overflow-y-auto px-8 py-8">
        {tab === "embeddings" && <EmbeddingsTab />}
        {tab === "daemon" && <DaemonTab />}
        {tab === "privacy" && <PrivacyTab />}
      </main>
    </div>
  );
}

function EmbeddingsTab() {
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(false);
  return (
    <section>
      <h3 className="text-[16px] font-semibold tracking-tight">Embeddings</h3>
      <p className="mt-1 text-[12px] text-fg-muted">
        The provider Recall uses to turn text into vectors.
      </p>

      <div className="mt-6 max-w-[480px] space-y-4">
        <Field
          label="Gemini API key"
          input={
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="AIza…"
              className={inputCls}
            />
          }
          hint={
            <a
              href="https://aistudio.google.com/apikey"
              className="text-fg-muted underline-offset-2 hover:text-accent hover:underline"
            >
              Get a free key
            </a>
          }
        />
        <div className="flex items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={!key.trim()}
            onClick={async () => {
              await invoke("keychain_set", {
                account: "gemini.api_key",
                secret: key.trim(),
              });
              await invoke("configure", {
                payload: {
                  gemini_api_key: "keychain://gemini.api_key",
                  vef_embedding_provider: "gemini",
                },
              });
              setSaved(true);
              setKey("");
            }}
          >
            Save
          </Button>
          {saved && (
            <span className="font-mono text-[11px] text-success">saved · daemon reloaded</span>
          )}
        </div>
      </div>
    </section>
  );
}

function DaemonTab() {
  return (
    <section>
      <h3 className="text-[16px] font-semibold tracking-tight">Daemon</h3>
      <p className="mt-1 text-[12px] text-fg-muted">
        Process settings. Most users never need to change these.
      </p>
      <div className="mt-6 max-w-[480px] space-y-3 font-mono text-[12px]">
        <KV k="port" v="auto-negotiated · 19847+" />
        <KV k="logs" v="~/.vef/daemon.log (rotated)" />
        <KV k="data" v="~/.vef/chromadb/" />
        <div className="pt-2">
          <Button variant="secondary" size="sm" onClick={() => invoke("open_log_dir")}>
            <ExternalLink size={11} />
            Open ~/.vef in Finder
          </Button>
        </div>
      </div>
    </section>
  );
}

function PrivacyTab() {
  return (
    <section>
      <h3 className="text-[16px] font-semibold tracking-tight">Privacy</h3>
      <div className="mt-6 max-w-[480px] space-y-3 text-[12.5px] leading-relaxed text-fg-muted">
        <p>
          Recall does not transmit any indexed content to Anthropic, the
          Recall team, or any third party except the embedding provider you
          chose.
        </p>
        <p>
          Your index lives entirely on disk at{" "}
          <code className="rounded-[3px] bg-bg-panel px-1 font-mono text-[11px]">~/.vef/chromadb/</code>.
          Delete that folder to wipe your index.
        </p>
        <p>
          Crash reports are off by default. Enabling them sends only stack
          traces — never document content.
        </p>
      </div>
    </section>
  );
}

const inputCls =
  "h-8 w-full rounded-panel border border-border bg-bg-input px-2.5 font-mono text-[12px] text-fg outline-none transition-colors placeholder:text-fg-dim focus:border-accent/60";

function Field({
  label,
  input,
  hint,
}: {
  label: string;
  input: React.ReactNode;
  hint?: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1.5 text-[11px] text-fg-muted">{label}</div>
      {input}
      {hint && <div className="mt-1.5 text-[10px]">{hint}</div>}
    </label>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline gap-3">
      <span className="w-16 text-fg-dim">{k}</span>
      <span className="text-fg">{v}</span>
    </div>
  );
}
