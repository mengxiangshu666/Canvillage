// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Link } from "@tanstack/react-router";
import { Copy, Crosshair, Download, ExternalLink, Trash2, Workflow, XCircle } from "lucide-react";

import { useCancelTask, useDeleteTask } from "@/lib/queries/tasks";
import { isActive, isTerminal, originDeepLink, taskCanvasNodeId } from "@/task-center/derivations";
import type { TaskState } from "@/task-center/types";
import { Button, buttonVariants } from "@/components/ui/button";
import { useCanvasStore } from "@/stores/canvasStore";
import { useAppStore } from "@/stores/app-store";
import { useTaskCenterStore } from "@/task-center/store";

export function TaskActions({ task }: { task: TaskState }) {
  const { t } = useTranslation();
  const cancelMut = useCancelTask();
  const deleteMut = useDeleteTask();
  const deepLink = originDeepLink(task);
  const nodeId = taskCanvasNodeId(task);
  const workflowRunId = task.metadata?.workflow_run_id;
  const workflowCanvasId = task.metadata?.canvas_id;
  const workflowLink =
    typeof workflowRunId === "string" && workflowRunId.length > 0 &&
    typeof workflowCanvasId === "string" && workflowCanvasId.length > 0 &&
    (task.project_id ?? task.project)
      ? {
          project: task.project_id ?? task.project,
          canvas: workflowCanvasId,
          workflowRunId,
        }
      : null;

  // useCancelTask now accepts `beatNum` + `scope`, so every active task is
  // precisely cancellable. The old "hide for scoped" guard is removed.
  const onCancel = () => {
    cancelMut.mutate(
      {
        type: task.task_type,
        project: task.project_id ?? task.project,
        episode: task.episode,
        beatNum: task.beat_num ?? undefined,
        scope: task.scope ?? undefined,
      },
      {
        onSuccess: () =>
          toast.success(
            t("taskCenter.toast.canceled", { label: task.task_type }),
          ),
      },
    );
  };

  const onCopyId = async () => {
    try {
      await navigator.clipboard.writeText(task.task_id);
      toast.success(t("taskCenter.toast.copied"));
    } catch {
      // Clipboard API may fail in non-HTTPS / non-focused contexts. Silent OK.
    }
  };

  const onDownloadLogs = () => {
    const blob = new Blob([task.logs.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${task.task_key}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const onDelete = () => {
    if (!window.confirm(t("taskCenter.actions.deleteConfirm"))) return;
    deleteMut.mutate(
      {
        type: task.task_type,
        project: task.project_id ?? task.project,
        episode: task.episode,
        beatNum: task.beat_num ?? undefined,
        scope: task.scope ?? undefined,
      },
      {
        onSuccess: () => {
          toast.success(t("taskCenter.toast.deleted"));
          useTaskCenterStore.getState().remove(task.task_key);
        },
      },
    );
  };

  const onLocateNode = () => {
    if (!nodeId) return;
    const store = useCanvasStore.getState();
    if (!store.nodes.some((node) => node.id === nodeId)) {
      toast.error(t("taskCenter.toast.nodeMissing"));
      return;
    }
    store.setSelectedNode(nodeId);
    store.requestFocusNode(nodeId);
    useAppStore.getState().setTaskPanelOpen(false);
  };

  return (
    <div className="flex min-h-12 shrink-0 items-center gap-1.5 border-t border-white/[0.07] bg-black/12 px-4 py-2">
      {isActive(task) && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={cancelMut.isPending}
        >
          <XCircle className="size-4" />
          {t("taskCenter.actions.cancel")}
        </Button>
      )}
      {deepLink && (
        <Button
          variant="ghost"
          size="sm"
          render={<Link to={deepLink.to} params={deepLink.params} />}
        >
          <ExternalLink className="size-4" />
          {t("taskCenter.actions.openOrigin")}
        </Button>
      )}
      {workflowLink && (
        <Link
          className={buttonVariants({ variant: "ghost", size: "sm" })}
          to="/projects/$project/production"
          params={{ project: workflowLink.project }}
          search={
            {
              canvas: workflowLink.canvas,
              workflowRunId: workflowLink.workflowRunId,
            } as never
          }
        >
          <Workflow className="size-4" />
          {t("taskCenter.actions.openWorkflow")}
        </Link>
      )}
      {isTerminal(task) && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onDelete}
          disabled={deleteMut.isPending}
          className="text-muted-foreground/70 hover:bg-destructive/10 hover:text-destructive"
        >
          <Trash2 className="size-4" />
          {t("taskCenter.actions.delete")}
        </Button>
      )}
      {nodeId && (
        <Button variant="default" size="sm" onClick={onLocateNode}>
          <Crosshair className="size-4" />
          {t("taskCenter.actions.locateNode")}
        </Button>
      )}
      <Button variant="ghost" size="sm" onClick={onCopyId}>
        <Copy className="size-4" />
        {t("taskCenter.actions.copyId")}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={onDownloadLogs}
        disabled={!task.logs.length}
      >
        <Download className="size-4" />
        {t("taskCenter.actions.downloadLogs")}
      </Button>
    </div>
  );
}
