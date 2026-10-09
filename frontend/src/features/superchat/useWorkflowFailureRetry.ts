// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  useCallback,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { toast } from "sonner";

import { commandWorkflowRun } from "@/api/workflow-runtime";
import type { WorkflowItemSelectionGroup } from "@/features/superchat/freezone-canvas-agent-ui";
import { mergeWorkflowRun } from "@/features/superchat/workflow-run-live";
import type { WorkflowRun, WorkflowRunCommand } from "@/types/workflow-runtime";

/**
 * Retry a failed atomic step as a whole.
 *
 * `quality_review` is atomic, so its only retry unit is the whole step; failed
 * item retry would be rejected by the runtime for this step.
 */
export function buildWorkflowStepRetryCommand(
  run: WorkflowRun,
  stepId: string,
): WorkflowRunCommand {
  const normalizedStepId = stepId.trim();
  return {
    command: "retry",
    step_id: normalizedStepId,
    retry_scope: "whole_step",
    idempotency_key: `ui-step-retry:${run.id}:${run.revision}:${normalizedStepId}`,
    expected_revision: run.revision,
  };
}

export function useWorkflowFailureRetry(options: {
  projectId: string;
  run: WorkflowRun | null;
  setRun: Dispatch<SetStateAction<WorkflowRun | null>>;
}) {
  const { projectId, run, setRun } = options;
  const [retryingItems, setRetryingItems] = useState(false);
  const [retryingStepId, setRetryingStepId] = useState<string | null>(null);

  const retryItems = useCallback(
    async (groups: WorkflowItemSelectionGroup[]) => {
      if (!projectId || !run || groups.length === 0 || retryingItems) return;
      let currentRun = run;
      let retriedCount = 0;
      setRetryingItems(true);
      try {
        for (const group of groups) {
          const itemIds = [
            ...new Set(
              group.itemIds.map((itemId) => itemId.trim()).filter(Boolean),
            ),
          ];
          if (!group.stepId || itemIds.length === 0) continue;
          const updated = await commandWorkflowRun(projectId, currentRun.id, {
            command: "retry",
            step_id: group.stepId,
            retry_scope: "failed_items_only",
            item_ids: itemIds,
            idempotency_key: `ui-item-retry:${currentRun.id}:${currentRun.revision}:${group.stepId}:${itemIds.join(",")}`,
            expected_revision: currentRun.revision,
          });
          currentRun = mergeWorkflowRun(currentRun, updated) ?? updated;
          retriedCount += itemIds.length;
          setRun((current) => mergeWorkflowRun(current, updated));
        }
        if (retriedCount > 0) toast.success(`已提交 ${retriedCount} 个失败项重试`);
      } catch (error) {
        const prefix = retriedCount > 0 ? `已提交 ${retriedCount} 个；` : "";
        toast.error(
          `${prefix}其余失败项重试未提交：${error instanceof Error ? error.message : String(error)}`,
        );
      } finally {
        setRetryingItems(false);
      }
    },
    [projectId, retryingItems, run, setRun],
  );

  const retryStep = useCallback(
    async (stepId: string) => {
      const normalizedStepId = stepId.trim();
      if (!projectId || !run || !normalizedStepId || retryingStepId) return;
      setRetryingStepId(normalizedStepId);
      try {
        const updated = await commandWorkflowRun(
          projectId,
          run.id,
          buildWorkflowStepRetryCommand(run, normalizedStepId),
        );
        setRun((current) => mergeWorkflowRun(current, updated));
        toast.success("已提交整步重试");
      } catch (error) {
        toast.error(
          `整步重试未提交：${error instanceof Error ? error.message : String(error)}`,
        );
      } finally {
        setRetryingStepId(null);
      }
    },
    [projectId, retryingStepId, run, setRun],
  );

  return { retryingItems, retryingStepId, retryItems, retryStep };
}
