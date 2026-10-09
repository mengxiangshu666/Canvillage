// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function read(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf8");
}

describe("ImageGenNode error notification contract", () => {
  it("keeps local cancellation out of batch progress and recovery failure writes", () => {
    const source = read("src/features/canvas/nodes/ImageGenNode.tsx");
    const start = source.indexOf("const settledResults = await runGenerationQueue(");
    const end = source.indexOf("const latestNodeData", start);
    const settling = source.slice(start, end);
    expect(start).toBeGreaterThan(0);
    expect(settling).toContain("if (!abortController.signal.aborted)");
    expect(settling).toContain("if (abortController.signal.aborted) return;");
  });
  it("stores the raw error separately from the displayed provider message", () => {
    const source = read("src/features/canvas/nodes/ImageGenNode.tsx");

    expect(source).toContain("generationError: displayErrorMessage");
    expect(source).toContain("generationErrorDetails: rawErrorMessage");
    expect(source).toContain("generationErrorRequestId: extractRequestId(rawErrorMessage)");
  });

  it("copies the preserved raw ImageGen error from the node toolbar", () => {
    const source = read("src/features/canvas/ui/NodeActionToolbar.tsx");

    expect(source).toContain("isExportImageNode(node) || isImageGenNode(node)");
    expect(source).toContain("return generationErrorDetails || generationError");
  });

  it("renders failures through the shared human-readable error card", () => {
    const source = read("src/features/canvas/nodes/ImageGenNode.tsx");

    expect(source).toContain("<NodeGenerationErrorCard");
    expect(source).toContain("details={generationErrorDetails}");
    expect(source).toContain("requestId={generationErrorRequestId}");
    expect(source).toContain("setPanelExpanded(true)");
  });

  it("keeps a timed-out image job queryable without resubmitting it", () => {
    const source = read("src/features/canvas/nodes/ImageGenNode.tsx");

    expect(source).toContain("fetchFreezoneJobResultWithRetry");
    expect(source).toContain("const handleRecoverImageTask = useCallback");
    expect(source).toContain("generationRecoveryJobId: taskJobId");
    expect(source).toContain("generationRecoveryJobId: null");
    expect(source).toContain("generationRecoveryTaskType: null");
    expect(source).toContain("onRecover={canRecoverImageTask ? () => void handleRecoverImageTask() : undefined}");
    expect(source).toContain("buildImageGenerationSuccessPatch(url)");
    const recoveryStart = source.indexOf("const handleRecoverImageTask");
    const submitStart = source.indexOf("const handleSubmit", recoveryStart);
    const recoveryBlock = source.slice(recoveryStart, submitStart);
    expect(recoveryBlock).toMatch(
      /fetchFreezoneJobResultWithRetry[\s\S]*?buildImageGenerationSuccessPatch\(url\)/,
    );
    expect(recoveryBlock).not.toContain("submitFreezoneGen(");
  });

  it("wires the same existing-result recovery into export image nodes", () => {
    const imageNode = read("src/features/canvas/nodes/ImageNode.tsx");
    const canvas = read("src/features/canvas/Canvas.tsx");

    expect(imageNode).toContain("const handleRecoverImageResult = useCallback");
    expect(imageNode).toContain("fetchFreezoneJobResultWithRetry(");
    expect(imageNode).toContain("onRecover={canRecoverImageResult ? () => void handleRecoverImageResult() : undefined}");
    expect(imageNode).toContain("buildImageGenerationSuccessPatch(url)");
    expect(canvas).toContain("generationRecoveryJobId: jobId");
    expect(canvas).toContain("? 'freezone_edit'");
    expect(canvas).toContain(": 'freezone_gen'");

    const recoveryStart = imageNode.indexOf("const handleRecoverImageResult");
    const recoveryEnd = imageNode.indexOf("// Natural pixel size", recoveryStart);
    const recoveryBlock = imageNode.slice(recoveryStart, recoveryEnd);
    expect(recoveryBlock).not.toContain("regenerateExportImageNode(");

    const regenerate = read("src/features/canvas/application/regenerateExportNode.ts");
    expect(regenerate).toContain("generationRecoveryJobId: null");
    expect(regenerate).toContain("generationRecoveryTaskType: null");
  });

  it("keeps the local queue and expression-state behavior while adding diagnostics", () => {
    const source = read("src/features/canvas/nodes/ImageGenNode.tsx");

    expect(source).toContain("const total = clampGenerationBatchCount(effectiveCount)");
    expect(source).toContain("await runGenerationQueue(");
    expect(source).toMatch(
      /withGlobalGenerationSlot\(\s*\(\) => runOne\(runIndex\),/,
    );
    expect(source).toContain("generationQueueTotal: total");
    expect(source).toContain('expression_status: "failed"');
  });

  it("preserves raw diagnostics in export-producing and resumed image flows", () => {
    const imageEdit = read("src/features/canvas/nodes/ImageEditNode.tsx");
    const storyboard = read("src/features/canvas/nodes/StoryboardGenNode.tsx");
    const resume = read("src/features/canvas/application/resumeGeneration.ts");

    for (const source of [imageEdit, storyboard]) {
      expect(source).toContain("resolveGenerationErrorDiagnostics(");
      expect(source).toContain("generationErrorDetails: diagnostics.details");
      expect(source).toContain("generationErrorRequestId: diagnostics.requestId");
    }
    expect(resume).toContain("generationError: providerErrorMessage(rawMessage) ?? rawMessage");
    expect(resume).toContain("const rawDetails = resolved.details ?? rawMessage");
    expect(resume).toContain("generationErrorDetails: diagnostics.details ?? rawDetails");
    expect(resume).toContain("diagnostics.requestId ?? rawRequestId");
  });
});
