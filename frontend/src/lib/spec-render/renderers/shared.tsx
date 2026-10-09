"use client";

import type { Spec } from "../spec";
import type { CastEntry } from "../cast-list";

export function parseCastEntries(rawItems: Array<unknown>): CastEntry[] {
  return rawItems
    .map((item) => {
      if (typeof item === "string") return { text: item };
      if (item && typeof item === "object") {
        const obj = item as Record<string, unknown>;
        const text = typeof obj.text === "string" ? obj.text : "";
        if (!text) return null;
        return {
          text,
          costumeDescription:
            typeof obj.costumeDescription === "string"
              ? obj.costumeDescription
              : undefined,
        };
      }
      return null;
    })
    .filter((e): e is CastEntry => e !== null);
}

export type AlertData = {
  variant?: string;
  title?: string;
  message?: string;
};
export type BadgeData = { label: string; variant?: string };

export function collectAlerts(spec: Spec, childIds: string[]): AlertData[] {
  const alerts: AlertData[] = [];
  for (const id of childIds) {
    const el = spec.elements[id];
    if (el?.type === "Alert") {
      alerts.push({
        variant: el.props?.variant as string | undefined,
        title: el.props?.title as string | undefined,
        message: el.props?.message as string | undefined,
      });
    }
  }
  return alerts;
}

export function AlertList({
  alerts,
  className,
}: {
  alerts: AlertData[];
  className?: string;
}) {
  if (alerts.length === 0) return null;
  return (
    <div className={className ?? "space-y-2 mb-3"}>
      {alerts.map((a, i) => (
        <div
          key={i}
          className={`rounded-lg border px-4 py-3 ${
            a.variant === "warning"
              ? "border-amber-500/20 bg-amber-500/5"
              : "border-blue-500/20 bg-blue-500/5"
          }`}
        >
          {a.title && (
            <div
              className={`text-sm font-medium mb-1 ${
                a.variant === "warning"
                  ? "text-amber-600 dark:text-amber-300"
                  : "text-blue-600 dark:text-blue-300"
              }`}
            >
              {a.title}
            </div>
          )}
          {a.message && (
            <div className="text-sm text-muted-foreground">{a.message}</div>
          )}
        </div>
      ))}
    </div>
  );
}

export function BadgeList({ badges }: { badges: BadgeData[] }) {
  if (badges.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 mb-3">
      {badges.map((b, i) => (
        <span
          key={i}
          className={`rounded-full px-3 py-1 text-xs ${
            b.variant === "warning"
              ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
              : b.variant === "success"
                ? "bg-green-500/15 text-green-700 dark:text-green-300"
                : b.variant === "error"
                  ? "bg-red-500/15 text-red-700 dark:text-red-300"
                  : "bg-blue-500/15 text-blue-700 dark:text-blue-300"
          }`}
        >
          {b.label}
        </span>
      ))}
    </div>
  );
}

export function looksLikeEpisode(spec: Spec, cardId: string): boolean {
  const s = spec.elements[cardId];
  if (s?.type !== "Card") return false;
  return (s.children ?? []).some((gc) => {
    const g = spec.elements[gc];
    return g && /^(Stack|Alert|Badge|Heading|List|Separator)$/.test(g.type);
  });
}
