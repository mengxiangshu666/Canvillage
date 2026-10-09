// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function read(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf8");
}

describe("VideoNode error notification contract", () => {
  it("shows the plain error dialog without a face-detection shortcut", () => {
    const source = read(
      "src/features/canvas/nodes/useVideoGenerationSubmission.ts",
    );

    // 真人素材被拦截的专用引导已移除：失败原因照常进「查看详情」，
    // 不再让用户去找一个手动开关。
    expect(source).not.toContain("InputImageSensitiveContentDetected");
    expect(source).not.toContain("真人素材审核");
    expect(source).toContain("resolveErrorContent(firstError");
    expect(source).toContain("diagnostics.details ?? undefined");
    expect(source).toContain('t("common.error")');
  });

  it("enables provider human review by default instead of a manual switch", () => {
    const nodeSource = read("src/features/canvas/nodes/VideoNode.tsx");
    const source = read(
      "src/features/canvas/nodes/useVideoGenerationSubmission.ts",
    );

    expect(nodeSource).not.toContain("真人验证");
    expect(nodeSource).toContain("const humanReview = isSeedance20Model;");
    expect(source).toContain("humanReview: isSeedance20Model,");
  });

  it("keeps the local channel gate and unbounded queue integration", () => {
    const nodeSource = read("src/features/canvas/nodes/VideoNode.tsx");
    const source = read(
      "src/features/canvas/nodes/useVideoGenerationSubmission.ts",
    );

    expect(nodeSource).toContain("useVideoGenerationSubmission({");
    expect(nodeSource).toContain("selectedVideoModel?.disabledReason?.trim()");
    expect(source).toContain("const total = clampGenerationBatchCount(count)");
    expect(source).toContain("await runGenerationQueue(");
    expect(source).toContain("withGlobalGenerationSlot(");
    expect(source).toContain("abortController.signal");
    expect(source).toContain("generationQueueTotal: total");
  });
});
