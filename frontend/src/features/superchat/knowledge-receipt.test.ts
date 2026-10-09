// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { describe, expect, it } from "vitest";

import {
  knowledgeReceiptFromMessage,
  knowledgeReceiptUsageLabel,
} from "./knowledge-receipt";

describe("knowledge receipt", () => {
  it("keeps shown, used, and verified counters distinct", () => {
    const result = knowledgeReceiptFromMessage({
      raw: {
        metadata: {
          knowledge_receipt: {
            shown_count: 3,
            used_count: 1,
            verified_count: 1,
            scope_counts: { project: 2, user: 1, professional: 0 },
            items: [
              { memory_id: 1, title: "规则", usage_status: "verified" },
              { memory_id: 2, title: "背景", usage_status: "shown" },
            ],
          },
        },
      },
    });

    expect(result).toMatchObject({
      shownCount: 3,
      usedCount: 1,
      verifiedCount: 1,
      projectCount: 2,
      userCount: 1,
    });
    expect(result?.items.map((item) => item.usage_status)).toEqual(["verified", "shown"]);
  });

  it("derives counters for older receipts without explicit summary fields", () => {
    const result = knowledgeReceiptFromMessage({
      raw: {
        metadata: {
          knowledge_receipt: {
            memory_ids: [1, 2, 3],
            items: [
              { memory_id: 1, title: "采用", usage_status: "used" },
              { memory_id: 2, title: "核验", usage_status: "verified" },
              { memory_id: 3, title: "展示", usage_status: "shown" },
            ],
          },
        },
      },
    });

    expect(result).toMatchObject({ shownCount: 3, usedCount: 2, verifiedCount: 1 });
  });

  it("keeps legacy used_memory_ids meaningful when item statuses are absent", () => {
    const result = knowledgeReceiptFromMessage({
      raw: {
        metadata: {
          knowledge_receipt: {
            memory_ids: [1, 2],
            used_memory_ids: [1],
            items: [
              { memory_id: 1, title: "旧规则" },
              { memory_id: 2, title: "旧背景" },
            ],
          },
        },
      },
    });

    expect(result?.items.map((item) => item.usage_status)).toEqual(["used", "shown"]);
    expect(result?.usedCount).toBe(1);
  });

  it("accepts shown-only receipts and rejects empty receipts", () => {
    expect(knowledgeReceiptFromMessage({
      raw: { metadata: { knowledge_receipt: { shown_count: 2, used_count: 0 } } },
    })).toMatchObject({ shownCount: 2, usedCount: 0 });
    expect(knowledgeReceiptFromMessage({
      raw: { metadata: { knowledge_receipt: { shown_count: 0, used_count: 0 } } },
    })).toBeNull();
  });

  it("uses explicit labels for each influence status", () => {
    expect(knowledgeReceiptUsageLabel("shown")).toBe("已展示");
    expect(knowledgeReceiptUsageLabel("used")).toBe("已采用");
    expect(knowledgeReceiptUsageLabel("verified")).toBe("已验证");
    expect(knowledgeReceiptUsageLabel("blocked")).toBe("已拦截");
    expect(knowledgeReceiptUsageLabel("ignored")).toBe("已忽略");
  });
});
