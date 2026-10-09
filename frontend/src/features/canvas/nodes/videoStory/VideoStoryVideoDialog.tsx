// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from 'react';
import { Loader2, Video } from 'lucide-react';

import { ProviderModelPicker } from '@/features/canvas/ui/ProviderModelPicker';
import { STORYBOARD_ASPECTS } from '@/features/canvas/domain/storyboardGroup';

/**
 * 「逐镜出视频」确认弹层。
 *
 * 与 {@link VideoStoryScatterDialog} 是同一件事的下一跳：那边把分镜行散成**图片**
 * 节点，这边把已经出好图的镜头散成**视频**节点（首帧＝那一镜的镜头图）。所以这里
 * 只有视频模型与画幅两个可调项 —— 时长由分镜表本身决定（见下），清晰度由视频节点
 * 按所选模型的契约自行收敛（`VideoNode` 的 `normalizeVideoQuality`），在这里再放一份
 * 就成了两套口径。
 *
 * 时长口径（与 `videoStoryShotVideos.ts` 同源）：有开始/结束时间码的行按该镜时长出片，
 * 没有时间码的行落到 5 秒兜底 —— 所以这里只做说明，不做一个会和表打架的滑块。
 */
export interface VideoStoryVideoDialogProps {
  open: boolean;
  /** create：首次逐镜出视频；regenerate：已有这批视频节点，重跑未落地的。 */
  mode: 'create' | 'regenerate';
  /** 会落成视频节点的镜数（已出图且有运动提示词）。 */
  shotCount: number;
  /** 本轮实际会提交出片的条数。 */
  pendingCount: number;
  /** regenerate 时行集合对不上 → 整批重建（旧视频节点会被删掉）。 */
  willRebuild?: boolean;
  /** 还没有镜头图、会被跳过的行数。 */
  skippedNoImage?: number;
  /** 连运动提示词都拼不出来的行数。 */
  skippedNoPrompt?: number;
  model: string;
  aspectKey: string;
  busy?: boolean;
  /** 已格式化的预估点数（拿不到账单规则时为 null）。 */
  priceDisplay?: string | null;
  onModelChange: (next: string) => void;
  onAspectChange: (next: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  /** 仅建节点（不出视频）。 */
  onCreateOnly?: () => void;
}

export function VideoStoryVideoDialog({
  open,
  mode,
  shotCount,
  pendingCount,
  willRebuild = false,
  skippedNoImage = 0,
  skippedNoPrompt = 0,
  model,
  aspectKey,
  busy = false,
  priceDisplay = null,
  onModelChange,
  onAspectChange,
  onCancel,
  onConfirm,
  onCreateOnly,
}: VideoStoryVideoDialogProps) {
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

  const skippedNotes = [
    skippedNoImage > 0 ? `${skippedNoImage} 行还没有镜头图（出图后才能出视频）` : '',
    skippedNoPrompt > 0 ? `${skippedNoPrompt} 行没有运动提示词` : '',
  ].filter(Boolean);
  const skippedNote = skippedNotes.length > 0 ? `另有 ${skippedNotes.join('、')}，会跳过。` : '';
  const description =
    mode === 'regenerate'
      ? willRebuild
        ? `分镜表已变化，将重建这批视频节点并按 ${aspectKey} 逐镜出视频，共 ${shotCount} 条。`
        : pendingCount > 0
          ? `将对未出片或失败的 ${pendingCount} 条重新生成，已出的视频不动。${skippedNote}`
          : `所有镜头视频都已出片，无需重新生成。${skippedNote}`
      : `按 ${shotCount} 个镜头散出视频节点，首帧取该镜的镜头图，` +
        `提示词取该行的视频运动提示词，时长按该行的时间码（没有时间码按 5 秒）。${skippedNote}`;

  return (
    <div
      className="fixed inset-0 z-[230] flex items-center justify-center bg-black/70 p-6"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex w-[min(420px,92vw)] flex-col gap-4 rounded-[14px] border border-white/[0.1] bg-[#1c1c1e]/98 p-4 text-text-dark shadow-[0_20px_60px_rgba(0,0,0,0.55)] backdrop-blur-xl">
        <header className="flex items-center gap-2">
          <Video className="h-4 w-4 text-text-muted" />
          <span className="text-[13px] font-medium">
            {mode === 'regenerate' ? '重新生成镜头视频' : '逐镜出视频'}
          </span>
        </header>

        <p className="text-[12px] leading-relaxed text-text-muted">{description}</p>

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
              disabled={busy || pendingCount === 0 || model.trim().length === 0}
              title={model.trim().length === 0 ? '先选一个视频模型' : undefined}
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
