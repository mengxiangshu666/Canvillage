// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useState } from "react";
import { decideVisualPreflight } from "@/api/workflow-runtime";
import { Link, useRouterState } from "@tanstack/react-router";
import {
  AlertTriangle,
  Check,
  Clapperboard,
  LoaderCircle,
  RotateCcw,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  useCanvasWorkflowRuns,
  useRetryWorkflowStep,
  useWorkflowRun,
} from "@/lib/queries/workflow-runs";
import { readLastCanvas } from "@/lib/url-params";
import { cn } from "@/lib/utils";
import type { WorkflowRun } from "@/types/workflow-runtime";

import {
  workflowRunOverviewVisibleRuns,
  workflowRunActiveStep,
  workflowRunFailedStep,
  workflowRunProgress,
  workflowRunRecoveryGuidance,
  workflowRunRetryTarget,
  workflowRunStatusLabel,
  workflowRunStatusTone,
  workflowRunTitle,
} from "./workflow-run-overview-model";

const MAX_RECENT_RUNS = 3;

function VisualPreflightReports({ run, projectId, onChanged }: {
  run: WorkflowRun; projectId: string; onChanged: () => void;
}) {
  const [pending, setPending] = useState(false);
  const artifact = run.artifacts.shot_videos as { visual_preflight?: {
    reports?: Array<{ shot_id: string; shot_no?: string; input_fingerprint: string;
      status: string; reason?: string; issues?: string[]; original_prompt: string;
      revised_motion_prompt?: string; dispatched_prompt?: string; decision?: string }>;
  }} | undefined;
  const rawReports = artifact?.visual_preflight?.reports;
  const reports = Array.isArray(rawReports) ? rawReports.filter((report) =>
    report && typeof report.shot_id === "string" && typeof report.input_fingerprint === "string" &&
    typeof report.original_prompt === "string" && typeof report.status === "string"
  ) : [];
  if (!reports.length) return null;
  return <div className="mt-2 space-y-2 text-xs">
    {reports.map((report) => <details key={report.shot_id} className="border-t border-white/10 pt-2">
      <summary className="cursor-pointer">镜头 {report.shot_no ?? report.shot_id} · {report.status === "aligned" ? "首帧与动作一致" : report.status === "suggested_edit" ? "动作已校准" : report.status === "check_unavailable" ? "检查不可用，尚未验证" : "需要核对"}</summary>
      <p className="my-2 break-words">{report.reason} {Array.isArray(report.issues) ? report.issues.filter((issue) => typeof issue === "string").join("；") : ""}</p>
      <p className="whitespace-pre-wrap break-words">原提示词：{report.original_prompt}</p>
      {report.revised_motion_prompt && <p className="mt-2 whitespace-pre-wrap break-words">建议提示词：{report.revised_motion_prompt}</p>}
      {report.dispatched_prompt && <p className="mt-2 whitespace-pre-wrap break-words">提交提示词：{report.dispatched_prompt}</p>}
      {run.status === "failed" && !report.decision && ["needs_user_review", "check_unavailable"].includes(report.status) &&
        <div className="mt-2 flex flex-wrap gap-2">
          {(["keep_original", ...(report.revised_motion_prompt ? ["accept_suggestion"] : [])] as const).map((decision) =>
            <Button key={decision} size="sm" variant="outline" disabled={pending} onClick={async () => {
              setPending(true);
              try {
                await decideVisualPreflight(projectId, run, report, decision as "keep_original" | "accept_suggestion");
                onChanged();
              } catch (error) { toast.error(error instanceof Error ? error.message : "决定未提交，请刷新状态"); }
              finally { setPending(false); }
            }}><Check className="mr-1 size-3" aria-hidden="true" />{decision === "keep_original" ? "按原提示词继续" : "接受建议并继续"}</Button>)}
        </div>}
    </details>)}
  </div>;
}

function formatStamp(value: string | undefined): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString();
}

/**
 * Read-only view of the durable WorkflowRuntime runs bound to this project's
 * canvas. The production page owns a different run model, so without this the
 * "一键成片" runs only surface inside the canvas Agent panel.
 */
export function WorkflowRunOverview({ projectId }: { projectId: string }) {
  const canvasFromUrl = useRouterState({
    select: (state) => {
      const canvas = (state.location.search as { canvas?: unknown }).canvas;
      return typeof canvas === "string" && canvas.length > 0 ? canvas : null;
    },
  });
  const canvasId = canvasFromUrl ?? readLastCanvas(projectId);
  const requestedRunId = useRouterState({
    select: (state) => {
      const id = (state.location.search as { workflowRunId?: unknown }).workflowRunId;
      return typeof id === "string" && id.length > 0 ? id : null;
    },
  });
  const runs = useCanvasWorkflowRuns(projectId, canvasId);
  const requestedRun = useWorkflowRun(projectId, requestedRunId);
  const retry = useRetryWorkflowStep(projectId, canvasId);

  const requestedRunMatches =
    requestedRun.data?.id === requestedRunId &&
    requestedRun.data.project_id === projectId &&
    requestedRun.data.canvas_id === canvasId;

  const ordered = useMemo(
    () => {
      const source = runs.data ?? [];
      const withRequested = requestedRunMatches && requestedRun.data
        ? [requestedRun.data, ...source.filter((run) => run.id !== requestedRun.data?.id)]
        : source;
      return workflowRunOverviewVisibleRuns(
        withRequested,
        MAX_RECENT_RUNS,
        requestedRunMatches ? requestedRunId : null,
      );
    },
    [runs.data, requestedRunMatches, requestedRun.data],
  );

  if (!canvasId) return null;

  const submitRetry = async (run: WorkflowRun, stepId: string) => {
    try {
      await retry.mutateAsync({ run, stepId });
      toast.success("已提交整步重试");
    } catch (error) {
      toast.error(
        `重试未提交：${error instanceof Error ? error.message : String(error)}`,
      );
    }
  };

  return (
    <section
      id="workflow-runs"
      className="overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.02]"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-3.5">
        <div className="flex min-w-0 items-center gap-2">
          <Clapperboard className="size-4 shrink-0 text-muted-foreground" />
          <h2 className="shrink-0 text-sm font-semibold">画布工作流</h2>
          <span
            className="min-w-0 truncate font-mono text-[10px] text-muted-foreground/70"
            title={canvasId}
          >
            {canvasId}
          </span>
        </div>
        {runs.isFetching ? (
          <LoaderCircle className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
        ) : null}
      </div>

      {requestedRunId && requestedRun.isError ? (
        <p role="status" className="px-5 py-2.5 text-xs text-amber-200">
          任务关联的工作流无法读取，列表状态仍按当前画布显示。
        </p>
      ) : null}
      {requestedRunId && requestedRun.data && !requestedRunMatches ? (
        <p role="status" className="px-5 py-2.5 text-xs text-amber-200">
          任务关联的工作流不属于当前画布，未展示。
        </p>
      ) : null}

      {runs.isError && ordered.length === 0 ? (
        <p className="px-5 py-4 text-xs text-red-300">工作流状态读取失败。</p>
      ) : ordered.length === 0 ? (
        <p className="px-5 py-4 text-xs text-muted-foreground">
          {runs.isLoading
            ? "正在读取工作流运行……"
            : "这个画布还没有工作流运行。"}
        </p>
      ) : (
        <>
          {runs.isError ? (
            <p role="status" className="px-5 py-2.5 text-xs text-amber-200">
              状态暂时无法更新，以下是最近一次读取的工作流状态。
            </p>
          ) : null}
          <ul className="divide-y divide-white/[0.06]">
            {ordered.map((run) => {
              const progress = workflowRunProgress(run);
              const activeStep = workflowRunActiveStep(run);
              const failedStep = workflowRunFailedStep(run);
              const recoveryGuidance = workflowRunRecoveryGuidance(
                run,
                failedStep,
              );
              const retryTarget = workflowRunRetryTarget(run);
              const failureText = failedStep?.error || run.error;
              const retrying =
                retry.isPending && retry.variables?.run.id === run.id;

              return (
                <li
                  key={run.id}
                  id={run.id === requestedRunId ? `workflow-run-${run.id}` : undefined}
                  aria-current={run.id === requestedRunId ? "location" : undefined}
                  className={cn(
                    "px-5 py-4",
                    run.id === requestedRunId && "bg-primary/[0.06]",
                  )}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">
                          {workflowRunTitle(run.workflow_id)}
                        </span>
                        <Badge
                          variant="outline"
                          className={workflowRunStatusTone(run.status)}
                        >
                          {workflowRunStatusLabel(run.status)}
                        </Badge>
                        <span className="font-mono text-[10px] text-muted-foreground/60">
                          {run.id.slice(0, 14)}
                        </span>
                        {run.run_mode ? (
                          <span className="text-[10px] text-muted-foreground/70">
                            {run.run_mode === "auto" ? "自动" : "草稿"}
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 truncate text-[11px] text-muted-foreground">
                        {activeStep
                          ? activeStep.label || activeStep.id
                          : "没有待执行步骤"}
                        {` · ${progress.completed}/${progress.total} 步`}
                        {run.created_at
                          ? ` · ${formatStamp(run.created_at)}`
                          : ""}
                      </p>
                      {failureText ? (
                        <p className="mt-1.5 flex items-start gap-1.5 text-[11px] text-red-300">
                          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                          <span className="min-w-0 break-words">
                            {run.error_code ? `${run.error_code}：` : ""}
                            {failureText}
                          </span>
                        </p>
                      ) : null}
                      {recoveryGuidance ? (
                        <p className="mt-1.5 text-[11px] leading-4 text-amber-200/80">
                          <span className="font-medium">
                            {recoveryGuidance.title}：
                          </span>
                          {recoveryGuidance.instruction}
                        </p>
                      ) : null}
                    </div>

                    <div className="flex w-full shrink-0 items-center justify-start gap-2 sm:w-auto sm:justify-end">
                      {retryTarget ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={retrying}
                          onClick={() =>
                            void submitRetry(run, retryTarget.stepId)
                          }
                        >
                          {retrying ? (
                            <LoaderCircle className="size-3.5 animate-spin" />
                          ) : (
                            <RotateCcw className="size-3.5" />
                          )}
                          重试{retryTarget.label}
                        </Button>
                      ) : null}
                      <Link
                        className={cn(
                          buttonVariants({ size: "sm", variant: "ghost" }),
                        )}
                        to="/projects/$project/freezone"
                        params={{ project: projectId }}
                        search={{ canvas: canvasId } as never}
                      >
                        打开画布
                      </Link>
                    </div>
                  </div>

                  <VisualPreflightReports run={run} projectId={projectId} onChanged={() => { void runs.refetch(); }} />

                  <div className="mt-2.5 h-1 w-full overflow-hidden rounded-full bg-white/[0.06]">
                    <div
                      className={cn(
                        "h-full rounded-full",
                        run.status === "failed"
                          ? "bg-red-400/70"
                          : "bg-primary/70",
                      )}
                      style={{
                        width: `${Math.round(progress.ratio * 100)}%`,
                      }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
