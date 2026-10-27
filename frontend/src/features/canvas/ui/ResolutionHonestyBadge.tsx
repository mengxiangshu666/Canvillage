// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { LucideIcon } from "lucide-react";
import { Image as ImageIcon, Video as VideoIcon } from "lucide-react";

import {
  evaluateResolutionHonesty,
  type ResolutionHonestyInput,
} from "@/features/canvas/domain/resolutionHonesty";

// 默认浮在节点右上角；节点标题栏要用 rightSlot 放按钮时改成 inline，
// 让角标和按钮排在同一行——两者都锚 right-1 会互相盖住（角标 z-20 压住按钮）。
const BADGE_FLOATING_POSITION_CLASS = "absolute -top-7 right-1";
const BADGE_INLINE_POSITION_CLASS = "relative";

const BADGE_BASE_CLASS =
  "z-20 flex max-w-[min(100%,220px)] items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium tabular-nums backdrop-blur-sm";

const BADGE_OK_CLASS =
  "border-white/10 bg-black/55 text-white/70";

const BADGE_MISMATCH_CLASS =
  "border-amber-400/35 bg-amber-950/70 text-amber-100/90";

export interface ResolutionHonestyBadgeProps {
  media: ResolutionHonestyInput["media"];
  requestedTier: string | null | undefined;
  actualWidth: number | null | undefined;
  actualHeight: number | null | undefined;
  /** Optional override icon; defaults by media. */
  icon?: LucideIcon;
  /**
   * 放在标题栏 rightSlot 里时置 true：不再绝对定位到节点右上角，
   * 而是跟着标题行排版，避免压住同一角落的按钮。
   */
  inline?: boolean;
  className?: string;
}

/**
 * Top-right node chip: requested tier · actual pixels.
 * Amber when the returned long edge is meaningfully off the requested tier.
 */
export function ResolutionHonestyBadge({
  media,
  requestedTier,
  actualWidth,
  actualHeight,
  icon,
  inline = false,
  className,
}: ResolutionHonestyBadgeProps) {
  const result = evaluateResolutionHonesty({
    media,
    requestedTier,
    actualWidth,
    actualHeight,
  });
  if (!result) return null;

  const Icon =
    icon ??
    (media === "image" ? ImageIcon : VideoIcon);

  return (
    <div
      className={`${
        inline ? BADGE_INLINE_POSITION_CLASS : BADGE_FLOATING_POSITION_CLASS
      } ${BADGE_BASE_CLASS} ${
        result.isMismatch ? BADGE_MISMATCH_CLASS : BADGE_OK_CLASS
      } ${className ?? ""}`}
      title={result.tooltip}
      role="status"
      aria-label={result.tooltip.replace(/\n/g, " · ")}
    >
      <Icon
        className={`h-3 w-3 shrink-0 ${
          result.isMismatch ? "text-amber-200/80" : "text-white/45"
        }`}
      />
      <span className="truncate">{result.badgeLabel}</span>
      {result.isMismatch ? (
        <span className="shrink-0 text-[10px] font-semibold text-amber-200/90">
          偏
        </span>
      ) : null}
    </div>
  );
}
