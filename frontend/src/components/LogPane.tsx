import { useEffect, useRef } from "react";

/** Monospace log view that sticks to the bottom unless the user scrolled up. */
export default function LogPane({ lines, empty }: { lines: string[]; empty: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <pre
      ref={ref}
      className="logs"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
    >
      {lines.length ? lines.join("\n") : <span className="muted">{empty}</span>}
    </pre>
  );
}
