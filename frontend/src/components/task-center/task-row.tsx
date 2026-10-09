// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import dayjs from "dayjs";
import { useTranslation } from "react-i18next";
import type { TaskState } from "@/task-center/types";
import { displayLabel, taskActivityLabel } from "@/task-center/derivations";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { Check, CircleAlert, CircleDot, Loader2, X } from "lucide-react";

function shortTimestamp(task: TaskState): string {
  // Match the list sort: prefer updated_at, fall back to created_at.
  const raw = task.updated_at || task.created_at;
  if (!raw) return "";
  const d = dayjs(raw);
  if (!d.isValid()) return "";
  return d.isSame(dayjs(), "day") ? d.format("HH:mm") : d.format("MM-DD");
}

const STATUS_COLOR: Record<TaskState["status"], string> = {
  submitting: "text-muted-foreground",
  queued: "text-muted-foreground",
  pending: "text-muted-foreground",
  starting: "text-muted-foreground",
  running: "text-primary",
  waiting: "text-primary",
  completed: "text-success",
  failed: "text-destructive",
  cancelled: "text-muted-foreground",
};

export function TaskRow({
  task,
  selected,
  onClick,
}: {
  task: TaskState;
  selected: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  const label = displayLabel(task, t);
  const activityLabel = taskActivityLabel(task, t);
  const StatusIcon = task.status === "completed"
    ? Check
    : task.status === "failed"
      ? CircleAlert
      : task.status === "cancelled"
        ? X
        : task.status === "running" || task.status === "starting" || task.status === "waiting"
          ? Loader2
          : CircleDot;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "mb-1 flex h-[58px] w-full items-center gap-2.5 rounded-xl border border-transparent px-2.5 text-left transition-colors hover:bg-white/[0.045]",
        selected && "border-white/[0.09] bg-white/[0.075]",
      )}
    >
      <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-lg bg-white/[0.045]", STATUS_COLOR[task.status])}>
        <StatusIcon className={cn("size-3.5", (task.status === "running" || task.status === "starting" || task.status === "waiting") && "animate-spin")} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-white/86">{label}</span>
          <span className="shrink-0 text-[10px] tabular-nums text-white/32">{shortTimestamp(task)}</span>
        </span>
        <span className="mt-1 flex items-center gap-2">
          <span className={cn("text-[10px]", STATUS_COLOR[task.status])}>
            {t(`taskCenter.status.${task.status}`)}
          </span>
          {task.status === "running" || task.status === "waiting" ? (
            <>
              <span className="min-w-0 flex-1">
                <Progress value={Math.round(task.progress * 100)} className="h-1" />
              </span>
              <span className="w-7 text-right text-[10px] tabular-nums text-white/42">
                {Math.round(task.progress * 100)}%
              </span>
            </>
          ) : activityLabel ? (
            <span className="min-w-0 flex-1 truncate text-[10px] text-white/34">{activityLabel}</span>
          ) : null}
        </span>
      </span>
    </button>
  );
}
