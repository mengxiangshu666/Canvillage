// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import { buildScriptRowKeys } from './scriptViews';
import { scriptRowFingerprint } from './scriptRowFingerprint';
import { SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD, scriptTailReferenceStaleReason, type CanvasGraphSlice } from './scriptShotVideos';
import { scriptFieldSequence } from './scriptFields';
import {
  maxOccupiedCharacterSlots,
  rowCharacters,
  rowReferenceImageUrl,
} from './scriptViews';

/**
 * 分镜表导出 CSV。
 *
 * 照搬 LibTV 脚本节点的导出实现（`scriptRowsToCsvContent`）：
 * - 角色列按各行实际占用槽位的最大值动态展开（LibTV 按 `characters[]` 最大长度）；
 * - 角色三连列（角色 / 角色描述 / 角色图）插在「画面描述」之后，其余列保持原顺序；
 * - 含 `,` `"` 换行 的单元格用双引号包裹并把 `"` 转义成 `""`；
 * - 「参考」列取参考帧图 URL（LibTV 的 `videoReference.referenceFrameImage`）。
 */

function escapeCsvCell(value: string): string {
  if (
    value.includes(',') ||
    value.includes('"') ||
    value.includes('\n') ||
    value.includes('\r')
  ) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function fieldValue(row: FreezoneStoryScriptRow, key: string): string {
  if (key === 'reference') {
    return rowReferenceImageUrl(row) ?? '';
  }
  const raw = row[key];
  if (typeof raw === 'string') return raw;
  if (typeof raw === 'number') return String(raw);
  return '';
}

function characterSlotValue(
  row: FreezoneStoryScriptRow,
  slot: number,
  kind: 'name' | 'description' | 'image',
): string {
  const character = rowCharacters(row).find((entry) => entry.slot === slot);
  if (!character) return '';
  if (kind === 'name') return character.name;
  if (kind === 'description') return character.description;
  return character.imageUrl ?? '';
}

/** 生成 CSV 文本（含 UTF-8 BOM，Excel 打开中文不乱码）。 */
export function buildScriptCsv(rows: FreezoneStoryScriptRow[], context?: {
  directorPlan?: FreezoneStoryDirectorPlan | null;
  videos?: CanvasNode[];
  directorPlanPending?: boolean;
  graph?: CanvasGraphSlice;
}): string {
  const slotCount = Math.max(1, maxOccupiedCharacterSlots(rows));
  const fields = scriptFieldSequence(slotCount);
  const characterKeys = new Set<string>();
  for (let slot = 1; slot <= slotCount; slot += 1) {
    characterKeys.add(`character_${slot}`);
    characterKeys.add(`character_description_${slot}`);
    characterKeys.add(`character_image_${slot}`);
  }

  const handoffHeaders = ['片序', '镜头身份', '显示镜号', '所属段落', '视频节点', '视频状态', '视频地址'];
  const header = [...fields.map((field) => field.label), ...handoffHeaders].map(escapeCsvCell).join(',');
  const rowKeys = buildScriptRowKeys(rows);
  const body = rows.map((row, index) => {
    const cells = fields.map((field) => {
      if (characterKeys.has(field.key)) {
        const slot = Number(field.key.replace(/^character(?:_description|_image)?_/, ''));
        if (!Number.isFinite(slot)) return '';
        const kind = field.key.startsWith('character_description_')
          ? 'description'
          : field.key.startsWith('character_image_')
            ? 'image'
            : 'name';
        return escapeCsvCell(characterSlotValue(row, slot, kind));
      }
      return escapeCsvCell(fieldValue(row, field.key));
    });
    const videos = (context?.videos ?? []).filter((node) => readScriptShotId(node.data) === rowKeys[index]);
    const video = videos[0];
    const url = typeof video?.data?.videoUrl === 'string' ? video.data.videoUrl.trim() : '';
    const snapshot = video?.data?.[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD];
    const status = context?.directorPlanPending ? '导演规划未同步'
      : videos.length > 1 ? '存在重复视频，请核对'
      : video?.data?.generationError ? '生成失败'
      : !url ? '未出片'
      : typeof snapshot === 'string' && snapshot !== scriptRowFingerprint(row, index) ? '脚本已改，视频过期'
      : video && context?.graph && scriptTailReferenceStaleReason(video, context.graph) ? '尾帧参考已变化，视频过期'
      : typeof snapshot !== 'string' ? '已有视频，内容对应未核验'
      : '内容对应当前脚本，观感未验';
    const usableUrl = status === '已有视频，内容对应未核验' || status === '内容对应当前脚本，观感未验' ? url : '';
    const sequences = (context?.directorPlan?.sequences ?? [])
      .filter((sequence) => sequence.shot_nos?.includes(Number(row.shot_no)))
      .map((sequence) => [sequence.sequence_id, sequence.title].filter(Boolean).join(' ')).join('；');
    cells.push(...[
      String(index + 1), rowKeys[index] ?? '', String(row.display_shot_no ?? row.shot_no ?? ''),
      sequences, videos.map((node) => node.id).join('；'), status, usableUrl,
    ].map(escapeCsvCell));
    return cells.join(',');
  });

  // \r\n 是 RFC 4180 行尾；Excel / 表格软件通用。
  return `\uFEFF${[header, ...body].join('\r\n')}\r\n`;
}

/** 导出文件名：`<标题>-分镜脚本.csv`，标题里的非法字符换成下划线。 */
export function buildScriptCsvFileName(title?: string | null): string {
  const base = (title ?? '').trim() || '分镜脚本';
  const safe = base.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80);
  return `${safe}-分镜脚本.csv`;
}

/** 浏览器端触发下载（节点内「下载」按钮用）。 */
export function downloadScriptCsv(rows: FreezoneStoryScriptRow[], title?: string | null, context?: Parameters<typeof buildScriptCsv>[1]): void {
  const blob = new Blob([buildScriptCsv(rows, context)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = buildScriptCsvFileName(title);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // 交给浏览器接管后再释放，避免 Safari / 部分 Chromium 版本取消下载。
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
