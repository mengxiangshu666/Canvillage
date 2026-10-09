import type { FreezoneTextPrepareResult } from "@/api/freezoneTextPrepare";
import type {
  TextAnnotationNodeData,
  TextPreparePreview,
} from "@/features/canvas/domain/canvasNodes";

export function buildTextPreparePreview(input: {
  result: FreezoneTextPrepareResult;
  jobId: string;
  taskKey: string;
  createdAt?: number;
}): TextPreparePreview {
  const { result } = input;
  return {
    preparedText: result.prepared_text,
    sourceText: result.source_text,
    sourceHash: result.source_hash,
    mode: result.mode,
    model: result.model,
    jobId: input.jobId,
    taskKey: input.taskKey,
    changeSummary: result.change_summary ?? [],
    unresolved: result.unresolved ?? [],
    warnings: result.warnings ?? [],
    createdAt: input.createdAt ?? Date.now(),
  };
}

export function isTextPreparePreviewStale(
  preview: Pick<TextPreparePreview, "sourceText"> | null | undefined,
  currentContent: string,
): boolean {
  if (!preview) return false;
  return preview.sourceText !== currentContent;
}

export function buildTextPrepareApplyPatch(
  preview: Pick<TextPreparePreview, "sourceText" | "preparedText"> | null | undefined,
  currentContent: string,
): Partial<TextAnnotationNodeData> | null {
  if (!preview || isTextPreparePreviewStale(preview, currentContent)) return null;
  return {
    content: preview.preparedText,
    textPreparePreview: null,
    textPrepareError: null,
  };
}
