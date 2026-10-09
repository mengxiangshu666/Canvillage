import type { Dispatch, SetStateAction } from "react";

import type { WorkflowRun } from "@/types/workflow-runtime";
import type {
  CanvasAgentNodeRef,
  CanvasAgentSkill,
} from "@/features/superchat/canvas-agent-skills";
import type { CanvasAgentRunMode } from "@/features/superchat/canvas-agent-run-mode";
import { useWorkflowAssetBindingRepair } from "@/features/superchat/use-workflow-asset-binding-repair";
import { useWorkflowRecoveryAuthorizations } from "@/features/superchat/use-workflow-recovery-authorizations";

export function useWorkflowCanvasRecoveryActions(input: {
  projectId: string;
  run: WorkflowRun | null;
  setRun: Dispatch<SetStateAction<WorkflowRun | null>>;
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
  const repair = useWorkflowAssetBindingRepair({
    projectId: input.projectId,
    run: input.run,
    setRun: input.setRun,
    busy: input.busy,
  });
  const authorization = useWorkflowRecoveryAuthorizations(input);
  return { ...repair, ...authorization };
}
