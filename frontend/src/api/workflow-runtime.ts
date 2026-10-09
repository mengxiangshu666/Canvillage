import { apiCall } from "@/api/client";
import type {
  WorkflowComposeAuthorization,
  WorkflowComposeAuthorizationCreate,
  WorkflowCanvasAssetBindingCommandRequest,
  WorkflowCanvasAssetBindingReadinessResult,
  WorkflowCanvasAssetBindingRepairResult,
  WorkflowDefinition,
  WorkflowRun,
  WorkflowRunCommand,
  WorkflowRunCreate,
  WorkflowRunEventCreate,
  WorkflowRunEventPage,
} from "@/types/workflow-runtime";

const projectPath = (project: string) =>
  `projects/${encodeURIComponent(project)}`;

export async function decideVisualPreflight(project: string, run: WorkflowRun, report: {
  shot_id: string; input_fingerprint: string;
}, decision: "keep_original" | "accept_suggestion"): Promise<WorkflowRun> {
  return apiCall<WorkflowRun>(`${projectPath(project)}/workflow-runs/${encodeURIComponent(run.id)}/visual-preflight-decision`, {
    method: "POST", json: {
      canvas_id: run.canvas_id, shot_id: report.shot_id,
      input_fingerprint: report.input_fingerprint, decision,
      command_id: `visual-decision:${run.id}:${run.revision}:${report.shot_id}:${decision}`,
      expected_run_revision: run.revision,
    },
  });
}

/** Subscribe to server snapshots without triggering canvas or Agent UI effects. */
export function subscribeWorkflowRunSnapshots(options: {
  projectId: string;
  canvasId: string;
  runId: string;
  onRun: (run: WorkflowRun) => void;
  onError: () => void;
}): { close: () => void } {
  if (typeof EventSource === "undefined") return { close: () => undefined };
  const source = new EventSource(
    `/api/v1/${projectPath(options.projectId)}/workflow-runs/${encodeURIComponent(options.runId)}/events/stream`,
    { withCredentials: true },
  );
  const onSnapshot = (event: Event) => {
    try {
      const { run } = JSON.parse((event as MessageEvent).data) as { run?: WorkflowRun };
      if (!run || run.id !== options.runId || run.project_id !== options.projectId ||
          run.canvas_id !== options.canvasId || !Number.isSafeInteger(run.revision) ||
          !Number.isSafeInteger(run.event_seq)) return;
      options.onRun(run);
    } catch {
      options.onError();
    }
  };
  source.addEventListener("workflow.snapshot", onSnapshot);
  source.addEventListener("workflow.terminal", (event) => {
    onSnapshot(event);
    source.close();
  });
  source.addEventListener("error", options.onError);
  return { close: () => source.close() };
}

export async function listWorkflowDefinitions(
  project: string,
  signal?: AbortSignal,
): Promise<WorkflowDefinition[]> {
  return apiCall<WorkflowDefinition[]>(
    `${projectPath(project)}/workflows`,
    { signal },
  );
}

export async function startWorkflowRun(
  project: string,
  payload: WorkflowRunCreate,
): Promise<WorkflowRun> {
  return apiCall<WorkflowRun>(
    `${projectPath(project)}/workflow-runs`,
    { method: "POST", json: payload },
  );
}

export async function recordWorkflowRunEvent(
  project: string,
  runId: string,
  payload: WorkflowRunEventCreate,
): Promise<WorkflowRun> {
  return apiCall<WorkflowRun>(
    `${projectPath(project)}/workflow-runs/${encodeURIComponent(runId)}/events`,
    { method: "POST", json: payload },
  );
}

export async function getWorkflowRun(
  project: string,
  runId: string,
  signal?: AbortSignal,
): Promise<WorkflowRun> {
  return apiCall<WorkflowRun>(
    `${projectPath(project)}/workflow-runs/${encodeURIComponent(runId)}`,
    { signal },
  );
}

export async function listWorkflowRunEvents(
  project: string,
  runId: string,
  afterSeq = 0,
  limit = 200,
  signal?: AbortSignal,
): Promise<WorkflowRunEventPage> {
  const params = new URLSearchParams({
    after_seq: String(afterSeq),
    limit: String(limit),
  });
  return apiCall<WorkflowRunEventPage>(
    `${projectPath(project)}/workflow-runs/${encodeURIComponent(runId)}/events?${params.toString()}`,
    { signal },
  );
}

export async function listWorkflowRuns(
  project: string,
  canvasId: string,
  signal?: AbortSignal,
): Promise<WorkflowRun[]> {
  return apiCall<WorkflowRun[]>(
    `${projectPath(project)}/workflow-runs?canvas_id=${encodeURIComponent(canvasId)}`,
    { signal },
  );
}

export async function commandWorkflowRun(
  project: string,
  runId: string,
  payload: WorkflowRunCommand,
): Promise<WorkflowRun> {
  return apiCall<WorkflowRun>(
    `${projectPath(project)}/workflow-runs/${encodeURIComponent(runId)}/command`,
    { method: "POST", json: payload },
  );
}

export async function issueWorkflowComposeAuthorization(
  project: string,
  runId: string,
  payload: WorkflowComposeAuthorizationCreate,
): Promise<WorkflowComposeAuthorization> {
  return apiCall<WorkflowComposeAuthorization>(
    `${projectPath(project)}/workflow-runs/${encodeURIComponent(runId)}/compose-authorizations`,
    { method: "POST", json: payload },
  );
}

export async function repairWorkflowCanvasAssetBinding(
  project: string,
  runId: string,
  payload: WorkflowCanvasAssetBindingCommandRequest,
): Promise<WorkflowCanvasAssetBindingRepairResult> {
  return apiCall<WorkflowCanvasAssetBindingRepairResult>(
    `${projectPath(project)}/workflow-runs/${encodeURIComponent(runId)}/canvas-asset-binding-repair`,
    { method: "POST", json: payload },
  );
}

export async function revalidateWorkflowCanvasAssetBinding(
  project: string,
  runId: string,
  payload: WorkflowCanvasAssetBindingCommandRequest,
): Promise<WorkflowCanvasAssetBindingReadinessResult> {
  return apiCall<WorkflowCanvasAssetBindingReadinessResult>(
    `${projectPath(project)}/workflow-runs/${encodeURIComponent(runId)}/canvas-asset-binding-revalidate`,
    { method: "POST", json: payload },
  );
}
