import type { WorkflowRun } from "@/types/workflow-runtime";

import {
  buildCanvasAgentRequest,
  type CanvasAgentNodeRef,
  type CanvasAgentSkill,
} from "@/features/superchat/canvas-agent-skills";
import type { CanvasAgentRunMode } from "@/features/superchat/canvas-agent-run-mode";
import { canvasWorkflowRuntimeContextFromRun } from "@/features/superchat/canvas-workflow-fast-start";

export const WORKFLOW_COMPOSE_AUTHORIZATION_DISPLAY_TEXT =
  "已授权最终合成，小树继续恢复当前成片。";

export function buildWorkflowComposeContinuation(input: {
  run: WorkflowRun;
  composeAuthorizationId: string;
  canvasContext: string;
  skillIds: readonly string[];
  pinnedNodes?: readonly CanvasAgentNodeRef[];
  runMode?: CanvasAgentRunMode;
  additionalSkills?: readonly CanvasAgentSkill[];
}): { displayText: string; transportText: string } {
  const composeAuthorizationId = input.composeAuthorizationId.trim();
  if (!composeAuthorizationId) {
    throw new Error("compose authorization id is required");
  }
  return {
    displayText: WORKFLOW_COMPOSE_AUTHORIZATION_DISPLAY_TEXT,
    transportText: buildCanvasAgentRequest({
      userText: [
        `继续恢复 WorkflowRun ${input.run.id} 的 final_film。`,
        "使用本轮服务端签发的一次性合成票据，先消费票据，再只重试原 Run 的原 final_film step。",
        "不要另开 WorkflowRun，不要用自动合成标志替代票据。",
      ].join(""),
      executionLane: "canvas_execute",
      skillIds: input.skillIds,
      explicitSkillIds: input.skillIds,
      additionalSkills: input.additionalSkills,
      composeAuthorizationId,
      canvasContext: input.canvasContext,
      pinnedNodes: input.pinnedNodes,
      runMode: input.runMode,
      workflowRuntime: canvasWorkflowRuntimeContextFromRun(input.run),
      forceStructured: true,
    }),
  };
}
