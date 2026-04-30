import clsx from "clsx";

interface Props {
  keys: (string | number)[];
  className?: string;
}

export function KeyHint({ keys, className }: Props) {
  return (
    <span className={clsx("inline-flex items-center gap-0.5 font-mono text-[10px]", className)}>
      {keys.map((k, i) => (
        <kbd
          key={i}
          className="rounded-[3px] border border-border bg-bg px-1 py-px text-fg-muted"
        >
          {k}
        </kbd>
      ))}
    </span>
  );
}
