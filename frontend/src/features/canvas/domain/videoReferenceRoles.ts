// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { VideoGenMode } from './canvasNodes';

export type VideoReferenceRole =
  | 'identity'
  | 'scene'
  | 'prop'
  | 'motion'
  | 'audio'
  | 'first_frame'
  | 'keyframe'
  | 'last_frame'
  | 'continuity'
  | 'style'
  | 'generic';

const ROLE_LABELS: Record<VideoReferenceRole, string> = {
  identity: '角色身份锚点',
  scene: '场景空间锚点',
  prop: '道具锚点',
  motion: '动作/运镜参考',
  audio: '声音/节奏参考',
  first_frame: '首帧约束',
  keyframe: '本镜状态关键画面',
  last_frame: '尾帧约束',
  continuity: '上一镜画面',
  style: '风格参考',
  generic: '通用参考',
};

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function clean(value: unknown, limit = 160): string {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

function roleFromText(value: unknown): VideoReferenceRole | undefined {
  const text = clean(value, 240).toLowerCase();
  if (!text) return undefined;
  if (text.includes('first_frame') || text.includes('first-frame') || text.includes('首帧')) return 'first_frame';
  if (text.includes('last_frame') || text.includes('last-frame') || text.includes('尾帧')) return 'last_frame';
  if (text.includes('continuity') || text.includes('承接')) return 'continuity';
  if (text.includes('identity') || text.includes('character') || text.includes('portrait') || text.includes('角色') || text.includes('人物') || text.includes('人像') || text.includes('身份')) return 'identity';
  if (text.includes('prop') || text.includes('道具') || text.includes('物品')) return 'prop';
  if (text.includes('scene') || text.includes('background') || text.includes('environment') || text.includes('场景') || text.includes('背景') || text.includes('环境') || text.includes('pano')) return 'scene';
  if (text.includes('motion') || text.includes('pose') || text.includes('camera') || text.includes('运镜') || text.includes('动作') || text.includes('姿态')) return 'motion';
  if (text.includes('audio') || text.includes('music') || text.includes('sound') || text.includes('音频') || text.includes('音乐') || text.includes('声音')) return 'audio';
  if (text.includes('style') || text.includes('风格') || text.includes('质感')) return 'style';
  return undefined;
}

/** Prefer explicit asset metadata; unlabeled nodes remain untyped. */
export function inferVideoReferenceRole(
  data: Record<string, unknown> | null | undefined,
): VideoReferenceRole | undefined {
  if (!data) return undefined;
  for (const value of [
    data.reference_role,
    data.referenceRole,
    data.output_role,
    data.role,
    data.nodeRole,
    data.assetKind,
    data.media_role,
    recordValue(data.__freezone_source)?.role,
  ]) {
    const role = roleFromText(value);
    if (role) return role;
  }
  if (Array.isArray(data.mainline_context)) {
    for (const context of data.mainline_context) {
      const role = roleFromText(recordValue(context)?.role);
      if (role) return role;
    }
  }
  return roleFromText(data.displayName);
}

export function videoReferenceRoleLabel(role: VideoReferenceRole | undefined): string {
  return ROLE_LABELS[role ?? 'generic'];
}

/**
 * 「上一镜承接」边（T-153）的参考素材角色。
 *
 * 同一条边在不同镜头下语义不同 —— 对**本镜**它是首帧，对**下一镜**它是上一镜的画面，
 * 所以角色只能从边推，不能写进分镜图节点的 data。这里只认承接边：别的边一律返回
 * `undefined`，保持各节点自己的 `inferVideoReferenceRole` 结论不变，不在本函数里
 * 顺手给首帧边升级语义。
 *
 * 判据取边角色（`scriptShotContinuity`）**或**边标签（「上一镜承接」）：前者是代码
 * 真相，后者是用户在画布上看到的那句话，任一对得上就认。
 */
export function referenceRoleFromContinuityEdge(
  edgeRole: unknown,
  edgeLabel?: unknown,
): VideoReferenceRole | undefined {
  const role = clean(edgeRole, 120).toLowerCase();
  if (role.includes('continuity')) return 'continuity';
  return roleFromText(edgeLabel) === 'continuity' ? 'continuity' : undefined;
}

/** Roles belong to the connection: the same image can serve different shots. */
export function referenceRoleFromVideoEdge(edgeRole: unknown, edgeLabel?: unknown): VideoReferenceRole | undefined {
  if (edgeRole === 'scriptShotVideo') return 'first_frame';
  if (edgeRole === 'scriptShotKeyframe') return 'keyframe';
  if (edgeRole === 'scriptShotAssetReference') return roleFromText(edgeLabel);
  return referenceRoleFromContinuityEdge(edgeRole, edgeLabel);
}

export interface VideoReferenceRoleEntry {
  kind: 'image' | 'video' | 'audio';
  role?: VideoReferenceRole;
  displayName?: string | null;
}

function typePrefix(kind: VideoReferenceRoleEntry['kind']): string {
  return kind === 'image' ? '图片' : kind === 'video' ? '视频' : '音频';
}

/** Preserve role meaning for providers whose request only accepts URL arrays.
 *
 * 输出是一句自然语言，不是台账：旧格式 `[参考素材角色]\n图片1=尾帧约束；…` 用的是
 * 方括号标题加等号记账，属于 `drama-skills/delivery-profile.md` 里禁止进入交付文本的
 * 「内部规则标记」。改成自然句后信息一条不少，但不再是一块可朗读的清单。
 */
export function renderVideoReferenceRoleLegend(
  entries: readonly VideoReferenceRoleEntry[],
  mode?: VideoGenMode,
): string {
  if (entries.length === 0) return '';
  const counters = { image: 0, video: 0, audio: 0 };
  const lines: string[] = [];
  entries.forEach((entry, index) => {
    counters[entry.kind] += 1;
    const role = entry.role ?? (
      mode === 'firstLastFrame' && entry.kind === 'image'
        ? index === 0 ? 'first_frame' : index === 1 ? 'last_frame' : undefined
        : undefined
    );
    if (!role) return;
    const name = clean(entry.displayName, 80);
    lines.push(
      `${typePrefix(entry.kind)}${counters[entry.kind]} 是${videoReferenceRoleLabel(role)}${name ? `（${name}）` : ''}`,
    );
  });
  return lines.length > 0 ? `\n参考素材中，${lines.join('，')}。` : '';
}

export { ROLE_LABELS };
