import type { FreezoneCanvasPayload } from "@/api/canvas";
import type {
  WorkflowCanvasAssetBindingCommandRequest,
  WorkflowCanvasAssetBindingReadinessResult,
  WorkflowCanvasAssetBindingRepairResult,
  WorkflowRun,
} from "@/types/workflow-runtime";
import {
  workflowRecoveryFromRun,
  type WorkflowStepRecovery,
} from "@/features/superchat/workflow-failure-dismissal";

const REPAIR_SCHEMA = "workflow_canvas_asset_binding_repair.v1";
const READINESS_SCHEMA = "workflow_canvas_asset_binding_readiness.v1";
const MEDIA_AUTHORIZATION_SCHEMA = "workflow_media_authorization_request.v1";
const SOURCE_ACTION = "repair_canvas_asset_binding";
const SOURCE_ERROR_CODE = "workflow_storyboard_canvas_asset_ambiguous";

export interface WorkflowAssetBindingRecoveryDependencies {
  flushCanvas: (project: string, canvas: string) => Promise<boolean | null>;
  repairBinding: (
    project: string,
    runId: string,
    payload: WorkflowCanvasAssetBindingCommandRequest,
  ) => Promise<WorkflowCanvasAssetBindingRepairResult>;
  getCanvas: (project: string, canvas: string) => Promise<FreezoneCanvasPayload>;
  applyRemoteCanvas: (
    project: string,
    canvas: string,
    remote: FreezoneCanvasPayload,
  ) => boolean;
  revalidateBinding: (
    project: string,
    runId: string,
    payload: WorkflowCanvasAssetBindingCommandRequest,
  ) => Promise<WorkflowCanvasAssetBindingReadinessResult>;
  getRun: (project: string, runId: string) => Promise<WorkflowRun>;
  focusNodes: (nodeIds: readonly string[]) => boolean;
}

export interface WorkflowAssetBindingRecoveryInput {
  projectId: string;
  run: WorkflowRun;
  repairCommandId: string;
  revalidateCommandId: string;
}

export type WorkflowAssetBindingRecoveryOutcome =
  | { status: "authorization_required"; run: WorkflowRun }
  | {
    status: "not_ready";
    readiness: WorkflowCanvasAssetBindingReadinessResult;
  };

function nonEmptyString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(nonEmptyString).filter(Boolean))];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function assertCommandId(value: string, label: string): void {
  if (!value.trim()) throw new Error(`${label} command id 不能为空`);
}

function assertSourceContract(input: WorkflowAssetBindingRecoveryInput): WorkflowStepRecovery {
  const projectId = input.projectId.trim();
  if (!projectId || input.run.project_id !== projectId) {
    throw new Error("WorkflowRun 与当前项目不一致");
  }
  if (input.run.status !== "failed") {
    throw new Error("只有失败的 WorkflowRun 可以整理重复资产绑定");
  }
  const recovery = workflowRecoveryFromRun(input.run);
  if (
    recovery?.schema !== "workflow_step_recovery.v1"
    || recovery.action !== SOURCE_ACTION
    || recovery.error_code !== SOURCE_ERROR_CODE
    || recovery.requires_paid_media !== false
    || recovery.auto_retry_allowed !== false
  ) {
    throw new Error("当前失败步骤不是可安全整理的重复资产绑定");
  }
  if (!nonEmptyString(recovery.workflow_run_id) || recovery.workflow_run_id !== input.run.id) {
    throw new Error("恢复合同与当前 WorkflowRun 不一致");
  }
  if (!recovery.asset_ids?.length || !recovery.target_node_ids?.length) {
    throw new Error("恢复合同缺少资产或目标节点范围");
  }
  return recovery;
}

function assertRepairResult(
  result: WorkflowCanvasAssetBindingRepairResult,
  input: WorkflowAssetBindingRecoveryInput,
): void {
  if (
    result.schema !== REPAIR_SCHEMA
    || !["repaired", "idempotent_replay"].includes(result.status)
    || result.run_id !== input.run.id
    || result.step_id !== workflowRecoveryFromRun(input.run)?.step_id
    || result.run_revision !== input.run.revision
    || result.media_replay_started !== false
    || result.next_action !== "revalidate_readiness"
  ) {
    throw new Error("服务端资产整理回执与当前 Run 不一致");
  }
}

function resolvedNodeIdsFromCanvas(
  recovery: WorkflowStepRecovery,
  remote: FreezoneCanvasPayload,
): string[] {
  const targetNodeIds = new Set(recovery.target_node_ids ?? []);
  const nodes = Array.isArray(remote.nodes)
    ? remote.nodes.filter(isRecord)
    : [];
  const resolved: string[] = [];
  for (const assetId of recovery.asset_ids ?? []) {
    const bound = nodes.filter((node) => (
      targetNodeIds.has(nonEmptyString(node.id))
      && nonEmptyString(isRecord(node.data) ? node.data.scriptAssetId : "") === assetId
    ));
    const nodeId = bound.length === 1 ? nonEmptyString(bound[0].id) : "";
    if (nodeId && !resolved.includes(nodeId)) {
      resolved.push(nodeId);
    }
  }
  return resolved;
}

function assertReadinessResult(
  result: WorkflowCanvasAssetBindingReadinessResult,
  input: WorkflowAssetBindingRecoveryInput,
): void {
  if (
    result.schema !== READINESS_SCHEMA
    || result.run_id !== input.run.id
    || result.step_id !== workflowRecoveryFromRun(input.run)?.step_id
    || result.media_submission_started !== false
  ) {
    throw new Error("服务端资产复验回执与当前 Run 不一致");
  }
  if (result.status === "not_ready") {
    if (result.ready !== false) {
      throw new Error("not_ready 复验回执的 ready 必须为 false");
    }
    return;
  }
  const authorization = result.authorization_request;
  if (
    !["authorization_required", "idempotent_replay"].includes(result.status)
    || result.ready !== true
    || !authorization
    || authorization.schema !== MEDIA_AUTHORIZATION_SCHEMA
    || authorization.run_id !== input.run.id
    || authorization.step_id !== result.step_id
    || authorization.requires_user_action !== true
  ) {
    throw new Error("资产复验通过但没有生成可用的媒体授权交接");
  }
}

export async function repairAndRevalidateWorkflowAssetBinding(
  input: WorkflowAssetBindingRecoveryInput,
  dependencies: WorkflowAssetBindingRecoveryDependencies,
): Promise<WorkflowAssetBindingRecoveryOutcome> {
  const recovery = assertSourceContract(input);
  assertCommandId(input.repairCommandId, "repair");
  assertCommandId(input.revalidateCommandId, "revalidate");

  const flushed = await dependencies.flushCanvas(
    input.projectId,
    input.run.canvas_id,
  );
  if (flushed !== true) {
    throw new Error("画布还有未保存修改，已停止服务端资产整理");
  }

  const request: WorkflowCanvasAssetBindingCommandRequest = {
    canvas_id: input.run.canvas_id,
    step_id: recovery.step_id,
    command_id: input.repairCommandId,
    expected_run_revision: input.run.revision,
    ...(input.run.source_turn_id
      ? { source_turn_id: input.run.source_turn_id }
      : {}),
  };
  const repaired = await dependencies.repairBinding(
    input.projectId,
    input.run.id,
    request,
  );
  assertRepairResult(repaired, input);

  const remote = await dependencies.getCanvas(
    input.projectId,
    input.run.canvas_id,
  );
  const applied = dependencies.applyRemoteCanvas(
    input.projectId,
    input.run.canvas_id,
    remote,
  );
  if (!applied) {
    throw new Error("当前画布运行态未接入，无法对齐服务端整理结果");
  }

  const keptNodeIds = stringArray(repaired.kept_node_ids);
  dependencies.focusNodes(
    keptNodeIds.length > 0
      ? keptNodeIds
      : resolvedNodeIdsFromCanvas(recovery, remote),
  );

  const revalidated = await dependencies.revalidateBinding(
    input.projectId,
    input.run.id,
    {
      ...request,
      command_id: input.revalidateCommandId,
    },
  );
  assertReadinessResult(revalidated, input);
  if (revalidated.status === "not_ready") {
    return { status: "not_ready", readiness: revalidated };
  }

  const updated = await dependencies.getRun(input.projectId, input.run.id);
  if (updated.id !== input.run.id) {
    throw new Error("服务端返回了错误的 WorkflowRun");
  }
  return { status: "authorization_required", run: updated };
}
