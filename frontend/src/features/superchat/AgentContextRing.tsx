// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { cn } from "@/lib/utils";

import type { AgentContextUsage } from "./agent-context-usage";

type AgentContextRingProps = {
  usage: AgentContextUsage;
};

export function AgentContextRing({ usage }: AgentContextRingProps) {
  const value = usage.available ? usage.remainingPercent : 0;

  return (
    <span
      className={cn(
        "village-agent-context-ring",
        `village-agent-context-ring--${usage.tone}`,
        !usage.available && "village-agent-context-ring--unknown",
      )}
      role={usage.available ? "progressbar" : "status"}
      aria-label={`${usage.label}。${usage.detail}`}
      title={`${usage.label} · ${usage.detail}`}
      {...(usage.available
        ? {
            "aria-valuemin": 0,
            "aria-valuemax": 100,
            "aria-valuenow": usage.remainingPercent,
            "aria-valuetext": usage.label,
          }
        : {})}
    >
      <svg
        className="village-agent-context-ring__svg"
        viewBox="0 0 12 12"
        aria-hidden="true"
      >
        <circle
          className="village-agent-context-ring__track"
          cx="6"
          cy="6"
          r="5.25"
          pathLength="100"
        />
        <circle
          className="village-agent-context-ring__value"
          cx="6"
          cy="6"
          r="5.25"
          pathLength="100"
          strokeDasharray="100 100"
          strokeDashoffset={100 - value}
          transform="rotate(-90 6 6)"
        />
      </svg>
    </span>
  );
}
