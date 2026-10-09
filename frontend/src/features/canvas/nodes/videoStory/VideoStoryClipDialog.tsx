// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from 'react';
import { Loader2, Scissors } from 'lucide-react';

/**
 * 「逐镜切片段」确认弹层。
 *
 * 与 {@link VideoStoryVideoDialog} 是同一张表上的另一条路，但可调项为零 —— 这是
 * 刻意的：切片段是纯 ffmpeg 的本地操作，没有模型可选、没有画幅可挑、也不花钱。
 * 唯一会影响结果的输入是**分镜表里的时间码**，而那正是用户在这张表里编辑的东西，
 * 在这里再放一份就成了两套口径。
 *
 * 所以这一层只做一件事：把账说清楚（要切几段、几行没时间码、共多少秒），
 * 让用户点下去之前知道会得到什么。
 */
export interface VideoStoryClipDialogProps {
  open: boolean;
  /** create：首次逐镜切片段；regenerate：已有这批节点，只补没切出来的。 */
  mode: 'create' | 'regenerate';
  /** 时间码能解析出合法区间的行数。 */
  clipCount: number;
  /** 本轮实际会提交去切的段数。 */
  pendingCount: number;
  /** regenerate 时行集合对不上 → 整批重建（旧片段节点会被删掉）。 */
  willRebuild?: boolean;
  /** 表里几行没有可解析的时间码（会被跳过）。 */
  skippedNoRange?: number;
  /** 本轮会切出的秒数合计。 */
  plannedSeconds?: number;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function VideoStoryClipDialog({
  open,
  mode,
  clipCount,
  pendingCount,
  willRebuild = false,
  skippedNoRange = 0,
  plannedSeconds = 0,
  busy = false,
  onCancel,
  onConfirm,
}: VideoStoryClipDialogProps) {
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
    skippedNoRange > 0 ? `另有 ${skippedNoRange} 行没有开始/结束时间码，会跳过。` : '';
  const secondsText = plannedSeconds > 0 ? `，共 ${plannedSeconds.toFixed(1)} 秒` : '';
  const description =
    mode === 'regenerate'
      ? willRebuild
        ? `分镜表已变化，将重建这批片段节点并按新时间码重切，共 ${clipCount} 段${secondsText}。`
        : pendingCount > 0
          ? `将重切 ${pendingCount} 段（没有片段或区间改过的那些），已切好的不动${secondsText}。${skippedNote}`
          : `所有片段都在，时间码也没变，无需重切。${skippedNote}`
      : `按分镜表的时间码把源视频切成 ${clipCount} 段${secondsText}，每段落成一个视频节点。` +
        `切出来的是原片那几秒的副本，画面与声音照旧，不经过任何模型、不产生费用。${skippedNote}`;

  return (
    <div
      className="fixed inset-0 z-[230] flex items-center justify-center bg-black/70 p-6"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex w-[min(420px,92vw)] flex-col gap-4 rounded-[14px] border border-white/[0.1] bg-[#1c1c1e]/98 p-4 text-text-dark shadow-[0_20px_60px_rgba(0,0,0,0.55)] backdrop-blur-xl">
        <header className="flex items-center gap-2">
          <Scissors className="h-4 w-4 text-text-muted" />
          <span className="text-[13px] font-medium">
            {mode === 'regenerate' ? '重新切片段' : '逐镜切片段'}
          </span>
        </header>

        <p className="text-[12px] leading-relaxed text-text-muted">{description}</p>

        <footer className="flex items-center justify-end gap-2">
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
            {mode === 'regenerate' ? '开始重切' : '逐镜切片段'}
          </button>
        </footer>
      </div>
    </div>
  );
}
