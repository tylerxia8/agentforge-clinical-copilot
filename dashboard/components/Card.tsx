import type { ReactNode } from "react";

interface CardProps {
  title: string;
  count?: number;
  children: ReactNode;
  /** When set, renders an alert chip on the title (e.g. high-criticality allergies). */
  badge?: { text: string; tone: "warning" | "danger" | "muted" };
  /** Compact (used by error/empty states) suppresses the bottom padding. */
  compact?: boolean;
}

const TONE = {
  warning: "bg-amber-50 text-clinical-warning border-amber-200",
  danger: "bg-red-50 text-clinical-danger border-red-200",
  muted: "bg-slate-50 text-clinical-muted border-clinical-border",
} as const;

export function Card({ title, count, children, badge, compact }: CardProps) {
  return (
    <section className="rounded-lg border border-clinical-border bg-clinical-surface shadow-sm">
      <header className="flex items-center justify-between border-b border-clinical-border px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-tight text-clinical-text">{title}</h2>
          {typeof count === "number" && (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-clinical-muted tabular-nums">
              {count}
            </span>
          )}
        </div>
        {badge && (
          <span className={`rounded border px-2 py-0.5 text-xs font-medium ${TONE[badge.tone]}`}>
            {badge.text}
          </span>
        )}
      </header>
      <div className={compact ? "px-4 py-2" : "px-4 py-3"}>{children}</div>
    </section>
  );
}

export function EmptyState({ message }: { message: string }) {
  return <p className="text-sm text-clinical-muted">{message}</p>;
}

export function ErrorState({ status, resource }: { status: number; resource: string }) {
  const friendly =
    status === 404
      ? "No records found."
      : status === 403
      ? "You do not have permission to view this section."
      : "Could not load this section. Try refreshing.";
  return (
    <div className="text-sm text-clinical-muted">
      {friendly}
      <span className="ml-2 text-xs text-clinical-muted/80 tabular-nums">
        ({resource} HTTP {status})
      </span>
    </div>
  );
}
