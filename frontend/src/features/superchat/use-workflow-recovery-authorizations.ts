import { useCallback, useState } from "react";
import { toast } from "sonner";

import { issueWorkflowComposeAuthorization } from "@/api/workflow-runtime";
import type { WorkflowRun } from "@/types/workflow-runtime";

import type {
  CanvasAgentNodeRef,
  CanvasAgentSkill,
} from "@/features/superchat/canvas-agent-skills";
import type { CanvasAgentRunMode } from "@/features/superchat/canvas-agent-run-mode";
import { buildWorkflowComposeContinuation } from "@/features/superchat/workflow-compose-authorization";
import {
  workflowComposeAuthorizationRequestFromRun,
  workflowMediaAuthorizationRequestFromRun,
} from "@/features/superchat/workflow-failure-dismissal";
import { buildWorkflowMediaContinuation } from "@/features/superchat/workflow-media-authorization";

type MediaStepId = "storyboard_images" | "shot_videos";

export function useWorkflowRecoveryAuthorizations(input: {
  projectId: string;
  run: WorkflowRun | null;
  canvasContext: string;
  skillIds: readonly string[];
  pinnedNodes: readonly CanvasAgentNodeRef[];
  runMode: CanvasAgentRunMode;
  additionalSkills: readonly CanvasAgentSkill[];
  connected: boolean;
  busy: boolean;
  send: (
    displayText: string,
    attachments: never[],
    transportText: string,
    options: { engine: "village" },
  ) => boolean;
}) {
  const [authorizingCompose, setAuthorizingCompose] = useState(false);
  const [authorizingMediaStep, setAuthorizingMediaStep] = useState<MediaStepId | null>(null);

  const authorizeCompose = useCallback(async () => {
    const run = input.run;
    const request = workflowComposeAuthorizationRequestFromRun(run);
    if (!input.projectId || !run || !request || authorizingCompose) return;
    if (!input.connected || input.busy) {
      toast.error("小树当前不可发起恢复回合；请等待当前回合结束后再授权");
      return;
    }
    setAuthorizingCompose(true);
    try {
      const ticket = await issueWorkflowComposeAuthorization(input.projectId, run.id, {
        canvas_id: run.canvas_id,
        step_id: request.step_id,
      });
      if (
        !ticket.id
        || ticket.run_id !== run.id
        || ticket.step_id !== request.step_id
        || ticket.source_result_signature !== request.source_result_signature
      ) {
        throw new Error("最终合成票据与当前 Run 输入不一致");
      }
      const continuation = buildWorkflowComposeContinuation({
        run,
        composeAuthorizationId: ticket.id,
        canvasContext: input.canvasContext,
        skillIds: input.skillIds,
        pinnedNodes: input.pinnedNodes,
        runMode: input.runMode,
        additionalSkills: input.additionalSkills,
      });
      if (!input.send(
        continuation.displayText,
        [],
        continuation.transportText,
        { engine: "village" },
      )) {
        throw new Error("小树恢复回合未发送");
      }
      toast.success("已授权最终合成，正在恢复原工作流");
    } catch (error) {
      toast.error(
        `最终合成授权未发送：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setAuthorizingCompose(false);
    }
  }, [authorizingCompose, input]);

  const authorizeMedia = useCallback(async (
    stepId: MediaStepId,
    itemIds?: readonly string[],
  ) => {
    const run = input.run;
    const request = workflowMediaAuthorizationRequestFromRun(run);
    if (!input.projectId || !run || !request || request.step_id !== stepId) return;
    if (authorizingMediaStep) return;
    if (!input.connected || input.busy) {
      toast.error("小树当前不可发起恢复回合；请等待当前回合结束后再授权");
      return;
    }
    setAuthorizingMediaStep(stepId);
    try {
      const continuation = buildWorkflowMediaContinuation({
        run,
        stepId,
        itemIds,
        canvasContext: input.canvasContext,
        skillIds: input.skillIds,
        pinnedNodes: input.pinnedNodes,
        additionalSkills: input.additionalSkills,
      });
      if (!input.send(
        continuation.displayText,
        [],
        continuation.transportText,
        { engine: "village" },
      )) {
        throw new Error("小树恢复回合未发送");
      }
      toast.success(
        stepId === "storyboard_images"
          ? "已授权分镜出图，正在恢复原工作流"
          : "已授权逐镜视频，正在恢复原工作流",
      );
    } catch (error) {
      toast.error(
        `媒体授权未发送：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setAuthorizingMediaStep(null);
    }
  }, [authorizingMediaStep, input]);

  return {
    authorizingCompose,
    authorizingMediaStep,
    authorizeCompose,
    authorizeMedia,
  };
}
