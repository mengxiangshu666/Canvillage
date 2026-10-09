// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  formatPromptStrategySummary,
  PromptOptimizationPreview,
} from "./PromptOptimizationPreview";

const result = {
  optimized_prompt: "优化稿",
  preserved_intent: "保持原意",
  changes: ["补充动作"],
  applied_rules: ["首帧是 t=0"],
  warnings: [],
  output_language: "zh" as const,
  optimizer_model: "DC-seedance2-prompt-composer-LLM",
  target_model_profile: "seedance_2_prompt_profile_v1",
  knowledge_profiles: ["seedance_2_prompt_profile_v1"],
  knowledge_sources: ["D:\\knowledge\\Seedance.md"],
  knowledge_live_sources: ["D:\\knowledge\\Seedance.md"],
  knowledge_retrieval_mode: "live-local-files+curated-profiles" as const,
  knowledge_profile_details: [
    { id: "seedance_2_prompt_profile_v1", label: "Seedance", authority: "official-derived" as const },
  ],
  revision: "xiaoshu-rag-model-optimizer.v2",
};

describe("PromptOptimizationPreview", () => {
  it("formats a compact strategy summary when the backend returns a contract", () => {
    expect(
      formatPromptStrategySummary({
        workflow: "text_to_video",
        strategy: "narrative",
        duration_seconds: 10,
        max_temporal_beats: 3,
        max_camera_moves: 3,
      }),
    ).toBe("策略：文生视频 · 叙事型 · 10 秒 · 3 段动作 · 3 个主运镜");
    expect(formatPromptStrategySummary(undefined)).toBeNull();
  });

  it("shows the strategy summary without exposing retrieval provenance", () => {
    render(
      <PromptOptimizationPreview
        originalPrompt="原稿"
        result={{
          ...result,
          strategy_contract: {
            workflow: "text_to_video",
            strategy: "narrative",
            duration_seconds: 10,
            max_temporal_beats: 3,
            max_camera_moves: 3,
          },
        }}
        targetModelLabel="Seedance 2.0"
        onClose={vi.fn()}
        onApply={vi.fn()}
      />,
    );

    expect(
      screen.getByText("策略：文生视频 · 叙事型 · 10 秒 · 3 段动作 · 3 个主运镜"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/实际检索的知识规则/)).not.toBeInTheDocument();
  });

  it("keeps the result focused on a clean before-and-after decision", () => {
    render(
      <PromptOptimizationPreview
        originalPrompt="原稿"
        result={result}
        targetModelLabel="Seedance 2.0"
        onClose={vi.fn()}
        onApply={vi.fn()}
      />,
    );

    expect(screen.getByText("优化前")).toBeInTheDocument();
    expect(screen.getByText("优化后")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "不采用" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采用优化结果" })).toBeInTheDocument();
    expect(screen.queryByText(/实际检索的知识规则/)).not.toBeInTheDocument();
    expect(screen.queryByText(/已应用/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Seedance · official-derived/)).not.toBeInTheDocument();
  });

  it("blocks applying a result after the node context changed", () => {
    const onApply = vi.fn();
    render(
      <PromptOptimizationPreview
        originalPrompt="原稿"
        result={result}
        targetModelLabel="Seedance 2.0"
        stale
        onClose={vi.fn()}
        onApply={onApply}
      />,
    );

    const apply = screen.getByRole("button", { name: "结果已过期" });
    expect(apply).toBeDisabled();
    expect(screen.getByText(/请重新优化/)).toBeInTheDocument();
    apply.click();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("surfaces a silent fallback to the local rule optimizer", () => {
    render(
      <PromptOptimizationPreview
        originalPrompt="原稿"
        result={{
          ...result,
          optimizer_model: "local-deterministic-prompt-optimizer",
          optimizer_fallback_used: true,
          optimizer_failed_models: [
            { model: "direct/text-4a27c9b4fc34f694", error: "status_code: 503" },
          ],
          warnings: ["LLM 提示词优化通道不可用，已启用本地确定性优化，不消耗模型额度。"],
        }}
        targetModelLabel="Seedance 2.0"
        onClose={vi.fn()}
        onApply={vi.fn()}
      />,
    );

    expect(
      screen.getByText("上游文字模型不可用，已改用本地规则优化（不消耗模型额度）"),
    ).toBeInTheDocument();
    expect(screen.getByText(/LLM 提示词优化通道不可用/)).toBeInTheDocument();
  });

  it("explains why联网研究 was skipped and keeps sources one click away", () => {
    render(
      <PromptOptimizationPreview
        originalPrompt="原稿"
        result={{
          ...result,
          research_mode: "standard",
          research_status: "not_requested",
          research_skip_reason: "缺少项目上下文",
          research_trigger_reasons: ["提示词点名了其他模型或外部风格"],
          research_sources: [
            { title: "Official model guide", url: "https://docs.example.invalid/model" },
          ],
        }}
        targetModelLabel="Seedance 2.0"
        onClose={vi.fn()}
        onApply={vi.fn()}
      />,
    );

    // 默认只留一行摘要，不摊开一堆信息块。
    expect(screen.getByText(/联网：未联网（缺少项目上下文） · 自动/)).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Official model guide" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "详情" }));
    expect(
      screen.getByRole("link", { name: "Official model guide" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/提示词点名了其他模型或外部风格/)).toBeInTheDocument();
  });

  it("re-runs the optimization with the chosen research depth", () => {
    const onReoptimize = vi.fn();
    render(
      <PromptOptimizationPreview
        originalPrompt="原稿"
        result={{
          ...result,
          research_mode: "standard",
          research_status: "not_needed",
        }}
        targetModelLabel="Seedance 2.0"
        onReoptimize={onReoptimize}
        onClose={vi.fn()}
        onApply={vi.fn()}
      />,
    );

    expect(screen.getByText(/联网：本地知识足够 · 自动/)).toBeInTheDocument();
    screen.getByRole("button", { name: "深度联网" }).click();
    expect(onReoptimize).toHaveBeenCalledWith("expert");

    onReoptimize.mockClear();
    screen.getByRole("button", { name: "自动" }).click();
    expect(onReoptimize).not.toHaveBeenCalled();
  });
});
