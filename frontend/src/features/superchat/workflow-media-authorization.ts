import type { WorkflowRun } from "@/types/workflow-runtime";

import {
  buildCanvasAgentRequest,
  type CanvasAgentNodeRef,
  type CanvasAgentSkill,
} from "@/features/superchat/canvas-agent-skills";
import { canvasWorkflowRuntimeContextFromRun } from "@/features/superchat/canvas-workflow-fast-start";
import {
  workflowMediaAuthorizationRequestFromRun,
  type WorkflowMediaAuthorizationRequest,
} from "@/features/superchat/workflow-failure-dismissal";

export const WORKFLOW_MEDIA_AUTHORIZATION_DISPLAY_TEXT =
  "已授权付费媒体，小树继续恢复当前步骤。";

export function buildWorkflowMediaContinuation(input: {
  run: WorkflowRun;
  stepId: WorkflowMediaAuthorizationRequest["step_id"];
  itemIds?: readonly string[];
  canvasContext: string;
  skillIds: readonly string[];
  pinnedNodes?: readonly CanvasAgentNodeRef[];
  additionalSkills?: readonly CanvasAgentSkill[];
}): { displayText: string; transportText: string } {
  const request = workflowMediaAuthorizationRequestFromRun(input.run);
  if (!request || request.step_id !== input.stepId) {
    throw new Error("workflow media authorization request is required");
  }
  const itemIds = request.retry_scope === "failed_items_only"
    ? [
      ...new Set(
        (input.itemIds ?? request.item_ids)
          .map((itemId) => itemId.trim())
          .filter(Boolean),
      ),
    ]
    : [];
  if (
    request.retry_scope === "failed_items_only"
    && (
      itemIds.length === 0
      || itemIds.some((itemId) => !request.item_ids.includes(itemId))
    )
  ) {
    throw new Error("workflow failed-item authorization scope is invalid");
  }
  const stepLabel = request.media_kind === "image" ? "分镜出图" : "逐镜视频";
  const retryInstruction = request.retry_scope === "failed_items_only"
    ? `只重试这些原失败项：${itemIds.join("、")}。`
    : "仅恢复这个原 Run 的当前步骤。";
  return {
    displayText: WORKFLOW_MEDIA_AUTHORIZATION_DISPLAY_TEXT,
    transportText: buildCanvasAgentRequest({
      userText: [
        `已授权付费媒体。重试并恢复 WorkflowRun ${input.run.id} 的 ${stepLabel}。`,
        `只使用本轮服务端 grant 消费 ${request.step_id} 的`,
        `${request.recovery_action} 精确恢复授权，再把同一 consume key 作为一次性 marker`,
        "回传 WorkflowRuntime。",
        retryInstruction,
        "不得另开 WorkflowRun，不得改跑其他步骤，也不得扩大 item 范围。",
      ].join(""),
      executionLane: "canvas_execute",
      skillIds: input.skillIds,
      explicitSkillIds: input.skillIds,
      additionalSkills: input.additionalSkills,
      maxPaidStarts: 1,
      canvasContext: input.canvasContext,
      pinnedNodes: input.pinnedNodes,
      runMode: "auto",
      workflowRuntime: canvasWorkflowRuntimeContextFromRun(input.run),
      forceStructured: true,
    }),
  };
}
