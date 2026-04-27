import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowRight, Check, KeyRound, Sparkles, Folder, Plug, Loader2 } from "lucide-react";
import clsx from "clsx";

import { Button } from "../../components/Button";
import { ProviderStep } from "./ProviderStep";
import { FolderStep } from "./FolderStep";
import { ConnectorStep } from "./ConnectorStep";
import { SyncStep } from "./SyncStep";

interface Props {
  onComplete: () => void | Promise<void>;
}

const STEPS = [
  { id: "welcome", title: "Welcome", icon: Sparkles },
  { id: "provider", title: "Embedding provider", icon: KeyRound },
  { id: "folder", title: "Watch a folder", icon: Folder },
  { id: "connect", title: "Connect sources", icon: Plug },
  { id: "sync", title: "First sync", icon: Loader2 },
] as const;

export function Onboarding({ onComplete }: Props) {
  const [stepIdx, setStepIdx] = useState(0);
  const step = STEPS[stepIdx];

  function next() {
    if (stepIdx === STEPS.length - 1) {
      void onComplete();
    } else {
      setStepIdx((i) => i + 1);
    }
  }

  function back() {
    setStepIdx((i) => Math.max(0, i - 1));
  }

  return (
    <div className="flex h-full bg-bg">
      <aside className="flex w-[280px] flex-col border-r border-border bg-bg-panel p-6">
        <div className="mb-8">
          <div className="font-mono text-[11px] uppercase tracking-widest text-fg-dim">
            recall · setup
          </div>
          <div className="mt-2 text-[15px] font-medium text-fg">
            Five steps. No terminal.
          </div>
        </div>
        <ol className="flex flex-col gap-1">
          {STEPS.map((s, i) => {
            const Icon = s.icon;
            const done = i < stepIdx;
            const active = i === stepIdx;
            return (
              <li
                key={s.id}
                className={clsx(
                  "flex items-center gap-2.5 rounded-panel px-2.5 py-1.5 text-[13px]",
                  active
                    ? "bg-bg-hover text-fg"
                    : done
                      ? "text-fg-muted"
                      : "text-fg-dim",
                )}
              >
                <span
                  className={clsx(
                    "flex h-5 w-5 items-center justify-center rounded-full border text-[10px] font-mono",
                    done
                      ? "border-success/40 bg-success/10 text-success"
                      : active
                        ? "border-accent/40 bg-accent-soft text-accent"
                        : "border-border text-fg-dim",
                  )}
                >
                  {done ? <Check size={11} /> : <Icon size={11} />}
                </span>
                {s.title}
              </li>
            );
          })}
        </ol>
        <div className="mt-auto text-[11px] text-fg-dim">
          You can change every choice later in Settings.
        </div>
      </aside>
      <main className="flex flex-1 flex-col">
        <AnimatePresence mode="wait">
          <motion.div
            key={step.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2 }}
            className="flex-1 overflow-y-auto px-12 py-12"
          >
            {step.id === "welcome" && <WelcomeStep />}
            {step.id === "provider" && <ProviderStep onAdvance={next} />}
            {step.id === "folder" && <FolderStep onAdvance={next} />}
            {step.id === "connect" && <ConnectorStep onAdvance={next} />}
            {step.id === "sync" && <SyncStep onAdvance={next} />}
          </motion.div>
        </AnimatePresence>
        <footer className="flex items-center justify-between border-t border-border px-12 py-4">
          <Button
            variant="ghost"
            onClick={back}
            disabled={stepIdx === 0}
          >
            Back
          </Button>
          <div className="flex items-center gap-3">
            {step.id === "connect" && (
              <Button variant="ghost" onClick={next}>
                Skip
              </Button>
            )}
            {step.id === "welcome" && (
              <Button variant="primary" onClick={next}>
                Continue
                <ArrowRight size={14} />
              </Button>
            )}
          </div>
        </footer>
      </main>
    </div>
  );
}

function WelcomeStep() {
  return (
    <div className="max-w-[520px]">
      <h1 className="text-[28px] font-semibold leading-tight tracking-tight">
        One search box for your files,
        <br />
        email, calendar, and notes.
      </h1>
      <p className="mt-4 text-[14px] leading-relaxed text-fg-muted">
        Recall runs entirely on your Mac. Your index is a folder on disk. The
        only outbound calls are to your chosen embedding provider — or zero,
        if you run Ollama locally.
      </p>
      <ul className="mt-8 flex flex-col gap-3 text-[13px] text-fg-muted">
        <li className="flex items-start gap-2">
          <Check size={14} className="mt-0.5 text-success" />
          <span>Indexes Gmail, Calendar, Drive, Notion, Canvas, Schoology, cal.ai.</span>
        </li>
        <li className="flex items-start gap-2">
          <Check size={14} className="mt-0.5 text-success" />
          <span>Sub-200 ms semantic search across every source.</span>
        </li>
        <li className="flex items-start gap-2">
          <Check size={14} className="mt-0.5 text-success" />
          <span>Zero terminal, zero Cloud Console clicks.</span>
        </li>
      </ul>
      <div className="mt-10 inline-flex items-center gap-2 rounded-panel border border-border bg-bg-panel px-3 py-2 text-[12px] text-fg-muted">
        <Sparkles size={12} className="text-accent" />
        Press <kbd className="rounded-[3px] border border-border bg-bg px-1 py-px font-mono text-[10px]">⌥Space</kbd>
        anywhere on macOS for instant search.
      </div>
    </div>
  );
}
