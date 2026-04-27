import { useEffect, useState } from "react";
import clsx from "clsx";
import { CheckCircle2, Cloud, Cpu, KeyRound, Loader2 } from "lucide-react";

import { Button } from "../../components/Button";
import { invoke } from "../../lib/ipc";

type Provider = "ollama" | "gemini";

export function ProviderStep({ onAdvance }: { onAdvance: () => void }) {
  const [provider, setProvider] = useState<Provider>("ollama");
  const [geminiKey, setGeminiKey] = useState("");
  const [ollama, setOllama] = useState<{ installed: boolean; models: string[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    invoke<{ installed: boolean; models: string[] }>("ollama_detect").then(setOllama);
  }, []);

  async function save() {
    try {
      setSaving(true);
      setError(null);
      if (provider === "ollama") {
        if (!ollama?.installed) throw new Error("Install Ollama first.");
        const preferredModel =
          ollama.models.find((model) => model.includes("nomic-embed-text")) ??
          ollama.models[0] ??
          "nomic-embed-text";
        await invoke("configure", {
          payload: {
            vef_embedding_provider: "ollama",
            vef_ollama_base_url: "http://127.0.0.1:11434",
            vef_ollama_embed_model: preferredModel,
          },
        });
      } else {
        if (!geminiKey.trim()) throw new Error("Paste a Gemini key.");
        await invoke("keychain_set", {
          account: "gemini.api_key",
          secret: geminiKey.trim(),
        });
        await invoke("configure", {
          payload: {
            gemini_api_key: "keychain://gemini.api_key",
            vef_embedding_provider: "gemini",
            vef_embedding_model: "gemini-embedding-2-preview",
            vef_embedding_dimensions: 768,
          },
        });
      }
      onAdvance();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-[640px]">
      <h2 className="text-[22px] font-semibold tracking-tight">
        Pick an embedding provider
      </h2>
      <p className="mt-2 text-[13px] text-fg-muted">
        Embeddings turn text into vectors. Choose a local model (no data leaves
        your Mac) or Gemini's free tier for higher accuracy.
      </p>

      <div className="mt-8 grid grid-cols-2 gap-3">
        <ProviderCard
          active={provider === "ollama"}
          onClick={() => setProvider("ollama")}
          icon={<Cpu size={16} />}
          title="Ollama"
          desc="100% local · no key required"
          status={
            ollama === null
              ? <span className="font-mono text-[10px] text-fg-dim">checking…</span>
              : ollama.installed
                ? <span className="inline-flex items-center gap-1 text-success">
                    <CheckCircle2 size={11} />
                    <span className="font-mono text-[10px]">running on :11434</span>
                  </span>
                : <span className="font-mono text-[10px] text-warn">not detected</span>
          }
        />
        <ProviderCard
          active={provider === "gemini"}
          onClick={() => setProvider("gemini")}
          icon={<Cloud size={16} />}
          title="Gemini"
          desc="Free tier · 768-dim · fastest"
          status={
            <span className="font-mono text-[10px] text-fg-dim">
              api key required
            </span>
          }
        />
      </div>

      {provider === "gemini" && (
        <label className="mt-6 block">
          <div className="mb-1.5 flex items-center gap-1.5 text-[12px] text-fg-muted">
            <KeyRound size={11} />
            Gemini API key
          </div>
          <input
            type="password"
            value={geminiKey}
            onChange={(e) => setGeminiKey(e.target.value)}
            placeholder="AIza…"
            className={clsx(
              "h-9 w-full rounded-panel border border-border bg-bg-input px-3 font-mono text-[12px] text-fg outline-none transition-colors placeholder:text-fg-dim",
              "focus:border-accent/60",
            )}
          />
          <a
            href="https://aistudio.google.com/apikey"
            target="_blank"
            rel="noreferrer"
            className="mt-1.5 inline-block text-[11px] text-fg-muted underline-offset-2 hover:text-accent hover:underline"
          >
            Get a free key from aistudio.google.com →
          </a>
        </label>
      )}

      {provider === "ollama" && !ollama?.installed && (
        <div className="mt-6 rounded-panel border border-border bg-bg-panel p-4 text-[12px]">
          <div className="text-fg">Ollama not detected.</div>
          <div className="mt-1 text-fg-muted">
            Install with <code className="rounded-[3px] bg-bg px-1.5 py-0.5 font-mono">brew install ollama</code>{" "}
            then run <code className="rounded-[3px] bg-bg px-1.5 py-0.5 font-mono">ollama pull nomic-embed-text</code>.
          </div>
          <div className="mt-3 flex items-center gap-2 text-[11px] text-fg-dim">
            <Loader2 size={11} className="animate-spin" />
            We'll keep checking in the background.
          </div>
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-panel border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger">
          {error}
        </div>
      )}

      <div className="mt-8">
        <Button variant="primary" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save & continue"}
        </Button>
      </div>
    </div>
  );
}

function ProviderCard({
  active,
  onClick,
  icon,
  title,
  desc,
  status,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  title: string;
  desc: string;
  status: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "flex flex-col items-start gap-1.5 rounded-panel border p-4 text-left transition-colors",
        active
          ? "border-accent/60 bg-accent-soft"
          : "border-border bg-bg-panel hover:border-border-strong",
      )}
    >
      <div className="flex items-center gap-2 text-fg">
        {icon}
        <span className="text-[14px] font-medium">{title}</span>
      </div>
      <div className="text-[12px] text-fg-muted">{desc}</div>
      <div className="mt-2">{status}</div>
    </button>
  );
}
