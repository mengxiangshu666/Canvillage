// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { ChevronUp, Zap, Circle } from "lucide-react";
import {
  useTaskCenterStore,
  selectRunningTasks,
  selectLeadingRunning,
  selectLastCompletion,
} from "@/task-center/store";
import { useAppStore } from "@/stores/app-store";
import { displayLabel } from "@/task-center/derivations";
import { RegionBadge } from "@/components/layout/region-badge";
import { APP_VERSION } from "@/lib/app-version";
import { cn } from "@/lib/utils";
import type { StreamHealth, TaskState } from "@/task-center/types";

const HEALTH_COLOR: Record<StreamHealth, string> = {
  connecting: "text-muted-foreground",
  connected: "text-success",
  reconnecting: "text-warning",
  polling: "text-warning",
  failed: "text-destructive",
};

function relativeLabel(iso: string, now: number = Date.now()): string {
  const ms = now - Date.parse(iso);
  const s = Math.floor(ms / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ago`;
}

function taskProgress(task: TaskState | null | undefined): number {
  if (!task) return 0;
  if (task.status === "completed") return 100;
  return Math.max(0, Math.min(100, Math.round((task.progress || 0) * 100)));
}

type TaskStatusBarProps = {
  overlay?: boolean;
};

export function TaskStatusBar({ overlay = false }: TaskStatusBarProps) {
  const { t } = useTranslation();
  // Subscribe to the tasks Map itself (stable reference unless updated) and
  // derive running/leading/last-completion via useMemo so each render does
  // not allocate a fresh array and feed Zustand's equality check a new object,
  // which would loop (re-render → new array → store notifies → re-render …).
  const tasks = useTaskCenterStore((s) => s.tasks);
  const running = useMemo(
    () => selectRunningTasks({ tasks } as Parameters<typeof selectRunningTasks>[0]),
    [tasks],
  );
  const leading = useMemo(
    () => selectLeadingRunning({ tasks } as Parameters<typeof selectLeadingRunning>[0]),
    [tasks],
  );
  const lastDone = useMemo(
    () => selectLastCompletion({ tasks } as Parameters<typeof selectLastCompletion>[0]),
    [tasks],
  );
  const health = useTaskCenterStore((s) => s.streamHealth);
  const projectId = useTaskCenterStore((s) => s.projectId);
  const panelOpen = useAppStore((s) => s.taskPanelOpen);
  const panelHeight = useAppStore((s) => s.taskPanelHeight);
  const setOpen = useAppStore((s) => s.setTaskPanelOpen);
  const setSelected = useTaskCenterStore((s) => s.setSelected);

  /**
   * Toggle the panel. When opening, optionally pre-select a task so
   * clicking the leading/last-done item lands on its details.
   * Closing leaves the selection alone so re-opening returns to context.
   */
  const togglePanelWith = (key: string | null) => {
    if (panelOpen) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (key) setSelected(key);
  };

  const primaryKey = leading?.task_key ?? lastDone?.task_key ?? null;
  const progressTask = leading ?? lastDone ?? null;
  const progress = taskProgress(progressTask);
  const progressTone = progressTask?.status === "failed"
    ? "bg-destructive/55"
    : progressTask?.status === "completed"
      ? "bg-success/55"
      : "bg-primary/55";
  const isProgressActive = progressTask ? !["completed", "failed", "cancelled"].includes(progressTask.status) : false;
  const onBarClick = () => togglePanelWith(primaryKey);
  const onBarKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onBarClick();
    }
  };

  return (
    <footer
      className={cn(
        "z-50 flex select-none items-center text-[11px] transition-[right,bottom,background-color,width]",
        overlay
          ? "village-task-status-dock pointer-events-auto absolute right-3 h-9 justify-end overflow-hidden rounded-full border"
          : "relative h-9 shrink-0 cursor-pointer justify-between border-t border-white/[0.05] bg-background/82 px-3 backdrop-blur-xl hover:bg-muted/15",
      )}
      data-running={overlay && Boolean(leading) ? "true" : "false"}
      style={overlay ? { bottom: panelOpen ? panelHeight + 28 : 12 } : undefined}
      role={overlay ? undefined : "button"}
      tabIndex={overlay ? -1 : 0}
      aria-label={t("taskCenter.title")}
      aria-expanded={panelOpen}
      onClick={overlay ? undefined : onBarClick}
      onKeyDown={overlay ? undefined : onBarKeyDown}
    >
      <div
        className={cn(
          "flex min-w-0 items-center gap-1.5",
          overlay && "village-task-status-trigger h-full max-w-full cursor-pointer px-3 transition-colors",
        )}
        role={overlay ? "button" : undefined}
        tabIndex={overlay ? 0 : undefined}
        onClick={overlay ? onBarClick : undefined}
        onKeyDown={overlay ? onBarKeyDown : undefined}
      >
        <span className="inline-flex shrink-0 items-center gap-1 text-muted-foreground/90">
          <ChevronUp
            className={cn(
              "size-3 transition-transform duration-200",
              panelOpen && "rotate-180",
            )}
          />
          <span>{t("taskCenter.title")}</span>
        </span>
        {(!overlay || leading) ? (
          <span className="shrink-0 text-muted-foreground/45" aria-hidden>
            ·
          </span>
        ) : null}
        {leading ? (
          <span className="flex min-w-0 items-center gap-1.5 truncate text-muted-foreground">
            <span className="flex shrink-0 items-center gap-1 text-primary">
              <Zap className="size-3" />
              {running.length}
            </span>
            <span className="truncate">{displayLabel(leading, t)}</span>
          </span>
        ) : !overlay && lastDone ? (
          <span
            className={cn(
              "flex items-center gap-1",
              lastDone.status === "failed" ? "text-destructive" : "text-success",
            )}
          >
            {lastDone.status === "failed" ? "✗" : "✓"}{" "}
            <span className="truncate">{displayLabel(lastDone, t)}</span>
            <span className="shrink-0 text-muted-foreground/70">
              · {relativeLabel(lastDone.completed_at)}
            </span>
          </span>
        ) : !overlay ? (
          <span className="text-muted-foreground">
            {t("taskCenter.statusBar.idle")}
          </span>
        ) : null}
        {!overlay && isProgressActive ? (
          <span className="ml-2 hidden shrink-0 items-center gap-1.5 sm:flex">
            <span
              className="size-1.5 shrink-0 rounded-full bg-primary/55 animate-[task-status-breathe_2.4s_ease-in-out_infinite]"
              aria-hidden="true"
            />
            <span
              className="text-[11px] text-muted-foreground"
            >
              {t("taskCenter.statusBar.generationRunning")}
            </span>
            <span
              className="h-0.5 w-16 overflow-hidden rounded-full bg-white/[0.08]"
              aria-label={t("taskCenter.statusBar.progress", { percent: progress })}
            >
              <span
                className={cn("block h-full rounded-full transition-[width] duration-300", progressTone)}
                style={{ width: `${progress}%` }}
              />
            </span>
            <span className="w-8 text-right tabular-nums text-muted-foreground">
              {progress}%
            </span>
          </span>
        ) : null}
      </div>
      {!overlay ? <div className="flex shrink-0 items-center gap-2 text-muted-foreground">
        {projectId ? (
          <span className="ml-1.5 flex items-center gap-1">
            <Circle className={cn("size-1 fill-current", HEALTH_COLOR[health])} />
            <span className="sr-only" aria-live="polite">
              {t(`taskCenter.statusBar.${health}`)}
            </span>
            <span aria-hidden="true">{t(`taskCenter.statusBar.${health}`)}</span>
          </span>
        ) : null}
        <span
          className="shrink-0 font-normal tabular-nums text-muted-foreground/72"
          title={APP_VERSION}
        >
          {APP_VERSION}
        </span>
        <RegionBadge />
      </div> : null}
    </footer>
  );
}
