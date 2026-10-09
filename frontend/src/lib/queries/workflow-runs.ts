// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  commandWorkflowRun,
  getWorkflowRun,
  listWorkflowRuns,
  subscribeWorkflowRunSnapshots,
} from "@/api/workflow-runtime";
import type { WorkflowRun } from "@/types/workflow-runtime";

export const workflowRunQueryKeys = {
  canvas: (project: string, canvasId: string) =>
    ["workflow-runs", project, canvasId] as const,
  detail: (project: string, runId: string) =>
    ["workflow-run", project, runId] as const,
};

export function useWorkflowRun(project: string, runId: string | null) {
  return useQuery({
    queryKey: workflowRunQueryKeys.detail(project, runId ?? ""),
    queryFn: ({ signal }) => getWorkflowRun(project, runId as string, signal),
    enabled: Boolean(project && runId),
    staleTime: 1_000,
  });
}

/**
 * Read the WorkflowRuntime runs bound to one canvas.
 *
 * The production page owns a different run model, so this is the read-only
 * bridge that lets the same durable WorkflowRun facts show up there. It polls
 * faster only while a run still has work in flight.
 */
export function useCanvasWorkflowRuns(
  project: string,
  canvasId: string | null,
) {
  const queryClient = useQueryClient();
  const [liveScope, setLiveScope] = useState<string | null>(null);
  const scope = JSON.stringify([project, canvasId]);
  const query = useQuery({
    queryKey: workflowRunQueryKeys.canvas(project, canvasId ?? ""),
    queryFn: ({ signal }) =>
      listWorkflowRuns(project, canvasId as string, signal),
    enabled: Boolean(project && canvasId),
    refetchInterval: (query) => {
      const runs = query.state.data as WorkflowRun[] | undefined;
      const busyRuns = (runs ?? []).filter(
        (run) => run.status === "running" || run.status === "paused",
      );
      return busyRuns.length > 0 && (liveScope !== scope || busyRuns.length > 1)
        ? 3_000 : 15_000;
    },
    staleTime: 1_000,
  });

  const activeRun = (query.data ?? []).find((run) =>
    run.status === "running" || run.status === "paused",
  );
  const activeRunId = activeRun?.id ?? null;

  // A running WorkflowRun already has a durable SSE stream. Feed its snapshots
  // into the same list cache so the overview does not need to re-read the full
  // canvas run list every three seconds while work is in flight. The query
  // interval remains as a recovery path when SSE is unavailable.
  useEffect(() => {
    if (!project || !canvasId || !activeRunId) return undefined;
    let disposed = false;
    setLiveScope(null);
    const subscription = subscribeWorkflowRunSnapshots({
      projectId: project,
      canvasId,
      runId: activeRunId,
      onRun: (incoming) => {
        if (disposed) return;
        setLiveScope(scope);
        queryClient.setQueryData<WorkflowRun[]>(
          workflowRunQueryKeys.canvas(project, canvasId),
          (current) => {
            const source = current ?? [];
            const index = source.findIndex((run) => run.id === incoming.id);
            if (index < 0) return [...source, incoming];
            const previous = source[index];
            if (incoming.revision < previous.revision ||
                (incoming.revision === previous.revision && incoming.event_seq < previous.event_seq)) return source;
            return source.map((run, itemIndex) =>
              itemIndex === index ? incoming : run,
            );
          },
        );
      },
      onError: () => {
        if (!disposed) setLiveScope(null);
      },
    });
    return () => {
      disposed = true;
      subscription.close();
    };
  }, [activeRunId, canvasId, project, queryClient, scope]);

  return query;
}

export function useRetryWorkflowStep(project: string, canvasId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ run, stepId }: { run: WorkflowRun; stepId: string }) =>
      commandWorkflowRun(project, run.id, {
        command: "retry",
        step_id: stepId,
        retry_scope: "whole_step",
        idempotency_key: `ui-step-retry:${run.id}:${run.revision}:${stepId}`,
        expected_revision: run.revision,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: workflowRunQueryKeys.canvas(project, canvasId ?? ""),
      });
    },
  });
}
