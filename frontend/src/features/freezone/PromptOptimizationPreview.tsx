// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from "react";

import type { FreezonePromptOptimizeResult } from "@/api/ops";
import {
  ArrowRight,
  ChevronDown,
  Globe,
  Loader2,
  TriangleAlert,
  WandSparkles,
  X,
} from "lucide-react";

type PromptStrategyContract = FreezonePromptOptimizeResult["strategy_contract"];

export type PromptOptimizationResearchMode = "quick" | "standard" | "expert";

const WORKFLOW_LABELS: Record<string, string> = {
  text_to_image: "文生图",
  image_to_image: "图生图",
  text_to_video: "文生视频",
  image_to_video: "图生视频",
  first_last_frame: "首尾帧",
  multimodal_reference: "全能参考",
  video_edit: "视频编辑",
};

const STRATEGY_LABELS: Record<string, string> = {
  image: "图片型",
  narrative: "叙事型",
  control: "控制型",
};

const RESEARCH_STATUS_LABELS: Record<string, string> = {
  not_requested: "未联网",
  not_needed: "本地知识足够",
  completed: "已联网核验",
  degraded: "联网证据不足",
  unavailable: "联网不可用，已降级",
};

const RESEARCH_MODE_LABELS: Record<PromptOptimizationResearchMode, string> = {
  quick: "关闭",
  standard: "自动",
  expert: "深度联网",
};

const RESEARCH_MODE_OPTIONS: Array<{
  mode: PromptOptimizationResearchMode;
  label: string;
  hint: string;
}> = [
  { mode: "quick", label: "关闭", hint: "不联网，只用本地知识库与模型知识卡" },
  { mode: "standard", label: "自动", hint: "命中触发条件时才联网检索（默认）" },
  { mode: "expert", label: "深度联网", hint: "每次都联网核对官方资料与最新做法" },
];

export function formatPromptStrategySummary(
  contract: PromptStrategyContract,
): string | null {
  if (!contract?.workflow || !contract.strategy) {
    return null;
  }

  const parts = [
    WORKFLOW_LABELS[contract.workflow] ?? contract.workflow,
    STRATEGY_LABELS[contract.strategy] ?? contract.strategy,
  ];
  const isVideoWorkflow = !["text_to_image", "image_to_image"].includes(
    contract.workflow,
  );
  if (
    isVideoWorkflow &&
    typeof contract.duration_seconds === "number" &&
    Number.isFinite(contract.duration_seconds)
  ) {
    const duration = Number.isInteger(contract.duration_seconds)
      ? String(contract.duration_seconds)
      : String(Number(contract.duration_seconds.toFixed(3)));
    parts.push(`${duration} 秒`);
  }
  if (
    isVideoWorkflow &&
    typeof contract.max_temporal_beats === "number" &&
    Number.isFinite(contract.max_temporal_beats)
  ) {
    parts.push(`${contract.max_temporal_beats} 段动作`);
  }
  if (
    isVideoWorkflow &&
    typeof contract.max_camera_moves === "number" &&
    Number.isFinite(contract.max_camera_moves)
  ) {
    parts.push(`${contract.max_camera_moves} 个主运镜`);
  }

  return `策略：${parts.join(" · ")}`;
}

function formatOptimizerModelLabel(model: string): string {
  const clean = String(model || "").trim();
  if (!clean) return "未知模型";
  if (clean === "local-deterministic-prompt-optimizer") return "本地规则";
  return clean.replace(/^direct\//, "");
}

function truncateText(text: string, maxLength = 180): string {
  const clean = String(text || "").trim();
  return clean.length > maxLength ? `${clean.slice(0, maxLength)}…` : clean;
}

interface PromptOptimizationPreviewProps {
  originalPrompt: string;
  result: FreezonePromptOptimizeResult;
  targetModelLabel: string;
  stale?: boolean;
  /** 正在用某个联网深度重跑时，按钮显示加载态。 */
  rerunningMode?: PromptOptimizationResearchMode | null;
  onReoptimize?: (mode: PromptOptimizationResearchMode) => void;
  onClose: () => void;
  onApply: () => void;
}

export function PromptOptimizationPreview({
  originalPrompt,
  result,
  targetModelLabel,
  stale = false,
  rerunningMode = null,
  onReoptimize,
  onClose,
  onApply,
}: PromptOptimizationPreviewProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const strategySummary = formatPromptStrategySummary(result.strategy_contract);
  const researchMode = result.research_mode ?? "standard";
  const researchStatusLabel = result.research_status
    ? RESEARCH_STATUS_LABELS[result.research_status] ?? result.research_status
    : "未联网";
  const researchSkipReason = String(result.research_skip_reason ?? "").trim();
  const researchTriggerReasons = result.research_trigger_reasons ?? [];
  const researchSources = (result.research_sources ?? []).filter(
    (source) => typeof source.url === "string" && source.url.length > 0,
  );
  const warnings = result.warnings ?? [];
  const criticIssues = result.critic_issues ?? [];
  const fallbackUsed = result.optimizer_fallback_used === true;
  const hasDetails =
    researchSources.length > 0 ||
    (researchTriggerReasons.length > 0 && result.research_used !== true) ||
    criticIssues.length > 0;

  return (
    <div
      className="village-prompt-optimization-preview nodrag nowheel absolute bottom-14 left-3 right-3 z-[70] overflow-y-auto overscroll-contain rounded-[16px] border p-3 text-white backdrop-blur-xl"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="village-prompt-optimization-preview__header">
        <div className="min-w-0">
          <div className="village-prompt-optimization-preview__title">
            <WandSparkles aria-hidden="true" />
            <span>优化提示词</span>
          </div>
          <div className="village-prompt-optimization-preview__model" title={targetModelLabel}>
            针对 {targetModelLabel} 生成
          </div>
          <div
            className={`village-prompt-optimization-preview__source${
              fallbackUsed ? " village-prompt-optimization-preview__source--fallback" : ""
            }`}
            title={result.optimizer_model}
          >
            {fallbackUsed ? (
              <TriangleAlert aria-hidden="true" />
            ) : (
              <WandSparkles aria-hidden="true" />
            )}
            <span>
              {fallbackUsed
                ? "上游文字模型不可用，已改用本地规则优化（不消耗模型额度）"
                : `由 ${formatOptimizerModelLabel(result.optimizer_model)} 优化`}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭提示词优化结果"
          className="village-prompt-optimization-preview__close"
        >
          <X aria-hidden="true" />
        </button>
      </div>

      <div className="village-prompt-optimization-preview__compare">
        <div className="village-prompt-optimization-preview__pane">
          <div className="village-prompt-optimization-preview__label">优化前</div>
          <div className="village-prompt-optimization-preview__prompt">
            {originalPrompt}
          </div>
        </div>
        <div className="village-prompt-optimization-preview__pane village-prompt-optimization-preview__pane--optimized">
          <div className="village-prompt-optimization-preview__label">
            <span>优化后</span>
            <ArrowRight aria-hidden="true" />
          </div>
          <div className="village-prompt-optimization-preview__prompt">
            {result.optimized_prompt}
          </div>
        </div>
      </div>

      <div className="village-prompt-optimization-preview__summary">
        <div className="village-prompt-optimization-preview__summary-line">
          <Globe aria-hidden="true" />
          <span>
            联网：{researchStatusLabel}
            {researchSkipReason ? `（${researchSkipReason}）` : ""} ·{" "}
            {RESEARCH_MODE_LABELS[researchMode] ?? researchMode}
          </span>
        </div>
        {strategySummary && (
          <div className="village-prompt-optimization-preview__summary-line">
            <span>{strategySummary}</span>
          </div>
        )}
      </div>

      {onReoptimize && (
        <div
          className="village-prompt-optimization-preview__research-modes"
          role="group"
          aria-label="联网研究深度"
        >
          {RESEARCH_MODE_OPTIONS.map((option) => {
            const active = option.mode === researchMode;
            const running = rerunningMode === option.mode;
            return (
              <button
                key={option.mode}
                type="button"
                title={option.hint}
                disabled={Boolean(rerunningMode)}
                className={`village-prompt-optimization-preview__research-mode${
                  active ? " village-prompt-optimization-preview__research-mode--active" : ""
                }`}
                onClick={() => {
                  if (active) return;
                  onReoptimize(option.mode);
                }}
              >
                {running && <Loader2 className="animate-spin" aria-hidden="true" />}
                {option.label}
              </button>
            );
          })}
        </div>
      )}

      {warnings.length > 0 && (
        <div className="village-prompt-optimization-preview__warnings">
          <ul>
            {warnings.map((warning, index) => (
              <li key={`${index}-${warning.slice(0, 12)}`} title={warning}>
                {truncateText(warning)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {hasDetails && (
        <>
          <button
            type="button"
            className="village-prompt-optimization-preview__details-toggle"
            aria-expanded={detailsOpen}
            onClick={() => setDetailsOpen((open) => !open)}
          >
            <ChevronDown
              aria-hidden="true"
              className={
                detailsOpen
                  ? "village-prompt-optimization-preview__details-chevron--open"
                  : undefined
              }
            />
            <span>{detailsOpen ? "收起详情" : "详情"}</span>
          </button>
          {detailsOpen && (
            <div className="village-prompt-optimization-preview__details">
              {researchTriggerReasons.length > 0 && result.research_used !== true && (
                <div>
                  <span className="village-prompt-optimization-preview__label">
                    联网触发条件
                  </span>
                  <span>{researchTriggerReasons.join("；")}</span>
                </div>
              )}
              {researchSources.length > 0 && (
                <div>
                  <span className="village-prompt-optimization-preview__label">研究来源</span>
                  <span className="village-prompt-optimization-preview__research-links">
                    {researchSources.slice(0, 4).map((source, index) => (
                      <a
                        key={`${source.url}-${index}`}
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        title={source.title || source.url}
                        className="underline decoration-white/40 underline-offset-2"
                      >
                        {source.title || source.url}
                      </a>
                    ))}
                  </span>
                </div>
              )}
              {criticIssues.length > 0 && (
                <div>
                  <span className="village-prompt-optimization-preview__label">审校提示</span>
                  <span>{criticIssues.join("；")}</span>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {stale && (
        <div className="village-prompt-optimization-preview__stale">
          节点提示词、执行模型、模式、参数或参考素材已变化。本结果已过期，不能直接采用；请重新优化。
        </div>
      )}

      <div className="village-prompt-optimization-preview__actions">
        <button
          type="button"
          onClick={onClose}
          className="village-prompt-optimization-preview__keep"
        >
          不采用
        </button>
        <button
          type="button"
          onClick={onApply}
          disabled={stale}
          className="village-prompt-optimization-preview__apply"
        >
          {stale ? "结果已过期" : "采用优化结果"}
        </button>
      </div>
    </div>
  );
}
