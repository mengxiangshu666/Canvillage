// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from 'react';
import { Loader2, ImageOff, Sparkles, TriangleAlert } from 'lucide-react';

import { DirectModelPicker } from '@/features/canvas/ui/DirectModelPicker';
import { STORYBOARD_ASPECTS } from '@/features/canvas/domain/storyboardGroup';
import { SCRIPT_NODE_Z } from './scriptNodeLayout';
import { formatShotNumbers, describeShotScope, SCRIPT_REFERENCE_IMAGE_CAP, type ScriptPreflight } from './scriptPreflight';
import type { ScriptPaidActionGate } from './scriptPaidActionGate';

/**
 * 「生成分镜」确认弹层。
 *
 * 对齐 LibTV 脚本节点的生成分镜：先确认图片模型与比例（默认 16:9），再建组出图。
 * 已有分镜组时提示只重跑未出图 / 失败的那几张（对应 LibTV 的
 * `storyboardRegenerateMessage`「将对未生成或失败的分镜图重新生成，是否继续？」）。
 *
 * 主按钮是「真出图」那条路（建节点后由节点自身提交生成）；安静的次按钮只建节点、
 * 不出图 —— 用户想先看提示词再决定要不要烧点数时用得到。
 *
 * **逐镜清单**（2026-09-16 补）：此前这个弹层只报一个总数，用户点下去等于开盲盒 ——
 * 「脚本已变更」到底是改了提示词还是换了角色图、缺图的角色是不是缺一个两个，都看不到。
 * 现在这块由 {@link ScriptPreflight} 统一出数，逐镜列清楚：待出图 / 已过期 / 缺项，
 * 以及**这一镜到底会不会被提交**（缺图片提示词的那几镜建了节点也出不来图）。
 */

/** 清单里最多逐条列几镜，多的折成「等 n 镜」——弹层不该变成一张长表。 */
const MAX_LISTED_SHOTS = 8;

export interface ScriptStoryboardDialogProps {
  open: boolean;
  /** create：首次生成；regenerate：已有分镜组，重跑未出图的。 */
  mode: 'create' | 'regenerate';
  shotCount: number;
  /** 本轮实际需要出图的张数（regenerate 时是未出图 / 失败 / 已失效的数量）。 */
  pendingCount: number;
  /**
   * 逐镜清点结果。它给出的 `generatableShotCount` 才是**真的会被提交**的张数：
   * `pendingCount` 里有几镜缺图片提示词，图片节点建出来也会以「请先填写提示词」
   * 拒绝提交、一直挂着 —— 不提前说，用户会以为它在出图。
   */
  preflight?: ScriptPreflight | null;
  /**
   * 资产层缺口（第四刀）。逐镜清单把同一个角色的缺口报 N 遍，这里按**资产**报一次，
   * 并给一个就地补齐的入口。缺参考图是降级不是阻塞，所以只提示、不禁用主按钮。
   */
  assetGap?: { missingCount: number; missingSummary: string } | null;
  /** 跳到「生成资产图」。不传时不显示那个入口。 */
  onFixAssetGap?: () => void;
  /** 脚本行变了、会重建整组时提示用户。 */
  willRebuild?: boolean;
  groupLabel: string;
  model: string;
  aspectKey: string;
  /**
   * 此刻不该动这个弹层（脚本自己正在重新生成：行随时会被替换）。
   * 两个**动手**的按钮（生成 / 仅建节点）会禁用，并在底部说明**为什么** ——
   * 只转个圈会让人以为分镜图在出。取消**不禁用**：关一个本地弹层不应该被拦住。
   */
  busy?: boolean;
  /** busy 的原因；不传时给一句通用说明。 */
  busyReason?: string | null;
  /** 已格式化的预估点数（拿不到账单规则时为 null）。 */
  priceDisplay?: string | null;
  /** 真正出图前的当前门禁；仅建节点不受它影响。 */
  paidActionGate?: ScriptPaidActionGate | null;
  onModelChange: (next: string) => void;
  onAspectChange: (next: string) => void;
  onCancel: () => void;
  /** 生成分镜（出图）。 */
  onConfirm: () => void;
  /** 仅建节点（不出图）。regenerate 模式下不显示。 */
  onCreateOnly?: () => void;
}

export function ScriptStoryboardDialog({
  open,
  mode,
  shotCount,
  pendingCount,
  preflight = null,
  assetGap = null,
  onFixAssetGap,
  willRebuild = false,
  groupLabel,
  model,
  aspectKey,
  busy = false,
  busyReason = null,
  priceDisplay = null,
  paidActionGate = null,
  onModelChange,
  onAspectChange,
  onCancel,
  onConfirm,
  onCreateOnly,
}: ScriptStoryboardDialogProps) {
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

  // 缺图片提示词的镜：建了节点也提交不了，是这次真正要用户回去改的东西，单列。
  const blockedShots = preflight?.blockedShotNumbers ?? [];
  const staleShots = preflight?.staleShotNumbers ?? [];
  const pendingShots = preflight?.pendingShotNumbers ?? [];
  const degradedShots = preflight?.degradedShotNumbers ?? [];
  const overflowShots = preflight?.overflowShotNumbers ?? [];
  const tokenMissingShots = preflight?.tokenMissingShotNumbers ?? [];
  const generatable = preflight?.generatableShotCount ?? pendingCount;
  const paidAllowed = paidActionGate?.allowed ?? true;
  const paidBlockedReason = paidActionGate?.reason ?? null;

  const description =
    mode === 'regenerate'
      ? willRebuild
        ? `分镜行已变化，将重建「${groupLabel}」并按 ${aspectKey} 逐张出图，共 ${shotCount} 张。`
        : pendingCount > 0
          ? staleShots.length > 0
            // 区分「脚本改过所以过期」与「还没出图」：前者用户可能并不想重跑，
            // 得让他知道这次点下去会重出已存在的图 —— 并说清是第几镜。
            ? `脚本已变更：${formatShotNumbers(staleShots)} 与当前脚本行对不上，将连同未出图的共 ${pendingCount} 张一起重新生成。`
            : `将对未出图或失败的 ${describeShotScope(pendingShots, pendingCount)}重新生成，其余已出的图不动。`
          : '所有分镜图都已出图，无需重新生成。'
      : `按 ${shotCount} 个分镜在脚本节点右侧生成「${groupLabel}」，每个分镜一张图。`;

  return (
    <div
      className="fixed inset-0 flex items-center justify-center bg-black/70 p-6"
      style={{ zIndex: SCRIPT_NODE_Z.storyboardDialog }}
      onClick={(event) => event.stopPropagation()}
    >
      {/* 与画布其它浮层同一套材质：深底、细描边、靠间距分组而不是靠描边堆叠。 */}
      <div className="flex w-[min(460px,92vw)] flex-col gap-4 rounded-[14px] border border-white/[0.1] bg-[#1c1c1e]/98 p-4 text-text-dark shadow-[0_20px_60px_rgba(0,0,0,0.55)] backdrop-blur-xl">
        <header className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-text-muted" />
          <span className="text-[13px] font-medium">
            {mode === 'regenerate' ? '重新生成分镜图' : '生成分镜'}
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

        {assetGap && assetGap.missingCount > 0 && (
          <div className="flex items-start gap-1.5 rounded-[10px] border border-white/[0.08] bg-white/[0.03] p-2.5 text-[12px] leading-relaxed text-text-muted">
            <ImageOff className="h-3.5 w-3.5 shrink-0 translate-y-0.5" />
            <span className="min-w-0 flex-1">
              {`${assetGap.missingSummary} 还没有可用的资产图，这几镜会照常出图，但少一张参考图。`}
            </span>
            {onFixAssetGap && (
              <button
                type="button"
                className="shrink-0 rounded-[6px] px-1.5 py-0.5 text-[11px] text-text-dark/85 transition-colors hover:bg-white/[0.08] hover:text-text-dark"
                onClick={onFixAssetGap}
              >
                去生成
              </button>
            )}
          </div>
        )}

        {preflight &&
          (staleShots.length > 0 ||
            blockedShots.length > 0 ||
            degradedShots.length > 0 ||
            overflowShots.length > 0 ||
            tokenMissingShots.length > 0) && (
          <div className="flex flex-col gap-1.5 rounded-[10px] border border-amber-300/20 bg-amber-400/[0.07] p-2.5 text-[12px] leading-relaxed">
            <div className="flex items-center gap-1.5 text-amber-200/90">
              <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
              <span className="font-medium">逐镜检查</span>
            </div>
            {/* 缺图片提示词的那几镜：建了节点也提交不出去，必须先回去补。 */}
            {blockedShots.length > 0 && (
              <p className="text-amber-100/85">
                {formatShotNumbers(blockedShots, MAX_LISTED_SHOTS)} 缺图片提示词（分镜提示词与画面描述都空），
                这几张建出来也不会出图 —— 图片节点会按「请先填写提示词」拒绝提交。请先回表里补上。
              </p>
            )}
            {/* 角色没图：能出图，但少一张参考图，出来的人不像那个人 —— 是降级不是阻断。 */}
            {degradedShots.length > 0 && (
              <p className="text-text-muted">
                {formatShotNumbers(degradedShots, MAX_LISTED_SHOTS)} 的角色没有角色图，
                这几镜会照常出图，但生成时拿不到该角色的参考图。
              </p>
            )}
            {/* 参考图超上限：后端直接 `reference_images[:9]` 丢掉多出来的，不报警不记日志。
                只在后台拦没用 —— 用户看不到自己的图被丢了。 */}
            {overflowShots.length > 0 && (
              <p className="text-text-muted">
                {formatShotNumbers(overflowShots, MAX_LISTED_SHOTS)} 的参考图多于
                {` ${SCRIPT_REFERENCE_IMAGE_CAP} `}张，超出的部分会被上游丢掉，锚定块里的
                「图片N」也会与实际上传的对不上。这几镜请减少资产或拆镜。
              </p>
            )}
            {/* 提示词指向了不存在的编号：视频侧有同款硬闸门，图片侧此前完全没有。 */}
            {tokenMissingShots.length > 0 && (
              <p className="text-text-muted">
                {formatShotNumbers(tokenMissingShots, MAX_LISTED_SHOTS)} 的提示词里引用了
                这一镜没有的图片编号（例如只带 2 张图却写了「图片3」），那一句会被模型忽略。
              </p>
            )}
          </div>
        )}

        <div className="flex flex-col gap-3 rounded-[10px] bg-white/[0.03] p-3">
          <label className="flex items-center justify-between gap-3">
            <span className="shrink-0 text-[12px] text-text-muted">图片模型</span>
            <DirectModelPicker
              kind="image"
              value={model}
              onChange={onModelChange}
              ariaLabel="分镜图模型"
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
            {busy
              ? (busyReason ?? '脚本正在生成中，等它结束再生成分镜图')
              : pendingCount > 0 && priceDisplay
                ? // 点数按真的会被提交的张数报：`generatable` 已扣掉缺提示词的那几镜，
                  // 用 pendingCount 会多报（那几张根本不会发请求）。
                  `将出图 ${generatable} 张 · 预计消耗 ${priceDisplay}`
                : ''}
          </span>
          <div className="flex items-center gap-2">
            {mode === 'create' && onCreateOnly && (
              <button
                type="button"
                className="h-7 rounded-[8px] px-2.5 text-[12px] text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text-dark disabled:opacity-50"
                onClick={onCreateOnly}
                disabled={busy}
              >
                仅建节点
              </button>
            )}
            {/* 「取消」只关本地弹层，任何时候都能按 —— 把它也禁用会把用户关在弹层里。 */}
            <button
              type="button"
              className="h-7 rounded-[8px] px-2.5 text-[12px] text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text-dark"
              onClick={onCancel}
            >
              取消
            </button>
            <button
              type="button"
              className="inline-flex h-7 items-center gap-1.5 rounded-[8px] bg-white px-3 text-[12px] font-medium text-bg-dark transition-colors hover:bg-white/90 disabled:opacity-50"
              onClick={onConfirm}
              disabled={busy || pendingCount === 0 || !paidAllowed}
              title={
                busy
                  ? (busyReason ?? '脚本正在生成中，等它结束再生成分镜图')
                  : pendingCount === 0
                    ? '没有需要生成的分镜图'
                    : paidBlockedReason
                      ? paidBlockedReason
                    : generatable === 0
                      ? '待生成的镜都缺图片提示词，先回表里补齐'
                      : undefined
              }
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {mode === 'regenerate' ? '开始生成' : '生成分镜'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
