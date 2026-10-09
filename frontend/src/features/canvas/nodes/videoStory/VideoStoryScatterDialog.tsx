// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from 'react';
import { LayoutGrid, Loader2 } from 'lucide-react';

import { DirectModelPicker } from '@/features/canvas/ui/DirectModelPicker';
import { STORYBOARD_ASPECTS } from '@/features/canvas/domain/storyboardGroup';

/**
 * 「散开到画布」确认弹层。
 *
 * 与脚本节点的 {@link ScriptStoryboardDialog} 是同一件事的两套皮：那边的事实来源是
 * 人写的脚本行，这边是拉片解析出来的分镜表，所以措辞按「镜头」而不是「分镜图」。
 * 只有一处结构差异 —— regenerate 模式下若分镜行**整批换过**（行标识集合对不上），
 * 动作会把旧节点整批删掉重排，这时「仅建节点」也有意义（先把新节点摆出来看提示词），
 * 所以它在 rebuild 分支里同样出现。
 *
 * 主按钮是真出图那条路（建完节点由节点自身提交）；次按钮只摆节点不出图。
 */
export interface VideoStoryScatterDialogProps {
  open: boolean;
  /** create：首次散开；regenerate：已有这批节点，重跑未落地的。 */
  mode: 'create' | 'regenerate';
  /** 会落成节点的镜数。 */
  shotCount: number;
  /** 本轮实际会出图的张数。 */
  pendingCount: number;
  /** regenerate 时行集合对不上 → 整组重建（旧节点会被删掉）。 */
  willRebuild?: boolean;
  /** 表里缺少画面提示词、会被跳过的行数。 */
  skippedCount?: number;
  groupLabel: string;
  model: string;
  aspectKey: string;
  busy?: boolean;
  /** 已格式化的预估点数（拿不到账单规则时为 null）。 */
  priceDisplay?: string | null;
  onModelChange: (next: string) => void;
  onAspectChange: (next: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  /** 仅建节点（不出图）。 */
  onCreateOnly?: () => void;
}

export function VideoStoryScatterDialog({
  open,
  mode,
  shotCount,
  pendingCount,
  willRebuild = false,
  skippedCount = 0,
  groupLabel,
  model,
  aspectKey,
  busy = false,
  priceDisplay = null,
  onModelChange,
  onAspectChange,
  onCancel,
  onConfirm,
  onCreateOnly,
}: VideoStoryScatterDialogProps) {
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

  const skippedNote =
    skippedCount > 0 ? `另有 ${skippedCount} 行没有画面提示词，会跳过。` : '';
  const description =
    mode === 'regenerate'
      ? willRebuild
        ? `分镜表已变化，将重建「${groupLabel}」并按 ${aspectKey} 逐镜出图，共 ${shotCount} 张。`
        : pendingCount > 0
          ? `将对未出图或失败的 ${pendingCount} 张镜头图重新生成，已出的图不动。${skippedNote}`
          : `所有镜头图都已出图，无需重新生成。${skippedNote}`
      : `按 ${shotCount} 个镜头在视频故事节点右侧散出「${groupLabel}」，每镜一张图，` +
        `参考图取该镜关键帧。${skippedNote}`;

  return (
    <div
      className="fixed inset-0 z-[230] flex items-center justify-center bg-black/70 p-6"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex w-[min(420px,92vw)] flex-col gap-4 rounded-[14px] border border-white/[0.1] bg-[#1c1c1e]/98 p-4 text-text-dark shadow-[0_20px_60px_rgba(0,0,0,0.55)] backdrop-blur-xl">
        <header className="flex items-center gap-2">
          <LayoutGrid className="h-4 w-4 text-text-muted" />
          <span className="text-[13px] font-medium">
            {mode === 'regenerate' ? '重新生成镜头图' : '散开到画布'}
          </span>
        </header>

        <p className="text-[12px] leading-relaxed text-text-muted">{description}</p>

        <div className="flex flex-col gap-3 rounded-[10px] bg-white/[0.03] p-3">
          <label className="flex items-center justify-between gap-3">
            <span className="shrink-0 text-[12px] text-text-muted">图片模型</span>
            <DirectModelPicker
              kind="image"
              value={model}
              onChange={onModelChange}
              ariaLabel="镜头图模型"
              className="!h-7"
            />
          </label>

          <div className="flex items-center justify-between gap-3">
            <span className="shrink-0 text-[12px] text-text-muted">比例</span>
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
              disabled={busy || pendingCount === 0}
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {mode === 'regenerate' ? '开始生成' : '散开出图'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
