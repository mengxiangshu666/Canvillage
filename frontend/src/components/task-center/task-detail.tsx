// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  CheckCircle2,
  CircleAlert,
  CircleStop,
  Clock3,
  ImageIcon,
  LoaderCircle,
  Music2,
  Video,
} from "lucide-react";

import { useTaskCenterStore } from "@/task-center/store";
import { TaskLogs } from "./task-logs";
import { TaskActions } from "./task-actions";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Progress } from "@/components/ui/progress";
import { displayLabel, isActive, taskActivityLabel } from "@/task-center/derivations";
import { taskErrorMessage } from "@/task-center/task-errors";
import type { TaskState } from "@/task-center/types";
import { cn } from "@/lib/utils";

export function formatLocalTaskTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const formatted = new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absOffset = Math.abs(offsetMinutes);
  const hours = String(Math.floor(absOffset / 60)).padStart(2, "0");
  const minutes = String(absOffset % 60).padStart(2, "0");
  return `${formatted} UTC${sign}${hours}:${minutes}`;
}

function formatFriendlyTaskTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return new Intl.DateTimeFormat(undefined, sameDay
    ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }
    : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function formatDuration(task: TaskState): string {
  const start = Date.parse(task.created_at);
  const end = Date.parse(task.completed_at || task.updated_at);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

function stringField(value: unknown, key: string): string {
  if (!value || typeof value !== "object") return "";
  const field = (value as Record<string, unknown>)[key];
  return typeof field === "string" ? field.trim() : "";
}

function resultMetadata(task: TaskState): Record<string, unknown> | null {
  const result = task.result;
  if (!result || typeof result !== "object") return null;
  const metadata = (result as Record<string, unknown>).task_metadata;
  return metadata && typeof metadata === "object" ? metadata as Record<string, unknown> : null;
}

function taskMetadata(task: TaskState): Record<string, unknown> {
  return {
    ...(resultMetadata(task) ?? {}),
    ...(task.metadata ?? {}),
  };
}

function providerTaskId(task: TaskState): string {
  const result = task.result;
  const resultRecord = result && typeof result === "object" ? result as Record<string, unknown> : null;
  const resultTaskMetadata =
    resultRecord?.task_metadata && typeof resultRecord.task_metadata === "object"
      ? resultRecord.task_metadata as Record<string, unknown>
      : null;
  const metadata = taskMetadata(task);
  return (
    stringField(result, "provider_task_id") ||
    stringField(result, "huimeng_task_id") ||
    stringField(result, "newapi_task_id") ||
    stringField(resultTaskMetadata, "provider_task_id") ||
    stringField(resultTaskMetadata, "huimeng_task_id") ||
    stringField(resultTaskMetadata, "newapi_task_id") ||
    stringField(metadata, "provider_task_id") ||
    stringField(metadata, "huimeng_task_id") ||
    stringField(metadata, "newapi_task_id")
  );
}

function metadataField(task: TaskState, key: string): string {
  return stringField(taskMetadata(task), key);
}

function debugLabel(key: string, t: ReturnType<typeof useTranslation>["t"]): string {
  if (key === "task_id") return t("taskCenter.detail.meta.taskId");
  if (key === "provider_task_id") return t("taskCenter.detail.meta.providerTaskId");
  if (key === "workflow_run_id") return t("taskCenter.detail.meta.workflowRunId");
  if (key === "workflow_step_id") return t("taskCenter.detail.meta.workflowStepId");
  if (key === "run_id") return t("taskCenter.detail.meta.runId");
  if (key === "command_id") return t("taskCenter.detail.meta.commandId");
  return key;
}

function isPathLikeKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return (
    lowered === "path" ||
    lowered === "paths" ||
    lowered.endsWith("_path") ||
    lowered.endsWith("_paths")
  );
}

function isAbsoluteLocalPath(value: string): boolean {
  const lowered = value.trim().toLowerCase();
  if (
    lowered.startsWith("/static/") ||
    lowered.startsWith("http://") ||
    lowered.startsWith("https://") ||
    lowered.startsWith("blob:") ||
    lowered.startsWith("data:")
  ) {
    return false;
  }
  return value.startsWith("/") || /^[A-Za-z]:[\\/]/.test(value);
}

function sanitizeResultForDisplay(value: unknown, key = ""): unknown {
  if (Array.isArray(value)) {
    if (
      isPathLikeKey(key) &&
      value.some((item) => typeof item === "string" && isAbsoluteLocalPath(item))
    ) {
      return undefined;
    }
    return value
      .map((item) => sanitizeResultForDisplay(item))
      .filter((item) => item !== undefined);
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    Object.entries(value as Record<string, unknown>).forEach(([entryKey, entryValue]) => {
      const sanitized = sanitizeResultForDisplay(entryValue, entryKey);
      if (sanitized !== undefined) out[entryKey] = sanitized;
    });
    return out;
  }
  if (typeof value === "string" && isPathLikeKey(key) && isAbsoluteLocalPath(value)) {
    return undefined;
  }
  return value;
}

type ResultMedia = { url: string; kind: "image" | "video" | "audio" };

function mediaKind(url: string, key: string): ResultMedia["kind"] | null {
  const value = url.toLowerCase().split("?")[0].split("#")[0];
  const hint = key.toLowerCase();
  if (/\.(png|jpe?g|webp|gif|avif|bmp)$/.test(value) || hint.includes("image")) return "image";
  if (/\.(mp4|webm|mov|m4v)$/.test(value) || hint.includes("video")) return "video";
  if (/\.(mp3|wav|m4a|aac|ogg|flac)$/.test(value) || hint.includes("audio")) return "audio";
  if (url.startsWith("data:image/")) return "image";
  if (url.startsWith("data:video/")) return "video";
  if (url.startsWith("data:audio/")) return "audio";
  return null;
}

function collectResultMedia(
  value: unknown,
  key = "",
  output: ResultMedia[] = [],
  seen = new Set<string>(),
): ResultMedia[] {
  if (output.length >= 8) return output;
  if (typeof value === "string") {
    if (isAbsoluteLocalPath(value)) return output;
    const kind = mediaKind(value, key);
    const looksPublic = /^(https?:|blob:|data:|\/static\/)/i.test(value);
    if (kind && looksPublic && !seen.has(value)) {
      seen.add(value);
      output.push({ url: value, kind });
    }
    return output;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectResultMedia(item, key, output, seen));
    return output;
  }
  if (value && typeof value === "object") {
    Object.entries(value as Record<string, unknown>).forEach(([entryKey, entryValue]) => {
      collectResultMedia(entryValue, entryKey, output, seen);
    });
  }
  return output;
}

function resultSummary(task: TaskState): string {
  const result = task.result;
  return (
    stringField(result, "summary") ||
    stringField(result, "message") ||
    stringField(result, "detail") ||
    stringField(result, "status")
  );
}

function costValues(value: Record<string, number> | undefined): string {
  if (!value) return "—";
  const entries = Object.entries(value);
  return entries.length ? entries.map(([key, amount]) => `${key} ${amount}`).join(" · ") : "—";
}

function TaskStateIcon({ task }: { task: TaskState }) {
  if (task.status === "completed") return <CheckCircle2 className="size-5 text-emerald-400" />;
  if (task.status === "failed") return <CircleAlert className="size-5 text-rose-400" />;
  if (task.status === "cancelled") return <CircleStop className="size-5 text-white/42" />;
  return <LoaderCircle className="size-5 animate-spin text-cyan-300" />;
}

export function TaskDetail() {
  const { t } = useTranslation();
  const selectedTaskKey = useTaskCenterStore((s) => s.selectedTaskKey);
  const tasksMap = useTaskCenterStore((s) => s.tasks);
  const task = useMemo(
    () => (selectedTaskKey ? tasksMap.get(selectedTaskKey) ?? null : null),
    [selectedTaskKey, tasksMap],
  );

  if (!task) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <Clock3 className="size-6 text-white/18" />
        <span className="text-xs text-white/38">{t("taskCenter.panel.selectPrompt")}</span>
      </div>
    );
  }

  const providerId = providerTaskId(task);
  const displayResult = task.result ? sanitizeResultForDisplay(task.result) : null;
  const resultMedia = collectResultMedia(task.result);
  const summary = resultSummary(task);
  const costReceipt = task.production_cost_receipt;
  const costSummary = task.production_cost_summary;
  const sourceLabel = metadataField(task, "source_label");
  const targetLabel = metadataField(task, "target_label");
  const jobId = metadataField(task, "job_id") || metadataField(task, "scope") || task.scope || "";
  const celeryId = metadataField(task, "celery_task_id");
  const skillId = metadataField(task, "skill_id");
  const canvasId = metadataField(task, "canvas_id");
  const nodeId = metadataField(task, "node_id");
  const workflowRunId = metadataField(task, "workflow_run_id");
  const workflowStepId = metadataField(task, "workflow_step_id");
  const runId =
    task.task_acceptance_receipt?.run_id ||
    task.production_cost_receipt?.run_id ||
    metadataField(task, "run_id");
  const commandId =
    task.task_acceptance_receipt?.command_id ||
    task.production_cost_receipt?.command_id ||
    metadataField(task, "command_id");
  const distinctRunId = runId !== workflowRunId ? runId : "";
  const progress = Math.max(0, Math.min(100, Math.round(task.progress * 100)));
  const activityLabel = taskActivityLabel(task, t);
  const debugRows = [
    ["task_key", task.task_key],
    ["task_id", task.task_id],
    ["job_id", jobId],
    ["celery_task_id", celeryId],
    ["provider_task_id", providerId],
    ["skill_id", skillId],
    ["canvas_id", canvasId],
    ["node_id", nodeId],
    ["workflow_run_id", workflowRunId],
    ["workflow_step_id", workflowStepId],
    ["run_id", distinctRunId],
    ["command_id", commandId],
  ].filter(([, value]) => Boolean(value));

  return (
    <div className="flex h-full flex-col bg-[radial-gradient(circle_at_50%_-30%,rgba(255,255,255,0.035),transparent_42%)]">
      <Tabs defaultValue="overview" className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex shrink-0 items-start justify-between gap-4 px-5 pt-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate text-[15px] font-semibold tracking-[-0.02em] text-white/92">
                {displayLabel(task, t)}
              </h3>
              <span
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                  task.status === "completed" && "border-emerald-400/18 bg-emerald-400/10 text-emerald-300",
                  task.status === "failed" && "border-rose-400/18 bg-rose-400/10 text-rose-300",
                  isActive(task) && "border-cyan-300/18 bg-cyan-300/10 text-cyan-200",
                  task.status === "cancelled" && "border-white/10 bg-white/[0.04] text-white/44",
                )}
              >
                {t(`taskCenter.status.${task.status}`)}
              </span>
            </div>
            {(sourceLabel || targetLabel) ? (
              <div className="mt-1 truncate text-[11px] text-white/38">
                {[sourceLabel, targetLabel].filter(Boolean).join(" → ")}
              </div>
            ) : null}
          </div>
          <TabsList className="h-8 shrink-0 rounded-xl bg-white/[0.055] p-0.5">
            <TabsTrigger value="overview" className="h-7 rounded-[9px] px-3 text-[11px]">
              {t("taskCenter.detail.tabs.overview")}
            </TabsTrigger>
            <TabsTrigger value="logs" className="h-7 rounded-[9px] px-3 text-[11px]">
              {t("taskCenter.detail.tabs.logs")}
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="overview" className="min-h-0 flex-1 overflow-auto px-5 pb-5 pt-4 text-xs">
          <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-4">
            <div className="flex items-start gap-3">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-black/20">
                <TaskStateIcon task={task} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-[13px] font-medium text-white/88">
                      {activityLabel}
                    </p>
                    <p className="mt-1 text-[11px] text-white/38">
                      {isActive(task)
                        ? t("taskCenter.detail.runningDescription")
                        : task.status === "completed"
                          ? t("taskCenter.detail.completedDescription")
                          : t("taskCenter.detail.stoppedDescription")}
                    </p>
                  </div>
                  <span className="text-[18px] font-semibold tabular-nums text-white/82">{progress}%</span>
                </div>
                <Progress value={progress} className="mt-3 h-1.5 bg-white/[0.07]" />
              </div>
            </div>
          </section>

          <div className="mt-3 grid grid-cols-3 gap-2">
            <div className="rounded-xl border border-white/[0.065] bg-black/10 px-3 py-2.5">
              <div className="text-[10px] text-white/32">{t("taskCenter.detail.meta.createdAt")}</div>
              <div className="mt-1 text-[11px] tabular-nums text-white/68">{formatFriendlyTaskTime(task.created_at)}</div>
            </div>
            <div className="rounded-xl border border-white/[0.065] bg-black/10 px-3 py-2.5">
              <div className="text-[10px] text-white/32">{t("taskCenter.detail.duration")}</div>
              <div className="mt-1 text-[11px] tabular-nums text-white/68">{formatDuration(task)}</div>
            </div>
            <div className="rounded-xl border border-white/[0.065] bg-black/10 px-3 py-2.5">
              <div className="text-[10px] text-white/32">{task.completed_at ? t("taskCenter.detail.meta.completedAt") : t("taskCenter.detail.meta.updatedAt")}</div>
              <div className="mt-1 text-[11px] tabular-nums text-white/68">{formatFriendlyTaskTime(task.completed_at || task.updated_at)}</div>
            </div>
          </div>

          {costReceipt || costSummary ? (
            <section className="mt-3 rounded-2xl border border-amber-300/12 bg-amber-300/[0.045] p-4">
              <div className="flex items-center justify-between gap-3">
                <h4 className="text-[12px] font-medium text-amber-100/82">成本回执</h4>
                <span className="text-[10px] text-amber-100/42">
                  {costSummary?.receipt_count ?? 1} 条
                </span>
              </div>
              <div className="mt-2 grid gap-1.5 text-[11px] leading-5 text-white/56">
                <div className="flex justify-between gap-3"><span>预估</span><span className="text-right text-white/72">{costValues(costReceipt?.estimated_cost ?? costSummary?.estimated_cost)}</span></div>
                <div className="flex justify-between gap-3"><span>已占用</span><span className="text-right text-white/72">{costValues(costReceipt?.reserved_cost ?? costSummary?.reserved_cost)}</span></div>
                <div className="flex justify-between gap-3"><span>实际</span><span className="text-right text-white/72">{costValues(costReceipt?.actual_cost ?? costSummary?.actual_cost)}</span></div>
                <div className="flex justify-between gap-3"><span>浪费</span><span className="text-right text-white/72">{costValues(costReceipt?.wasted_cost ?? costSummary?.wasted_cost)}</span></div>
              </div>
            </section>
          ) : null}

          {task.error ? (
            <section className="mt-3 rounded-2xl border border-rose-400/14 bg-rose-400/[0.055] p-4 text-rose-200">
              <div className="flex items-center gap-2 font-medium">
                <CircleAlert className="size-4" />
                {t("taskCenter.detail.error.label")}
              </div>
              <div className="mt-2 leading-5 text-rose-100/82">{taskErrorMessage(task, t)}</div>
            </section>
          ) : null}

          {displayResult ? (
            <section className="mt-4">
              <div className="mb-2 flex items-center justify-between gap-3">
                <h4 className="text-[12px] font-medium text-white/72">{t("taskCenter.detail.resultTitle")}</h4>
                {resultMedia.length ? <span className="text-[10px] text-white/30">{t("taskCenter.detail.artifactCount", { count: resultMedia.length })}</span> : null}
              </div>
              {resultMedia.length ? (
                <div className="grid grid-cols-2 gap-2 xl:grid-cols-3">
                  {resultMedia.map((media) => (
                    <div key={media.url} className="group relative aspect-video overflow-hidden rounded-xl border border-white/[0.08] bg-black/24">
                      {media.kind === "image" ? (
                        <img src={media.url} alt="任务生成结果" className="h-full w-full object-cover" loading="lazy" />
                      ) : media.kind === "video" ? (
                        <video src={media.url} className="h-full w-full object-cover" controls preload="metadata" />
                      ) : (
                        <div className="flex h-full flex-col items-center justify-center gap-2 p-3">
                          <Music2 className="size-5 text-white/42" />
                          <audio src={media.url} className="w-full" controls preload="metadata" />
                        </div>
                      )}
                      <span className="pointer-events-none absolute left-2 top-2 flex size-6 items-center justify-center rounded-lg bg-black/55 text-white/72 backdrop-blur-md">
                        {media.kind === "image" ? <ImageIcon className="size-3.5" /> : media.kind === "video" ? <Video className="size-3.5" /> : <Music2 className="size-3.5" />}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-2xl border border-white/[0.07] bg-white/[0.025] px-4 py-3">
                  <p className="text-[12px] font-medium text-white/72">{t("taskCenter.detail.resultSaved")}</p>
                  <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-white/40">
                    {summary || t("taskCenter.detail.resultSavedDescription")}
                  </p>
                </div>
              )}
              <details className="mt-2 rounded-xl border border-white/[0.06] bg-black/10 px-3 py-2">
                <summary className="cursor-pointer text-[11px] text-white/38 hover:text-white/64">{t("taskCenter.detail.technicalResult")}</summary>
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/20 p-3 font-mono text-[10px] leading-5 text-white/48">
                  {JSON.stringify(displayResult, null, 2)}
                </pre>
              </details>
            </section>
          ) : null}

          {debugRows.length ? (
            <details className="mt-3 rounded-xl border border-white/[0.06] bg-black/10 px-3 py-2">
              <summary className="cursor-pointer text-[11px] text-white/34 hover:text-white/62">
                {t("taskCenter.detail.debugInfo")}
              </summary>
              <div className="mt-2 grid gap-1.5 font-mono text-[10px] leading-5">
                {debugRows.map(([label, value]) => (
                  <div key={label} className="break-all">
                    <span className="text-white/28">{debugLabel(label, t)}: </span>
                    <span className="text-white/52">{value}</span>
                  </div>
                ))}
                <div className="break-all">
                  <span className="text-white/28">完整时间: </span>
                  <span className="text-white/52">{formatLocalTaskTime(task.updated_at)}</span>
                </div>
              </div>
            </details>
          ) : null}
        </TabsContent>

        <TabsContent value="logs" className="min-h-0 flex-1 overflow-hidden px-2 pb-2 pt-3">
          <TaskLogs task={task} />
        </TabsContent>
      </Tabs>
      <TaskActions task={task} />
    </div>
  );
}
