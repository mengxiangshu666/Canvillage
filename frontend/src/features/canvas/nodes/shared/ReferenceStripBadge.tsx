// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  referenceChipNumberLabel,
  referenceDurationBadge,
  type ReferenceMediaKind,
} from "./referenceStripBadges";

/**
 * 引用缩略图左上角的序号角标。
 *
 * 序号就是同类型 1-based 计数，与提交给后端的 `@图片N` 同一个编号：用户看到的
 * 1 号，就是他能在提示词里引用的 1 号。此前缩略图上不显示序号，「提示词里的
 * @图片3 是哪一张」只能靠数。
 *
 * 一行里混了多种类型时（`kind` + `mixedKinds` 同时给出）数字前加一个单字类型前缀，
 * 免得三个按类型各数各的角标全写「1」。理由与不采用统一连续序号的取舍见
 * `./referenceStripBadges.ts` 顶部注释。
 *
 * 视频 / 音频 / 图片三种 chip 共用同一个组件，保证角标位置与字号完全一致。
 */
export function ReferenceChipNumberBadge({
  typeIndex,
  kind,
  mixedKinds,
}: {
  typeIndex: number;
  kind?: ReferenceMediaKind;
  mixedKinds?: boolean;
}) {
  const label = referenceChipNumberLabel(typeIndex, kind, mixedKinds);
  if (!label) return null;
  return (
    <span
      className="pointer-events-none absolute left-1 top-1 z-10 flex h-4 min-w-4 items-center justify-center rounded-[4px] bg-black/65 px-1 text-[9px] font-semibold leading-none text-white"
      style={{ textShadow: "0 1px 1px rgba(0,0,0,0.6)" }}
    >
      {label}
    </span>
  );
}

/**
 * 引用缩略图左下角的时长角标。只有视频 / 音频有；图片没有时长，组件直接不渲染。
 */
export function ReferenceChipDurationBadge({
  kind,
  durationMs,
}: {
  kind: ReferenceMediaKind;
  durationMs?: number | null;
}) {
  const label = referenceDurationBadge(kind, durationMs);
  if (!label) return null;
  return (
    <span
      className="pointer-events-none absolute bottom-1 left-1 z-10 rounded-[4px] bg-black/65 px-1 text-[9px] font-medium leading-[14px] text-white"
      style={{ textShadow: "0 1px 1px rgba(0,0,0,0.6)" }}
    >
      {label}
    </span>
  );
}
