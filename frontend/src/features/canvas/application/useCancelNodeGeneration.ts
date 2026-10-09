// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useState } from "react";
import { toast } from "sonner";

import {
  cancelProjectTask,
  cancelTaskMonitoring,
  TaskCompletionError,
} from "@/api/tasks";
import type { FreezoneJobRef } from "@/api/ops";
import {
  generationTaskDescriptor,
  generationTaskRefsFromNode,
  persistedGenerationTaskRef,
  type PersistedGenerationTaskRef,
} from "@/features/canvas/application/resumeGeneration";
import { readUrl } from "@/lib/url-params";
import { useCanvasStore } from "@/stores/canvasStore";
import { CLEARED_GENERATION_TASK_PATCH } from "@/features/canvas/application/generationTaskArbitration";

export const NODE_GENERATION_CLEARED_PATCH = {
  ...CLEARED_GENERATION_TASK_PATCH,
  generationError: null,
  generationErrorDetails: null,
  generationErrorRequestId: null,
} as const;

export function nodeTaskCancellationError(taskKey: string): TaskCompletionError {
  return new TaskCompletionError("任务已由用户终止", "cancelled", taskKey || "node-generation");
}

/** Add one submitted job without losing sibling jobs from a batch generation. */
export function registerNodeGenerationTask(
  nodeId: string,
  ref: FreezoneJobRef,
  options: { primary?: boolean } = {},
): void {
  const store = useCanvasStore.getState();
  const node = store.nodes.find((item) => item.id === nodeId);
  const refs = generationTaskRefsFromNode(node?.data);
  const nextRef = persistedGenerationTaskRef(ref);
  if (!refs.some((item) => item.taskKey === nextRef.taskKey)) refs.push(nextRef);

  store.updateNodeData(nodeId, {
    ...(options.primary || refs.length === 1 ? generationTaskDescriptor(ref) : {}),
    generationTaskRefs: refs,
  });
}

/** If cancellation won while submit was still in flight, cancel the late job too. */
export async function cancelSubmittedTaskIfAborted(
  projectId: string,
  ref: FreezoneJobRef,
  signal?: AbortSignal,
): Promise<void> {
  if (!signal?.aborted) return;
  await cancelProjectTask({
    projectId,
    taskType: ref.task_type,
    scope: ref.job_id,
  });
  cancelTaskMonitoring(ref.task_key);
  throw signal.reason instanceof Error
    ? signal.reason
    : nodeTaskCancellationError(ref.task_key);
}

export function useCancelNodeGeneration(
  nodeId: string,
  data: unknown,
  onCancelLocal?: (reason: TaskCompletionError) => void,
  options: { clearPatch?: Record<string, unknown> } = {},
) {
  const [isCancelling, setIsCancelling] = useState(false);
  const canCancel = generationTaskRefsFromNode(data).length > 0;
  const clearPatch = options.clearPatch;

  const cancel = useCallback(async () => {
    if (isCancelling) return;
    const projectId = readUrl().project;
    if (!projectId) {
      toast.error("当前画布缺少项目标识，无法终止任务");
      return;
    }

    const latestNode = useCanvasStore.getState().nodes.find((node) => node.id === nodeId);
    const refs = generationTaskRefsFromNode(latestNode?.data ?? data);
    const reason = nodeTaskCancellationError(refs[0]?.taskKey ?? "");
    setIsCancelling(true);
    // Stop local queue workers and result probing immediately. Late submits are
    // handled by cancelSubmittedTaskIfAborted when they return from the server.
    onCancelLocal?.(reason);

    try {
      // Read once more after aborting local work so a just-finished concurrent
      // submit is included in this same cancellation click.
      const latest = useCanvasStore.getState().nodes.find((node) => node.id === nodeId);
      const currentRefs = generationTaskRefsFromNode(latest?.data ?? data);
      const refsToCancel = currentRefs.length > 0 ? currentRefs : refs;
      await Promise.all(
        refsToCancel.map((ref: PersistedGenerationTaskRef) =>
          cancelProjectTask({
            projectId,
            taskType: ref.taskType,
            scope: ref.jobId,
          }),
        ),
      );
      refsToCancel.forEach((ref) => cancelTaskMonitoring(ref.taskKey));
      useCanvasStore.getState().updateNodeData(nodeId, {
        ...NODE_GENERATION_CLEARED_PATCH,
        ...clearPatch,
      });
      toast.success("节点任务已终止，可重新生成");
    } catch (error) {
      toast.error(error instanceof Error ? `终止任务失败：${error.message}` : "终止任务失败");
    } finally {
      setIsCancelling(false);
    }
  }, [clearPatch, data, isCancelling, nodeId, onCancelLocal]);

  return { cancel, isCancelling, canCancel };
}
