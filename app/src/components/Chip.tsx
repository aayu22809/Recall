import clsx from "clsx";
import type { ButtonHTMLAttributes } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
  tint?: string;
}

export function Chip({ active, tint, className, children, ...rest }: Props) {
  return (
    <button
      className={clsx(
        "inline-flex h-6 items-center gap-1.5 rounded-chip border px-2 text-[11px] font-medium transition-colors",
        active
          ? "border-accent/40 bg-accent-soft text-fg"
          : "border-border bg-transparent text-fg-muted hover:border-border-strong hover:text-fg",
        className,
      )}
      {...rest}
    >
      {tint && (
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: tint }}
        />
      )}
      {children}
    </button>
  );
}
