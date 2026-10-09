// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 请求时长 vs 产物真实时长的节点角标判定（分辨率/比例 honesty 角标的同族）。
 *
 * 两边都必须是真实数字才给结论：缺一边返回 null、角标不渲染——宁可不显示，
 * 也不把「节点当前的配置值」冒充成「那次生成的请求值」。
 */

import { formatDurationSecondsShort } from "./canvasResourceMeta";

/** 与任务运行器的 durationCheck 同一容差：±0.5s 内视为一致。 */
export const DURATION_MATCH_TOLERANCE_SECONDS = 0.5;

export interface DurationHonestyInput {
  /** 提交时冻结的请求时长（秒）；缺失或非法则不给结论。 */
  requestedSeconds?: number | null;
  /** 产物实测时长（秒），优先取后端 ffprobe 的 resourceMeta.durationSec。 */
  actualSeconds?: number | null;
  toleranceSeconds?: number;
}

export interface DurationHonestyResult {
  requestedSeconds: number;
  actualSeconds: number;
  isMismatch: boolean;
  /** 紧凑角标文案，例如 "30s → 15.05s"。 */
  badgeLabel: string;
  /** 悬停全文。 */
  tooltip: string;
}

export function evaluateDurationHonesty(
  input: DurationHonestyInput,
): DurationHonestyResult | null {
  const requested = Number(input.requestedSeconds);
  const actual = Number(input.actualSeconds);
  if (!Number.isFinite(requested) || requested <= 0) return null;
  if (!Number.isFinite(actual) || actual <= 0) return null;

  const tolerance =
    typeof input.toleranceSeconds === "number" &&
    Number.isFinite(input.toleranceSeconds) &&
    input.toleranceSeconds >= 0
      ? input.toleranceSeconds
      : DURATION_MATCH_TOLERANCE_SECONDS;

  const isMismatch = Math.abs(actual - requested) > tolerance;
  const requestedLabel = formatDurationSecondsShort(requested);
  const actualLabel = formatDurationSecondsShort(actual);

  const tooltip = [
    `请求时长：${requestedLabel}`,
    `实际时长：${actualLabel}`,
    isMismatch
      ? `相差 ${formatDurationSecondsShort(Math.abs(actual - requested))}`
      : `在 ±${tolerance}s 容差内`,
    "说明：实际时长以产物容器实测为准；不一致时结果已如实记录，可携任务号向渠道方举证。",
  ].join("\n");

  return {
    requestedSeconds: requested,
    actualSeconds: actual,
    isMismatch,
    badgeLabel: isMismatch ? `${requestedLabel} → ${actualLabel}` : actualLabel,
    tooltip,
  };
}
