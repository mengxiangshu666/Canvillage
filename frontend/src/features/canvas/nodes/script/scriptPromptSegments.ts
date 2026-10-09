// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 脚本节点的段级只读展示入口。
 *
 * 实现已移到 `features/canvas/domain/promptSegments.ts` —— 运镜写入
 * （`domain/promptCamera.ts`）与这里的只读展示必须是同一套切段口径，否则同一条提示词
 * 在两处会切出不同的段。本文件保留原来的导出名，脚本节点的既有引用不用改。
 */

import { splitShotPromptSegments, isFrozenShotSegment } from '@/features/canvas/domain/promptSegments';

export function scriptRewriteFrozenFacts(rows: readonly { shot_prompt?: string | null }[], targetIndex: number | null): string[] {
  const style = splitShotPromptSegments(rows[0]?.shot_prompt).filter(segment => segment.startsWith('视觉风格'));
  const cast = splitShotPromptSegments(targetIndex === null ? '' : rows[targetIndex]?.shot_prompt)
    .filter(segment => isFrozenShotSegment(segment) && !segment.startsWith('视觉风格'));
  return [...cast, ...style];
}

export {
  MOTION_SEGMENT_PREFIXES,
  SHOT_SEGMENT_PREFIXES,
  hasEightSegments,
  isFrozenShotSegment,
  shotPromptSegment,
  splitPromptSegments,
  splitShotPromptSegments,
} from '@/features/canvas/domain/promptSegments';
