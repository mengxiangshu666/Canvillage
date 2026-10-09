// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { ChatMessage } from "./types";

export const KNOWLEDGE_RECEIPT_USAGE_STATUSES = [
  "shown",
  "used",
  "verified",
  "blocked",
  "ignored",
] as const;

export type KnowledgeReceiptUsageStatus =
  (typeof KNOWLEDGE_RECEIPT_USAGE_STATUSES)[number];

export type KnowledgeReceiptItem = {
  memory_id: number;
  title: string;
  summary: string;
  kind: string;
  scope_kind: string;
  status: string;
  source: string;
  confidence: number;
  evidence_count: number;
  retrieved_count: number;
  applied_count: number;
  positive_count: number;
  negative_count: number;
  last_verified_at?: string | null;
  candidate_recall: boolean;
  execution_rule: boolean;
  influence: string;
  usage_status: KnowledgeReceiptUsageStatus;
};

export type KnowledgeReceiptSummary = {
  legacySummary: boolean;
  shownCount: number;
  usedCount: number;
  verifiedCount: number;
  projectCount: number;
  userCount: number;
  professionalCount: number;
  items: KnowledgeReceiptItem[];
};

const usageStatuses = new Set<string>(KNOWLEDGE_RECEIPT_USAGE_STATUSES);

function finiteNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function nonNegativeInteger(value: unknown, fallback = 0): number {
  return Math.max(0, Math.floor(finiteNumber(value, fallback)));
}

function boundedConfidence(value: unknown): number {
  return Math.min(1, Math.max(0, finiteNumber(value)));
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function usageStatus(value: unknown): KnowledgeReceiptUsageStatus {
  const normalized = String(value ?? "").trim().toLowerCase();
  return usageStatuses.has(normalized)
    ? normalized as KnowledgeReceiptUsageStatus
    : "shown";
}

function parseItem(
  value: unknown,
  fallbackUsageStatus: KnowledgeReceiptUsageStatus = "shown",
): KnowledgeReceiptItem | null {
  const item = asRecord(value);
  if (!item || !String(item.title ?? "").trim()) return null;
  const memoryId = nonNegativeInteger(item.memory_id);
  if (memoryId <= 0) return null;
  return {
    memory_id: memoryId,
    title: String(item.title).slice(0, 120),
    summary: String(item.summary ?? "").slice(0, 220),
    kind: String(item.kind ?? ""),
    scope_kind: String(item.scope_kind ?? ""),
    status: String(item.status ?? ""),
    source: String(item.source ?? ""),
    confidence: boundedConfidence(item.confidence),
    evidence_count: nonNegativeInteger(item.evidence_count),
    retrieved_count: nonNegativeInteger(item.retrieved_count),
    applied_count: nonNegativeInteger(item.applied_count),
    positive_count: nonNegativeInteger(item.positive_count),
    negative_count: nonNegativeInteger(item.negative_count),
    last_verified_at: typeof item.last_verified_at === "string"
      ? item.last_verified_at
      : null,
    candidate_recall: item.candidate_recall === true,
    execution_rule: item.execution_rule === true,
    influence: String(item.influence ?? ""),
    usage_status: usageStatus(item.usage_status ?? fallbackUsageStatus),
  };
}

/**
 * Parse the redacted knowledge receipt attached to an assistant message.
 * Missing counters are derived from item statuses so older server responses
 * remain understandable without claiming that every recalled item was used.
 */
export function knowledgeReceiptFromMessage(
  message: Pick<ChatMessage, "raw">,
): KnowledgeReceiptSummary | null {
  const raw = asRecord(message.raw);
  const metadata = asRecord(raw?.metadata);
  const receipt = asRecord(metadata?.knowledge_receipt);
  if (!receipt) return null;

  const memoryIds = Array.isArray(receipt.memory_ids) ? receipt.memory_ids : [];
  const usedMemoryIds = new Set(
    (Array.isArray(receipt.used_memory_ids) ? receipt.used_memory_ids : [])
      .map((value) => nonNegativeInteger(value))
      .filter((value) => value > 0),
  );
  const verifiedMemoryIds = new Set(
    (Array.isArray(receipt.verified_memory_ids) ? receipt.verified_memory_ids : [])
      .map((value) => nonNegativeInteger(value))
      .filter((value) => value > 0),
  );
  const items = Array.isArray(receipt.items)
    ? receipt.items
      .map((item) => {
        const rawItem = asRecord(item);
        const memoryId = nonNegativeInteger(rawItem?.memory_id);
        const fallbackStatus = verifiedMemoryIds.has(memoryId)
          ? "verified"
          : usedMemoryIds.has(memoryId)
            ? "used"
            : "shown";
        return parseItem(item, fallbackStatus);
      })
      .filter((item): item is KnowledgeReceiptItem => item !== null)
      .slice(0, 12)
    : [];
  const derivedUsedCount = items.filter((item) => ["used", "verified"].includes(item.usage_status)).length;
  const derivedVerifiedCount = items.filter((item) => item.usage_status === "verified").length;
  const shownCount = nonNegativeInteger(
    receipt.shown_count,
    memoryIds.length || items.length,
  );
  const usedCount = nonNegativeInteger(receipt.used_count, derivedUsedCount);
  const verifiedCount = nonNegativeInteger(receipt.verified_count, derivedVerifiedCount);
  const scopeCounts = asRecord(receipt.scope_counts) ?? {};

  if (shownCount <= 0 && usedCount <= 0 && verifiedCount <= 0 && items.length === 0) return null;
  return {
    legacySummary: !("shown_count" in receipt || "verified_count" in receipt),
    shownCount,
    usedCount,
    verifiedCount,
    projectCount: nonNegativeInteger(scopeCounts.project),
    userCount: nonNegativeInteger(scopeCounts.user),
    professionalCount: nonNegativeInteger(scopeCounts.professional),
    items,
  };
}

export function knowledgeReceiptUsageLabel(status: KnowledgeReceiptUsageStatus): string {
  switch (status) {
    case "used":
      return "已采用";
    case "verified":
      return "已验证";
    case "blocked":
      return "已拦截";
    case "ignored":
      return "已忽略";
    case "shown":
    default:
      return "已展示";
  }
}
