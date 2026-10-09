import type { ChatAttachment, ChatMessage, ModelEntry } from "./types";

export type AgentContextUsage = {
  available: boolean;
  contextLength: number;
  estimatedUsedTokens: number;
  estimatedRemainingTokens: number;
  remainingPercent: number;
  label: string;
  detail: string;
  tone: "normal" | "warning" | "critical";
};

function estimatedTextTokens(text: string): number {
  let units = 0;
  for (const character of text) {
    units += /[\u3400-\u9fff\uf900-\ufaff]/u.test(character) ? 1 : /\s/u.test(character) ? 0.08 : 0.28;
  }
  return Math.ceil(units);
}

function attachmentTokens(attachments: readonly ChatAttachment[] | undefined): number {
  return (attachments?.length ?? 0) * 640;
}

function compactTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

export function estimateAgentContextUsage(input: {
  model: ModelEntry | null | undefined;
  messages: readonly ChatMessage[];
  draft?: string;
  streamText?: string;
  attachments?: readonly ChatAttachment[];
}): AgentContextUsage {
  const contextLength = Math.max(0, Math.floor(input.model?.contextLength ?? 0));
  if (!contextLength) {
    return {
      available: false,
      contextLength: 0,
      estimatedUsedTokens: 0,
      estimatedRemainingTokens: 0,
      remainingPercent: 0,
      label: "上下文待模型",
      detail: "模型窗口未知；请先配置并检测当前模型",
      tone: "warning",
    };
  }

  const messageTokens = input.messages.reduce((total, message) => (
    total + estimatedTextTokens(message.text || "") + attachmentTokens(message.attachments) + 16
  ), 0);
  const reservedOutput = Math.max(512, Math.floor(input.model?.maxOutputTokens ?? 0));
  const estimatedUsedTokens = Math.min(
    contextLength,
    1_200
      + reservedOutput
      + messageTokens
      + estimatedTextTokens(input.draft || "")
      + estimatedTextTokens(input.streamText || "")
      + attachmentTokens(input.attachments),
  );
  const estimatedRemainingTokens = Math.max(0, contextLength - estimatedUsedTokens);
  const remainingPercent = Math.max(0, Math.min(100, Math.round(
    (estimatedRemainingTokens / contextLength) * 100,
  )));
  const tone = remainingPercent < 15 ? "critical" : remainingPercent <= 40 ? "warning" : "normal";
  return {
    available: true,
    contextLength,
    estimatedUsedTokens,
    estimatedRemainingTokens,
    remainingPercent,
    label: `上下文剩余约 ${remainingPercent}%`,
    detail: `估算已使用 ${compactTokens(estimatedUsedTokens)} / ${compactTokens(contextLength)} · 已预留本轮输出`,
    tone,
  };
}
