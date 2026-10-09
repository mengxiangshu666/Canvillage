// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Clock, Film, Ruler, TriangleAlert, UserRound } from 'lucide-react';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import {
  computeScriptShotRhythm,
  formatTotalDuration,
  summarizeScriptStats,
  SCRIPT_SHOT_MAX_DURATION_SECONDS,
  type ScriptStats,
} from './scriptStats';

/**
 * 脚本表读数条。
 *
 * 表头此前只有「共 N 个分镜」（而且只在全屏弹层里）—— 用户看不到这份脚本总共多长、
 * 景别是不是单调、有哪几镜还缺提示词或缺角色图。这一条把这些数就地摆出来，
 * 缺项用琥珀色并带 tooltip 说清是「哪几个角色」，而不是只在开拍时才炸。
 *
 * 排版克制：一行、等宽数字、靠间距分组；有问题才出现琥珀色，没问题不占视觉。
 */

export interface ScriptStatsBarProps {
  stats: ScriptStats;
  rows: readonly FreezoneStoryScriptRow[];
  /** 行内联版（节点内）比全屏版更紧。 */
  compact?: boolean;
  /**
   * 会**真的拦住出图**的镜号（缺图片提示词那几镜）。给了就在末尾补一枚琥珀 chip。
   *
   * 与「N 项待补」刻意分开：待补里混着「缺运动提示词」这类不影响本轮出图的项，
   * 合成一句会让用户以为点了「生成分镜」什么都没发生。这一项是真的什么都不发生 ——
   * 图片节点建出来会按「请先填写提示词」拒绝提交、一直挂着。
   */
  blockedShotNumbers?: readonly string[];
}

const CHIP_CLASS =
  'inline-flex items-center gap-1 whitespace-nowrap text-[11px] leading-4 text-text-muted';

export function ScriptStatsBar({
  stats,
  rows,
  compact = false,
  blockedShotNumbers = [],
}: ScriptStatsBarProps) {
  if (stats.shotCount === 0) return null;

  const shotSizeText = stats.shotSizeTop.map((bucket) => bucket.label).join(' / ');
  // 开拍前自查两条（`libtv §DISTILL/06_AGENT_BEHAVIOR_SPEC.md §4.1` 的硬规则表里
  // 「景别跳档」与「首镜人物必须在场」）：都是逐镜之间才看得出来的事，
  // 摆在景别分布旁边。规则为什么不是按语料字面的「跳两档」实现，见 `computeScriptShotRhythm`。
  const rhythm = computeScriptShotRhythm(rows);
  const rhythmIssueCount = rhythm.issues.length;
  const issueCount =
    stats.missingImagePromptCount +
    stats.missingMotionPromptCount +
    stats.characterWithoutImageCount +
    // 超时长的行也算「待补」：它不拦住出片，但出出来比表里写的短 —— 得有人去拆镜。
    stats.overlongDurationCount;
  // 整条的读屏文案 / 悬浮摘要：此前 `summarizeScriptStats` 写好了却只有自己的单测在读，
  // 视觉上每个 chip 各自带 title，读屏用户却听不到一句完整的话。
  const summary = [
    summarizeScriptStats(stats, rows),
    blockedShotNumbers.length > 0 ? `${blockedShotNumbers.length} 镜缺图片提示词、无法出图` : '',
    rhythmIssueCount > 0 ? `${rhythmIssueCount} 处相邻镜景别没换（取景会一样）` : '',
    rhythm.firstShotWithoutCharacter ? '首镜没有人物' : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div
      className={`flex items-center gap-x-3 gap-y-1 overflow-hidden border-b border-[rgba(255,255,255,0.06)] px-2.5 text-text-muted ${
        compact ? 'flex-nowrap py-1' : 'flex-wrap py-1.5'
      }`}
      aria-label={summary}
      title={summary}
    >
      <span className={CHIP_CLASS}>
        <Film className="h-3.5 w-3.5 shrink-0" />
        <span className="tabular-nums">{stats.shotCount}</span> 镜
      </span>

      <span className={CHIP_CLASS} title="各行时长之和；解析不出时长的行不计入">
        <Clock className="h-3.5 w-3.5 shrink-0" />
        <span className="tabular-nums">{formatTotalDuration(stats.totalDurationSec)}</span>
        {stats.unknownDurationCount > 0 && (
          <span className="text-amber-200/90">
            （{stats.unknownDurationCount} 镜无时长）
          </span>
        )}
        {/* 长镜头提示核对具体模型，不在派生时裁切导演时长。 */}
        {stats.overlongDurationCount > 0 && (
          <span
            className="text-amber-200/90"
            title={`${stats.overlongDurationCount} 镜超过 ${SCRIPT_SHOT_MAX_DURATION_SECONDS}s，生成前请核对所选模型的时长支持；不支持时可拆镜或换模型`}
          >
            （{stats.overlongDurationCount} 镜超 {SCRIPT_SHOT_MAX_DURATION_SECONDS}s）
          </span>
        )}
      </span>

      {shotSizeText && (
        <span
          className={`${CHIP_CLASS} min-w-0`}
          title={[
            ...stats.shotSizeTop.map((bucket) => `${bucket.label} ${bucket.count}`),
            stats.shotSizeOtherCount > 0 ? `其它 ${stats.shotSizeOtherCount}` : '',
            stats.shotSizeEmptyCount > 0 ? `未填 ${stats.shotSizeEmptyCount}` : '',
          ]
            .filter(Boolean)
            .join(' · ')}
        >
          <span className="truncate">
            景别 <span className="text-text-dark/85">{shotSizeText}</span>
          </span>
        </span>
      )}

      {/* 景别节奏：连着两镜取景一样的那几处。只在有问题时出现，说清**是哪两镜** ——
          只说「节奏平」用户没法改。 */}
      {rhythmIssueCount > 0 && (
        <span
          className={`${CHIP_CLASS} !text-amber-200/90`}
          title={`相邻两镜景别相同，切出来取景一样（libtv 分镜表要求相邻镜换档）：${rhythm.issues
            .slice(0, 6)
            .map((issue) => `第 ${issue.previousShotNumber}→${issue.shotNumber} 镜都是${issue.shotSize}`)
            .join('、')}${rhythmIssueCount > 6 ? ` 等 ${rhythmIssueCount} 处` : ''}`}
        >
          <Ruler className="h-3.5 w-3.5 shrink-0" />
          <span className="tabular-nums">{rhythmIssueCount}</span> 处景别没换
        </span>
      )}

      {/* 首镜自查：libtv 的硬规则「首镜人物必须在场」。只在整份脚本有角色时才报 ——
          全片空镜 / 风光的话首镜没人是对的。 */}
      {rhythm.firstShotWithoutCharacter && (
        <span
          className={`${CHIP_CLASS} !text-amber-200/90`}
          title="首镜（第 1 行）里没有填角色，但后面几镜有角色 —— 分镜表硬规则要求首镜人物在场"
        >
          <UserRound className="h-3.5 w-3.5 shrink-0" />
          首镜没有人物
        </span>
      )}

      {issueCount > 0 && (
        <span
          className={`${CHIP_CLASS} !text-amber-200/90`}
          title={[
            stats.missingImagePromptCount > 0
              ? `${stats.missingImagePromptCount} 镜缺图片提示词（分镜提示词与画面描述都空）`
              : '',
            stats.missingMotionPromptCount > 0
              ? `${stats.missingMotionPromptCount} 镜缺运动提示词`
              : '',
            stats.overlongDurationCount > 0
              ? `${stats.overlongDurationCount} 镜超过 ${SCRIPT_SHOT_MAX_DURATION_SECONDS}s，需核对模型支持`
              : '',
            stats.characterWithoutImageCount > 0
              ? `${stats.characterWithoutImageCount} 镜的角色没有角色图${
                  stats.missingCharacterNames.length > 0
                    ? `：${stats.missingCharacterNames.join('、')}`
                    : ''
                }`
              : '',
          ]
            .filter(Boolean)
            .join('；')}
        >
          <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
          <span className="tabular-nums">{issueCount}</span> 项待补
        </span>
      )}

      {/* 这一枚与「待补」并列而不是并进它：它是**硬阻塞**（出不了图），
          待补里还有不影响本轮出图的项。两者同一处显示才能一眼看出差别。 */}
      {blockedShotNumbers.length > 0 && (
        <span
          className={`${CHIP_CLASS} !text-red-300/85`}
          title={`${blockedShotNumbers
            .map((no) => `第 ${no} 镜`)
            .join('、')} 缺图片提示词，生成分镜图时不会出图`}
        >
          <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
          <span className="tabular-nums">{blockedShotNumbers.length}</span> 镜不能出图
        </span>
      )}

      {!compact && rows.length > 0 && (
        <span className="ml-auto text-[11px] text-text-muted/70">
          首尾镜号 {String(rows[0]?.shot_no ?? 1)} – {String(rows[rows.length - 1]?.shot_no ?? rows.length)}
        </span>
      )}
    </div>
  );
}
