import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { toast } from "sonner";

import { getFreezoneCanvas } from "@/api/canvas";
import {
  getWorkflowRun,
  repairWorkflowCanvasAssetBinding,
  revalidateWorkflowCanvasAssetBinding,
} from "@/api/workflow-runtime";
import {
  applyRemoteFreezoneCanvas,
  flushFreezoneCanvasRuntime,
} from "@/lib/freezone-canvas-runtime";
import { focusWorkflowCanvasNodes } from "@/features/superchat/workflow-canvas-focus";
import { repairAndRevalidateWorkflowAssetBinding } from "@/features/superchat/workflow-asset-binding-recovery";
import { mergeWorkflowRun } from "@/features/superchat/workflow-run-live";
import type { WorkflowRun } from "@/types/workflow-runtime";

interface RepairAttempt {
  key: string;
  repairCommandId: string;
  revalidateCommandId: string;
}

function requestId(prefix: string): string {
  const suffix = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  return `${prefix}:${suffix}`;
}

export function useWorkflowAssetBindingRepair(input: {
  projectId: string;
  run: WorkflowRun | null;
  setRun: Dispatch<SetStateAction<WorkflowRun | null>>;
  busy: boolean;
}) {
  const [repairing, setRepairing] = useState(false);
  const attemptRef = useRef<RepairAttempt | null>(null);

  const repair = useCallback(async () => {
    const run = input.run;
    const projectId = input.projectId.trim();
    if (!projectId || !run || repairing) return;
    if (input.busy) {
      toast.error("小树正在执行当前回合；请等待结束后再整理画布");
      return;
    }

    const attemptKey = `${run.id}:${run.revision}:${run.canvas_id}`;
    if (attemptRef.current?.key !== attemptKey) {
      attemptRef.current = {
        key: attemptKey,
        repairCommandId: requestId("ui:asset-binding-repair"),
        revalidateCommandId: requestId("ui:asset-binding-revalidate"),
      };
    }
    const attempt = attemptRef.current;
    if (!attempt) return;

    setRepairing(true);
    try {
      const outcome = await repairAndRevalidateWorkflowAssetBinding(
        {
          projectId,
          run,
          repairCommandId: attempt.repairCommandId,
          revalidateCommandId: attempt.revalidateCommandId,
        },
        {
          flushCanvas: flushFreezoneCanvasRuntime,
          repairBinding: repairWorkflowCanvasAssetBinding,
          getCanvas: getFreezoneCanvas,
          applyRemoteCanvas: applyRemoteFreezoneCanvas,
          revalidateBinding: revalidateWorkflowCanvasAssetBinding,
          getRun: getWorkflowRun,
          focusNodes: focusWorkflowCanvasNodes,
        },
      );
      if (outcome.status === "not_ready") {
        toast.error("重复绑定已整理，但资产仍未通过只读复验，未进入付费授权");
        return;
      }
      attemptRef.current = null;
      input.setRun((current) => mergeWorkflowRun(current, outcome.run));
      toast.success("重复绑定已由服务端整理，现已等待媒体授权");
    } catch (error) {
      toast.error(
        `资产绑定整理未完成：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setRepairing(false);
    }
  }, [input, repairing]);

  return {
    repairingWorkflowAssetBindings: repairing,
    repairWorkflowAssetBindings: repair,
  };
}
