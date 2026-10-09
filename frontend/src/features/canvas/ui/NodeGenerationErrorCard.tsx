// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { type PointerEvent as ReactPointerEvent, useId, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Copy,
  RefreshCcw,
  SlidersHorizontal,
  X,
} from 'lucide-react';

interface NodeGenerationErrorCardProps {
  title?: string;
  message: string;
  details?: string | null;
  requestId?: string | null;
  stage?: string | null;
  suggestedAction?: string | null;
  retryBusy?: boolean;
  retryDisabled?: boolean;
  retryDisabledReason?: string | null;
  recoverBusy?: boolean;
  recoverDisabled?: boolean;
  recoverDisabledReason?: string | null;
  onRetry?: () => void;
  onRecover?: () => void;
  onChangeModel?: () => void;
  onDismiss?: () => void;
}

// Order matters: the explicit-moderation rule must win over the generic one,
// because HK relays reuse the same Chinese "安全政策" sentence for length,
// format and reference failures.
const HUMAN_ERROR_RULES: ReadonlyArray<[RegExp, string]> = [
  [/\b524\b|origin_response_timeout/i, '模型通道等待超时（524），请至少等 2 分钟后重试；反复失败请更换通道。'],
  [/multiple image references require a multi-reference video model/i, '当前模型不支持多张参考图，请更换支持多参考的视频模型。'],
  [/not supported model for image generation|only imagen models are supported/i, '当前模型不支持图片生成，请更换生图模型。'],
  [/\b(?:http\s*)?401\b|unauthorized|invalid api[_ -]?key|authentication failed/i, '当前模型认证失败，请检查 API Key。'],
  [/\b(?:http\s*)?403\b|forbidden|permission denied/i, '当前渠道拒绝了本次请求，请检查模型权限或接口协议。'],
  [/\b(?:http\s*)?404\b|not found/i, '当前提交路径或模型 ID 不存在，请检查模型配置。'],
  [/\b(?:http\s*)?429\b|rate limit|too many requests/i, '当前渠道请求过多，请稍后重新生成。'],
  [/timed?\s*out|timeout/i, '上游生成等待超时，可以重新生成或稍后查看任务结果。'],
  [/insufficient.*(?:credit|balance)|quota.*(?:exceed|insufficient)/i, '当前渠道额度不足，请检查余额或配额。'],
  [/safety_violations|moderation_blocked|image_generation_user_error/i, '上游内容审核明确拦截了这条请求。请调整画面描述或更换参考素材后重试。'],
  [/\bdirect image API content moderation failed\b/i, '上游内容审核明确拦截了这条请求。请调整画面描述或更换参考素材后重试。'],
  [/安全政策|不适合进行图像生成|无法用于生成图像/i, '上游图像接口返回通用「安全政策」拒绝，多由长提示词、多张参考图或中转抹平错误触发，不代表判定你的内容违规。可直接点「重新生成」，或减少参考图后再试。'],
  [/sensitive content|content.*policy|safety.*(?:block|reject)/i, '素材或提示词被上游内容策略拦截，请调整后重试。'],
  [/without (?:an )?(?:output|result)(?: url)?|未返回结果/i, '上游任务已结束，但没有返回可用结果。'],
];

function compactErrorText(value: string): string {
  return value
    .replace(/^\s*(?:freezone\s+)?(?:image|video|audio)?\s*generation failed\s*:\s*/i, '')
    .replace(/^\s*(?:生成失败|请求失败)\s*[：:]\s*/i, '')
    .trim();
}

export function humanizeGenerationError(message: string, details?: string | null): string {
  const evidence = `${message}\n${details ?? ''}`;
  for (const [pattern, summary] of HUMAN_ERROR_RULES) {
    if (pattern.test(evidence)) return summary;
  }

  const compact = compactErrorText(message);
  const looksMachineGenerated =
    compact.length > 110
    || /[{}\[\]]/.test(compact)
    || /(?:traceback|stack trace|request_id|response body)/i.test(compact);
  return looksMachineGenerated
    ? '本次生成没有成功，详细原因已收进「查看详情」。'
    : compact || '本次生成没有成功，请重新生成。';
}

function DetailRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[52px_minmax(0,1fr)] gap-2 text-left text-[10px] leading-4">
      <span className="text-white/38">{label}</span>
      <span className={`min-w-0 break-words text-white/64 [overflow-wrap:anywhere] ${mono ? 'font-mono' : ''}`}>
        {value}
      </span>
    </div>
  );
}

function stopNodeDrag(event: ReactPointerEvent<HTMLElement>) {
  event.stopPropagation();
}

export function NodeGenerationErrorCard({
  title = '生成失败',
  message,
  details,
  requestId,
  stage,
  suggestedAction,
  retryBusy = false,
  retryDisabled = false,
  retryDisabledReason = null,
  recoverBusy = false,
  recoverDisabled = false,
  recoverDisabledReason = null,
  onRetry,
  onRecover,
  onChangeModel,
  onDismiss,
}: NodeGenerationErrorCardProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const retryReasonId = useId();
  const summary = useMemo(() => humanizeGenerationError(message, details), [details, message]);
  const rawDetails = details?.trim() || message.trim();
  const hasDetails = Boolean(rawDetails || requestId || stage || suggestedAction);

  const copyDiagnostics = async () => {
    const rows = [
      rawDetails,
      stage ? `stage=${stage}` : '',
      requestId ? `request_id=${requestId}` : '',
      suggestedAction ? `suggested_action=${suggestedAction}` : '',
    ].filter(Boolean);
    if (rows.length === 0) return;
    try {
      await navigator.clipboard.writeText(rows.join('\n'));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch (error) {
      console.error('[generation-error-card] copy diagnostics failed', error);
    }
  };

  return (
    <div
      className="flex h-full w-full items-center justify-center p-3"
      data-testid="node-generation-error-card"
    >
      <section className="relative flex max-h-full w-full max-w-[340px] flex-col overflow-hidden rounded-2xl border border-red-300/14 bg-[#1d1d1f]/96 shadow-[0_14px_40px_rgba(0,0,0,0.34)] backdrop-blur-xl">
        {onDismiss && (
          <button
            type="button"
            aria-label="关闭失败提示"
            title="关闭并清除这次失败"
            onPointerDown={stopNodeDrag}
            onClick={(event) => {
              event.stopPropagation();
              onDismiss();
            }}
            className="nodrag nopan absolute right-2 top-2 z-10 inline-flex h-7 w-7 items-center justify-center rounded-full text-white/38 transition-colors hover:bg-white/[0.08] hover:text-white/78"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
        <div className="flex shrink-0 flex-col items-center px-4 pb-3 pt-4 text-center">
          <span className="mb-2 inline-flex h-8 w-8 items-center justify-center rounded-full bg-red-400/10 text-red-200">
            <AlertTriangle className="h-4 w-4" />
          </span>
          <strong className="text-[12px] font-semibold tracking-[0.02em] text-white/90">{title}</strong>
          <p className="mt-1.5 line-clamp-3 text-[11px] leading-[18px] text-white/58" title={summary}>
            {summary}
          </p>
          <div className="mt-3 flex flex-wrap items-center justify-center gap-1.5">
            {onRecover && (
              <button
                type="button"
                disabled={recoverBusy || recoverDisabled || retryBusy}
                title={
                  recoverBusy
                    ? '正在查询已有上游任务'
                    : recoverDisabledReason ?? '重新获取已有结果，不会重新生成'
                }
                onPointerDown={stopNodeDrag}
                onClick={(event) => {
                  event.stopPropagation();
                  onRecover();
                }}
                className="nodrag nopan inline-flex h-7 items-center gap-1.5 rounded-full border border-emerald-200/20 bg-emerald-200/12 px-3 text-[11px] font-medium text-emerald-50 transition-[transform,background-color,opacity] hover:bg-emerald-200/20 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-45"
              >
                <RefreshCcw className={`h-3 w-3 ${recoverBusy ? 'animate-spin' : ''}`} />
                {recoverBusy ? '获取中' : '重新获取'}
              </button>
            )}
            {onRetry && (
              <button
                type="button"
                disabled={retryBusy || retryDisabled}
                title={retryBusy ? '当前任务仍在生成中' : retryDisabledReason ?? '重新生成'}
                aria-describedby={retryDisabled && retryDisabledReason ? retryReasonId : undefined}
                onPointerDown={stopNodeDrag}
                onClick={(event) => {
                  event.stopPropagation();
                  onRetry();
                }}
                className="nodrag nopan inline-flex h-7 items-center gap-1.5 rounded-full bg-white px-3 text-[11px] font-medium text-black transition-[transform,background-color,opacity] hover:bg-white/88 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-45"
              >
                <RefreshCcw className={`h-3 w-3 ${retryBusy ? 'animate-spin' : ''}`} />
                {retryBusy ? '生成中' : '重新生成'}
              </button>
            )}
            {onChangeModel && (
              <button
                type="button"
                onPointerDown={stopNodeDrag}
                onClick={(event) => {
                  event.stopPropagation();
                  onChangeModel();
                }}
                className="nodrag nopan inline-flex h-7 items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.055] px-3 text-[11px] font-medium text-white/74 transition-[transform,background-color,color] hover:bg-white/[0.1] hover:text-white active:scale-[0.97]"
              >
                <SlidersHorizontal className="h-3 w-3" />
                更换模型
              </button>
            )}
          </div>
          {retryDisabled && retryDisabledReason && !retryBusy && (
            <p
              id={retryReasonId}
              className="mt-2 max-w-[260px] text-[10px] leading-4 text-amber-100/62"
            >
              {retryDisabledReason}
            </p>
          )}
        </div>

        {hasDetails && (
          <>
            <button
              type="button"
              aria-expanded={detailsOpen}
              onPointerDown={stopNodeDrag}
              onClick={(event) => {
                event.stopPropagation();
                setDetailsOpen((value) => !value);
              }}
              className="nodrag nopan flex h-8 w-full shrink-0 items-center justify-center gap-1 border-t border-white/[0.065] text-[10px] text-white/42 transition-colors hover:bg-white/[0.035] hover:text-white/68"
            >
              查看详情
              <ChevronDown className={`h-3 w-3 transition-transform duration-200 ${detailsOpen ? 'rotate-180' : ''}`} />
            </button>
            {detailsOpen && (
              <div
                className="nodrag nopan nowheel min-h-0 flex-1 space-y-1.5 overflow-y-auto border-t border-white/[0.055] bg-black/16 px-3 py-2.5"
                onPointerDown={stopNodeDrag}
                onClick={(event) => event.stopPropagation()}
              >
                {stage && <DetailRow label="阶段" value={stage} />}
                {requestId && <DetailRow label="请求 ID" value={requestId} mono />}
                {suggestedAction && <DetailRow label="建议" value={suggestedAction} />}
                {rawDetails && <DetailRow label="原始原因" value={rawDetails} mono />}
                <button
                  type="button"
                  onPointerDown={stopNodeDrag}
                  onClick={(event) => {
                    event.stopPropagation();
                    void copyDiagnostics();
                  }}
                  className="ml-auto mt-1 inline-flex h-6 items-center gap-1 rounded-md px-2 text-[10px] text-white/44 transition-colors hover:bg-white/[0.06] hover:text-white/72"
                >
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {copied ? '已复制' : '复制诊断'}
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
