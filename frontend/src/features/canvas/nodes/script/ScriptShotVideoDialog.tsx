// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from 'react';
import { Loader2, TriangleAlert, Video } from 'lucide-react';

import { ProviderModelPicker } from '@/features/canvas/ui/ProviderModelPicker';
import { STORYBOARD_ASPECTS } from '@/features/canvas/domain/storyboardGroup';
import type { ScriptPaidActionGate } from './scriptPaidActionGate';
import {
  type ScriptShotAuditIssue,
  type ScriptShotAuditRow,
} from './scriptShotVideos';

/**
 * 「逐镜出视频」确认弹层（脚本节点那条路）。
 *
 * 与「视频故事 → 逐镜出视频」的 {@link VideoStoryVideoDialog} 是同一件事的两个入口：
 * 那边的事实来源是**拉片解析**出来的镜头表，这边是**脚本节点自己**的分镜行。所以两个
 * 弹层的可调项一致（视频模型 + 画幅），时长口径也一致（由表决定，不另给滑块）。
 *
 * 比那边多一句话：**提示词兜底计数**。脚本的后端提示词一直在产
 * `video_motion_prompt`，但老脚本 / 手改过的表可能没有 —— 那些行会退回画面提示词出片，
 * 运动感会弱。件数如实写在弹层上，不静默吞掉（LibTV 的「生成前自查清单」就是同一件事：
 * 开跑前把账摊开，见 `libtv §DISTILL/06_AGENT_BEHAVIOR_SPEC.md §4.1`）。
 */
export interface ScriptShotVideoDialogProps {
  open: boolean;
  /** create：首次逐镜出视频；regenerate：已有这批视频节点，重跑未落地的。 */
  mode: 'create' | 'regenerate';
  /** 会落成视频节点的镜数（已出图且有提示词）。 */
  shotCount: number;
  /** 本轮实际会提交出片的条数。 */
  pendingCount: number;
  /** regenerate 时行集合对不上 → 整批重建（旧视频节点会被删掉）。 */
  willRebuild?: boolean;
  /** 还没有分镜图、会被跳过的行数。 */
  skippedNoImage?: number;
  /** 连提示词都拼不出来的行数。 */
  skippedNoPrompt?: number;
  /** 用画面提示词兜底（没有视频运动提示词）的行数。 */
  fallbackPromptCount?: number;
  /** 会接上「上一镜承接」参考的镜数（同场景相邻镜）。 */
  continuityCount?: number;
  /** 所选模型能不能吃下第二张图；false 时弹层如实说明这批是各拍各的单图首帧。 */
  continuitySupported?: boolean;
  /** 逐镜体检表：每一镜会被自动填进去的出片事实（首帧、运镜、提示词来源、音轨）。 */
  rows?: readonly ScriptShotAuditRow[];
  model: string;
  aspectKey: string;
  busy?: boolean;
  /** 已格式化的预估点数（拿不到账单规则时为 null）。 */
  priceDisplay?: string | null;
  /** 真正出片前的当前门禁；仅建节点不受它影响。 */
  paidActionGate?: ScriptPaidActionGate | null;
  onModelChange: (next: string) => void;
  onAspectChange: (next: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  /** 仅建节点（不出视频）。 */
  onCreateOnly?: () => void;
}

const AUDIT_ISSUE_LABELS: Record<ScriptShotAuditIssue, string> = {
  noImage: '缺首帧',
  noPrompt: '缺提示词',
  noCamera: '未识别摄影安排',
  cameraNeedsReview: '摄影指令需确认',
  missingLastFrame: '本镜首尾帧尚未准备齐全或引用已过期',
  durationMismatch: '时长不一致',
  invalidDuration: '缺少明确时长',
  modelUnsupported: '所选模型不支持本镜生成方式、时长或声音方案',
};

const AUDIT_AUDIO_LABELS: Record<ScriptShotAuditRow['audioRoute'], string> = {
  native: '原生声音',
  external: '外部配音',
  silent: '静音',
};

/**
 * 逐镜体检表：出片前把这批镜头**会被自动填进去的事实**摊开（首帧、运镜、提示词来源、
 * 音轨、缺项），让审核变成扫一眼而不是逐个点开节点。
 *
 * 缺项不是错误：没有分镜图的镜这一批就是进不去，表里如实标出来比按钮点完再报更早一步
 * （对齐 `libtv §DISTILL/06_AGENT_BEHAVIOR_SPEC.md §4.1` 的生成前自查清单）。
 */
function ShotAuditTable({ rows }: { rows: readonly ScriptShotAuditRow[] }) {
  const issueRowCount = rows.filter((row) => row.issues.length > 0).length;
  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[12px] text-text-muted">逐镜核对</span>
        <span className="text-[11px] text-text-muted/80">
          {issueRowCount > 0
            ? `共 ${rows.length} 镜 · ${issueRowCount} 镜需核对`
            : `共 ${rows.length} 镜 · 无缺项`}
        </span>
      </div>
      <div className="max-h-[236px] overflow-auto rounded-[10px] border border-white/[0.08]">
        <table className="w-full min-w-[640px] table-fixed border-collapse text-left text-[11px]">
          <thead className="sticky top-0 z-10 bg-[#232326] text-text-muted">
            <tr>
              <th className="w-10 px-2 py-1.5 font-normal">镜号</th>
              <th className="w-24 px-2 py-1.5 font-normal">取用 / 生成</th>
              <th className="w-20 px-2 py-1.5 font-normal">首帧</th>
              <th className="px-2 py-1.5 font-normal">摄影安排</th>
              <th className="w-16 px-2 py-1.5 font-normal">提示词</th>
              <th className="w-24 px-2 py-1.5 font-normal">音轨</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.rowKey} className="border-t border-white/[0.06] align-middle">
                <td className="px-2 py-1 text-text-dark/90">{row.shotNumber}</td>
                <td className="px-2 py-1 text-text-muted">
                  {row.durationSec === null ? '—' : `${row.durationSec}s / ${row.generationDurationSec == null ? '不可用' : `${row.generationDurationSec}s`}`}
                </td>
                <td className="px-2 py-1">
                  {row.firstFrameUrl ? (
                    <img
                      src={row.firstFrameUrl}
                      alt=""
                      loading="lazy"
                      className="h-8 w-14 rounded-[4px] object-cover"
                    />
                  ) : (
                    <span className="block h-8 w-14 rounded-[4px] border border-dashed border-white/15" />
                  )}
                </td>
                <td className="px-2 py-1">
                  <span
                    className={`block truncate ${row.camera ? 'text-text-dark/85' : 'text-amber-200/80'}`}
                    title={row.camera || '未读到明确的摄影安排，请核对运动稿；固定机位也可以是有效安排'}
                  >
                    {row.camera || '—'}
                  </span>
                  {row.issues.length > 0 && (
                    <span className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-amber-200/80">
                      {row.issues.map((issue) => (
                        <span key={issue}>{AUDIT_ISSUE_LABELS[issue]}</span>
                      ))}
                    </span>
                  )}
                </td>
                <td className="px-2 py-1 text-text-muted">
                  {row.promptSource === 'motion' ? '运动稿' : '画面词'}
                </td>
                <td className="px-2 py-1">
                  <span className="block text-text-dark/85">{AUDIT_AUDIO_LABELS[row.audioRoute]}</span>
                  {row.dialogue && (
                    <span className="block truncate text-[10px] text-text-muted" title={row.dialogue}>
                      台词：{row.dialogue}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ScriptShotVideoDialog({
  open,
  mode,
  shotCount,
  pendingCount,
  willRebuild = false,
  skippedNoImage = 0,
  skippedNoPrompt = 0,
  fallbackPromptCount = 0,
  continuityCount = 0,
  continuitySupported = false,
  rows = [],
  model,
  aspectKey,
  busy = false,
  priceDisplay = null,
  paidActionGate = null,
  onModelChange,
  onAspectChange,
  onCancel,
  onConfirm,
  onCreateOnly,
}: ScriptShotVideoDialogProps) {
  // 弹层挂在 body 上（portal），键盘事件不冒泡到节点容器，所以自己监听 Esc。
  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onCancel]);

  if (!open) return null;

  const paidAllowed = paidActionGate?.allowed ?? true;
  const paidBlockedReason = paidActionGate?.reason ?? null;
  const skippedNotes = [
    skippedNoImage > 0 ? `${skippedNoImage} 行还没有分镜图（出图后才能出视频）` : '',
    skippedNoPrompt > 0 ? `${skippedNoPrompt} 行没有提示词` : '',
  ].filter(Boolean);
  const skippedNote = skippedNotes.length > 0 ? `另有 ${skippedNotes.join('、')}，会跳过。` : '';
  const fallbackNote =
    fallbackPromptCount > 0
      ? `${fallbackPromptCount} 行没有视频运动提示词，会用画面提示词出片（运动感会弱一些）。`
      : '';
  // 承接边（T-153）：同场景的相邻镜会额外参考上一镜的画面，让镜头之间接得上。
  // 不接的原因有两类，都要说清楚，不能让人以为是坏了：模型吃不下第二张图，
  // 或者这批镜头本来就都是硬切 / 换场景（那本来就不该接）。
  const continuityNote = !continuitySupported
    ? '当前模型不支持多图参考，这批镜头会各自用一张分镜图出片（镜头之间不会互相参考画面）。'
    : continuityCount > 0
      ? `${continuityCount} 个镜头会参考上一镜画面；已有当前视频的唯一截尾帧时优先使用，否则用分镜首帧。动作接点仍需核对。`
      : '这批没有可用的上一镜分镜参考，按各镜自己的首帧生成。';
  const description =
    mode === 'regenerate'
      ? willRebuild
        ? `分镜行已变化，将重建这批视频节点并按 ${aspectKey} 逐镜出视频，共 ${shotCount} 条。`
        : pendingCount > 0
          ? `将对未出片或失败的 ${pendingCount} 条重新生成，已出的视频不动。${skippedNote}`
          : `所有镜头视频都已出片，无需重新生成。${skippedNote}`
      : `按 ${shotCount} 个镜头散出视频节点，首帧取该镜的分镜图，` +
        `按所选模型生成足够长的素材，取用时长保留剧本节奏。${skippedNote}`;

  return (
    <div
      className="fixed inset-0 z-[230] flex items-center justify-center bg-black/70 p-6"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex w-[min(760px,94vw)] flex-col gap-4 rounded-[14px] border border-white/[0.1] bg-[#1c1c1e]/98 p-4 text-text-dark shadow-[0_20px_60px_rgba(0,0,0,0.55)] backdrop-blur-xl">
        <header className="flex items-center gap-2">
          <Video className="h-4 w-4 text-text-muted" />
          <span className="text-[13px] font-medium">
            {mode === 'regenerate' ? '重新生成镜头视频' : '逐镜出视频'}
          </span>
        </header>

        <p className="text-[12px] leading-relaxed text-text-muted">{description}</p>
        {paidBlockedReason && (
          <div
            role="alert"
            className="flex items-start gap-1.5 rounded-[10px] border border-red-400/25 bg-red-500/10 p-2.5 text-[12px] leading-relaxed text-red-100/85"
          >
            <TriangleAlert className="h-3.5 w-3.5 shrink-0 translate-y-0.5 text-red-300" />
            <span className="min-w-0 flex-1">{paidBlockedReason}</span>
          </div>
        )}
        {fallbackNote && (
          <p className="text-[12px] leading-relaxed text-amber-200/85">{fallbackNote}</p>
        )}
        <p className="text-[12px] leading-relaxed text-text-muted">{continuityNote}</p>

        {rows.length > 0 && <ShotAuditTable rows={rows} />}

        <div className="flex flex-col gap-3 rounded-[10px] bg-white/[0.03] p-3">
          <label className="flex items-center justify-between gap-3">
            <span className="shrink-0 text-[12px] text-text-muted">视频模型</span>
            <ProviderModelPicker
              domain="video"
              selectedModelId={model}
              onChange={onModelChange}
              className="!h-7"
            />
          </label>

          <div className="flex items-center justify-between gap-3">
            <span className="shrink-0 text-[12px] text-text-muted">画幅</span>
            <div className="flex flex-wrap justify-end gap-1">
              {STORYBOARD_ASPECTS.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  aria-pressed={option.key === aspectKey}
                  onClick={() => onAspectChange(option.key)}
                  className={`h-7 rounded-[8px] px-2 text-[12px] transition-colors ${
                    option.key === aspectKey
                      ? 'bg-white/[0.14] text-text-dark'
                      : 'text-text-muted hover:bg-white/[0.06] hover:text-text-dark'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <footer className="flex items-center justify-between gap-2">
          <span className="text-[12px] text-text-muted">
            {pendingCount > 0 && priceDisplay ? `预计消耗 ${priceDisplay}` : ''}
          </span>
          <div className="flex items-center gap-2">
            {onCreateOnly && (mode === 'create' || willRebuild) && (
              <button
                type="button"
                className="h-7 rounded-[8px] px-2.5 text-[12px] text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text-dark disabled:opacity-50"
                onClick={onCreateOnly}
                disabled={busy}
              >
                仅建节点
              </button>
            )}
            <button
              type="button"
              className="h-7 rounded-[8px] px-2.5 text-[12px] text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text-dark"
              onClick={onCancel}
              disabled={busy}
            >
              取消
            </button>
            <button
              type="button"
              className="inline-flex h-7 items-center gap-1.5 rounded-[8px] bg-white px-3 text-[12px] font-medium text-bg-dark transition-colors hover:bg-white/90 disabled:opacity-50"
              onClick={onConfirm}
              disabled={
                busy || pendingCount === 0 || model.trim().length === 0 || !paidAllowed || rows.some(row => row.issues.includes('missingLastFrame') || row.issues.includes('invalidDuration') || row.issues.includes('modelUnsupported'))
              }
              title={
                rows.some(row => row.issues.includes('modelUnsupported'))
                  ? '所选模型不支持当前时长或声音方案，请调整脚本或换模型'
                  : rows.some(row => row.issues.includes('invalidDuration'))
                  ? '先填写明确的正数秒时长，也可以先仅建草稿节点'
                  : rows.some(row => row.issues.includes('missingLastFrame'))
                  ? '推荐首尾帧的镜头尚未具备尾帧；可先仅建节点，再补齐尾帧单独生成'
                  : paidBlockedReason
                  ? paidBlockedReason
                  : model.trim().length === 0
                    ? '先选一个视频模型'
                    : undefined
              }
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {mode === 'regenerate' ? '开始生成' : '逐镜出视频'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
