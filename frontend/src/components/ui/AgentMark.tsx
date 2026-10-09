// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Sprout } from "lucide-react";

import { cn } from "@/lib/utils";

export function AgentMark({
  className,
  size = "md",
  withWordmark = false,
}: {
  className?: string;
  size?: "sm" | "md" | "lg" | "xl" | "launcher";
  withWordmark?: boolean;
}) {
  const sizeClass = {
    sm: "size-6",
    md: "size-7",
    lg: "size-9",
    xl: "size-8",
    launcher: "size-14",
  }[size];
  const iconClass = {
    sm: "size-3",
    md: "size-3.5",
    lg: "size-4",
    xl: "size-4",
    launcher: "size-6",
  }[size];
  const mark = (
    <span
      className={cn(
        "agent-mark relative inline-flex shrink-0 items-center justify-center rounded-full border border-white/[0.14] bg-[radial-gradient(circle_at_35%_28%,rgba(255,255,255,0.2),transparent_38%),linear-gradient(145deg,#292936,#0d0d12)] text-white/90 shadow-[0_8px_20px_rgba(0,0,0,0.28),inset_0_1px_0_rgba(255,255,255,0.16)]",
        sizeClass,
        className,
      )}
      aria-hidden
      data-agent-mark="bot"
    >
      <Sprout className={cn("relative z-10", iconClass)} strokeWidth={1.8} />
    </span>
  );
  if (!withWordmark) return mark;

  const chip =
    size === "lg"
      ? "h-9 gap-2 px-2.5"
      : size === "sm"
        ? "h-7 gap-1.5 px-1.5"
        : "h-8 gap-1.5 px-2";
  return (
    <span
      className={cn(
        "relative inline-flex shrink-0 items-center overflow-hidden rounded-full border border-white/10 bg-[#0b0b0c]/95 shadow-[0_0_0_1px_rgba(255,255,255,0.05)]",
        chip,
        className,
      )}
      aria-hidden
      data-agent-mark="bot-chip"
    >
      {mark}
      <span className="pr-0.5 text-[11px] font-semibold tracking-[-0.01em] text-white/85">
        小树
      </span>
    </span>
  );
}
