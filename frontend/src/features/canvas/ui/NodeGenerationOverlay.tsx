// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useState } from 'react';
import { Loader2, Square } from 'lucide-react';

type NodeGenerationOverlayProps = {
  /**
   * 生成开始时间戳。**只用于「已用时长」**——后端尚未回报进度时，节点显示的是
   * 真实的已用时间，不是本地计时器编出来的百分比。为空时从挂载时刻开始计时。
   */
  startedAt?: number | null;
  /** 后端任务的真实进度（0..1）。null / undefined 表示后端还没报，进入不确定态。 */
  progress?: number | null;
  /** @deprecated 仅保留兼容旧调用，加载态不再绘制背景遮罩。 */
  hasBackground?: boolean;
  /** 圆角,默认跟随节点圆角变量。 */
  rounded?: string;
  /** Optional node-local stop action. Hidden for overlays without a cancellable task. */
  onCancel?: () => void;
  cancelPending?: boolean;
};

/**
 * 后端进度 → 显示用整数百分比，**后端没报进度就返回 null**。
 *
 * 这里刻意不做「按预估时长推进」的回退。那个估算值取自硬编码的预期耗时
 * （改前散落在 `nodeRegistry` 的 `generationDurationMs: 60000`、模型档案的
 * `expectedDurationMs` 与几处 overlay 里写死的常量），与真实模型耗时无关，
 * 却和真进度共用同一套视觉 —— 用户无法区分「后端说 87%」和「前端自己编的
 * 87%」，任何一次耗时异常都会被读成「卡在 87%」。宁可显示「已用 12s」，
 * 也不给一个看起来像进度的假数字。
 *
 * `0` 是**合法进度**（任务刚建、runner 还没起步），必须与 `null` 区分开。
 */
export function resolveGenerationProgress(
  progress: number | null | undefined,
): number | null {
  if (typeof progress !== 'number' || !Number.isFinite(progress)) return null;
  return Math.round(Math.min(1, Math.max(0, progress)) * 100);
}

/**
 * 已用时长文案：秒级 `0s`/`12s`，超过一分钟 `1m23s`。
 *
 * 不显示「预计剩余」——那也是在没有信息时编数字，只是换了个字段。
 */
export function formatElapsedDuration(elapsedMs: number): string {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m${String(seconds % 60).padStart(2, '0')}s`;
}

/**
 * 节点生成中的统一 loading 覆盖层：
 * - 后端报了进度 → 中央显示真实百分比；
 * - 后端还没报 → 中央显示**已用时长** + 一条不确定态扫描条，
 *   并且**不输出 `aria-valuenow`**（WAI-ARIA 的不确定进度条契约）。
 */
export function NodeGenerationOverlay({
  startedAt = null,
  progress = null,
  hasBackground: _hasBackground = false,
  rounded = 'rounded-[var(--node-radius)]',
  onCancel,
  cancelPending = false,
}: NodeGenerationOverlayProps) {
  const [now, setNow] = useState(() => Date.now());
  const [mountedAt] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 120);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  const percent = resolveGenerationProgress(progress);
  const indeterminate = percent === null;
  const elapsedLabel = useMemo(() => {
    const begin = typeof startedAt === 'number' ? startedAt : mountedAt;
    return formatElapsedDuration(Math.max(0, now - begin));
  }, [mountedAt, now, startedAt]);

  return (
    <div
      className={`village-node-generation-overlay pointer-events-none absolute inset-0 z-10 flex items-center justify-center overflow-hidden ${rounded}`}
      data-village-node-generating="true"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      // 不确定态**必须**省略 aria-valuenow：带着一个编出来的值，读屏会把
      // 「不知道」念成具体百分比，那正是这次要消灭的假信息。
      aria-valuenow={indeterminate ? undefined : percent}
      data-village-node-progress={indeterminate ? 'indeterminate' : 'determinate'}
    >
      <div className="relative flex flex-col items-center text-center">
        {indeterminate ? (
          // 已用时长是真实读数。放在原百分比的位置，让「还没数」和「数到 87%」
          // 在视觉上就分得开，不必去读小字说明。
          <div className="flex items-center gap-2 leading-none text-white">
            <Loader2 className="size-5 shrink-0 animate-spin text-white/70" aria-hidden="true" />
            <span className="text-[20px] font-semibold tabular-nums tracking-tight">
              {elapsedLabel}
            </span>
          </div>
        ) : (
          <div className="flex items-baseline leading-none text-white">
            <span className="text-[34px] font-semibold tabular-nums tracking-tight">
              {percent}
            </span>
            <span className="ml-1 text-[15px] font-medium text-white/70">%</span>
          </div>
        )}
        {indeterminate && (
          <div
            className="village-node-generation-overlay__scan mt-3 h-[3px] w-24 overflow-hidden rounded-full bg-white/15"
            aria-hidden="true"
          >
            <div className="village-node-generation-overlay__scan-bar h-full w-1/2 rounded-full bg-white/70" />
          </div>
        )}
        {onCancel && (
          <button
            type="button"
            className="nodrag nowheel pointer-events-auto mt-4 inline-flex items-center gap-1.5 rounded-full border border-white/20 bg-black/45 px-3 py-1.5 text-[11px] font-medium text-white/85 shadow-lg backdrop-blur-md transition-colors hover:border-red-300/55 hover:bg-red-950/55 hover:text-white disabled:cursor-wait disabled:opacity-70"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              onCancel();
            }}
            disabled={cancelPending}
            aria-label={cancelPending ? '正在终止节点任务' : '终止节点任务'}
            title={cancelPending ? '正在终止节点任务' : '终止节点任务'}
            data-village-node-cancel="true"
          >
            {cancelPending ? (
              <Loader2 className="size-3 animate-spin" aria-hidden="true" />
            ) : (
              <Square className="size-3 fill-current" aria-hidden="true" />
            )}
            {cancelPending ? '正在终止' : '终止任务'}
          </button>
        )}
      </div>
    </div>
  );
}
