import clsx from "clsx";
import type { ReactNode } from "react";

interface Props {
  title: string;
  hint?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, hint, action, className }: Props) {
  return (
    <div
      className={clsx(
        "flex h-full min-h-[200px] flex-col items-center justify-center gap-2 px-6 py-10 text-center text-fg-muted",
        className,
      )}
    >
      <div className="text-[14px] text-fg">{title}</div>
      {hint && <div className="text-[12px] text-fg-muted">{hint}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
