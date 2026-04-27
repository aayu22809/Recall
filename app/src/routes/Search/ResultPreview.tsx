import { ExternalLink, Folder } from "lucide-react";

import type { SearchResult } from "../../lib/daemon";
import { Button } from "../../components/Button";
import { invoke } from "../../lib/ipc";
import { sourceLabel, sourceTint } from "../../lib/theme";

interface Props {
  result: SearchResult;
}

export function ResultPreview({ result }: Props) {
  const meta = (result.metadata ?? {}) as Record<string, unknown>;
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-5 py-4">
        <div className="flex items-center gap-2">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: sourceTint(result.source) }}
          />
          <span className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">
            {sourceLabel(result.source)}
          </span>
          <span className="ml-auto font-mono text-[10px] text-fg-dim">
            {result.timestamp.replace(/T.*/, "")}
          </span>
        </div>
        <h3 className="mt-2 break-words text-[16px] font-medium leading-snug text-fg">
          {result.file_name || "(untitled)"}
        </h3>
        <div className="mt-1 break-all font-mono text-[10px] text-fg-dim">
          {result.file_path}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {result.description && (
          <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-fg-muted">
            {result.description}
          </p>
        )}
        {Object.keys(meta).length > 0 && (
          <div className="mt-6">
            <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-fg-dim">
              metadata
            </div>
            <dl className="grid grid-cols-[80px_1fr] gap-x-3 gap-y-1.5 font-mono text-[11px]">
              {Object.entries(meta)
                .filter(([k]) => !k.startsWith("_"))
                .slice(0, 12)
                .map(([k, v]) => (
                  <Row key={k} k={k} v={v} />
                ))}
            </dl>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-border px-5 py-3">
        <Button
          variant="primary"
          size="sm"
          onClick={() => invoke("open_path", { path: result.file_path })}
        >
          <ExternalLink size={12} />
          Open
        </Button>
        {result.source === "files" && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => invoke("reveal_in_finder", { path: result.file_path })}
          >
            <Folder size={12} />
            Reveal in Finder
          </Button>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: unknown }) {
  return (
    <>
      <dt className="text-fg-dim">{k}</dt>
      <dd className="break-all text-fg">
        {typeof v === "string" ? v : JSON.stringify(v)}
      </dd>
    </>
  );
}
