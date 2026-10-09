// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { CanvasBackupStatus } from "@/api/canvas";
import { cn } from "@/lib/utils";
import type { ConflictSnapshot } from "./useCanvasSync";

export function Toast({ text, onClose }: { text: string; onClose: () => void }) {
  return (
    <div className="absolute left-1/2 top-6 z-40 max-w-md -translate-x-1/2 rounded-lg border border-border-default bg-surface/95 px-4 py-2 text-sm text-text shadow-xl backdrop-blur">
      <div className="flex items-center gap-3">
        <span className="break-words flex-1 min-w-0">{text}</span>
        <button
          type="button"
          onClick={onClose}
          className="text-text-muted hover:text-text text-xs"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

export function CanvasConflictOverlay({
  error,
  canvasId,
  onRefresh,
  onSaveCopy,
  readConflictSnapshot,
}: {
  error: string | null;
  canvasId: string;
  onRefresh: () => void;
  onSaveCopy: () => Promise<void>;
  readConflictSnapshot: () => ConflictSnapshot | null;
}) {
  const { t } = useTranslation();
  const [savingCopy, setSavingCopy] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  // Read once on mount so the "下载本地 JSON" button always renders against
  // the snapshot captured at the moment the 409 fired, even if a later save
  // would have rewritten it.
  const snapshot = useMemo(() => readConflictSnapshot(), [readConflictSnapshot]);

  const handleDownload = () => {
    if (!snapshot) return;
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    const stamp = snapshot.timestamp
      ? snapshot.timestamp.replace(/[:.]/g, "-")
      : new Date().toISOString().replace(/[:.]/g, "-");
    anchor.download = `freezone-${canvasId}-conflict-${stamp}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="absolute inset-0 bg-bg-dark/60 flex items-center justify-center">
      <div className="px-4 py-3 rounded-lg bg-surface border border-amber-400/50 text-sm text-amber-100 max-w-md flex flex-col gap-3">
        <div className="font-medium">画布保存冲突</div>
        <div className="text-text-muted">
          {error ?? "画布已被其他窗口或用户修改。刷新会丢弃当前本地未保存修改，另存为副本会保留当前画布。"}
        </div>
        {snapshot && (
          <div className="text-[11px] text-text-muted/80">
            本地未保存修改已暂存到浏览器，可下载备份后再决定是否刷新。
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onRefresh}
            className="px-3 py-1 rounded-md border border-amber-400/40 text-amber-100 hover:bg-amber-400/10 transition-colors"
          >
            刷新
          </button>
          <button
            type="button"
            disabled={savingCopy || !snapshot}
            onClick={() => {
              setSavingCopy(true);
              setCopyError(null);
              onSaveCopy()
                .catch((err) => {
                  setCopyError(err instanceof Error ? err.message : String(err));
                })
                .finally(() => setSavingCopy(false));
            }}
            className="px-3 py-1 rounded-md border border-cyan-300/45 bg-cyan-400/18 text-cyan-50 shadow-[0_0_18px_rgba(34,211,238,0.12)] transition-colors hover:border-cyan-200/70 hover:bg-cyan-400/28 disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-white/30 disabled:shadow-none"
            title={snapshot ? undefined : t("freezone.canvases.noConflictSnapshot")}
          >
            {savingCopy ? "保存中..." : "另存为副本"}
          </button>
          {snapshot && (
            <button
              type="button"
              onClick={handleDownload}
              className="px-3 py-1 rounded-md border border-[var(--ui-border-soft)] text-text hover:bg-bg-dark/50 transition-colors"
              title={`下载本地修改快照（${snapshot.nodes.length} 节点 · ${snapshot.edges.length} 连线）`}
            >
              下载本地 JSON
            </button>
          )}
        </div>
        {copyError && (
          <div className="text-[11px] text-red-300">
            {copyError}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Lightweight indicator for the backend's `backup_status` channel. Only
 * renders for `pending` (still uploading to OSS) and `failed` (local save is
 * durable but OSS replication did not stick); `synced` / `disabled` / `null`
 * stay silent so the canvas does not gain chrome for the happy path.
 *
 * The badge floats above ReactFlow's bottom-right zoom controls
 * (`bottom-3 right-3` is taken by `MiniMap`; the offset puts us just
 * above it without overlapping).
 */
export function BackupStatusIndicator({
  status,
}: {
  status: CanvasBackupStatus | null;
}) {
  if (status !== "pending" && status !== "failed") {
    return null;
  }
  const isFailed = status === "failed";
  const label = isFailed ? "云端备份失败" : "云端备份中";
  const detail = isFailed
    ? "本地修改已保存，但云端备份未完成。请保留页面，稍后会自动重试。"
    : "本地修改已保存，云端备份还在同步中。可以继续编辑。";
  const palette = isFailed
    ? "border-red-500/45 bg-red-500/10 text-red-200"
    : "border-amber-300/40 bg-amber-300/10 text-amber-100";
  const dot = isFailed ? "bg-red-400" : "bg-amber-300 animate-pulse";
  return (
    <div
      role={isFailed ? "alert" : "status"}
      className={`village-canvas-status absolute bottom-16 right-3 z-30 inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] leading-none shadow-sm ${palette}`}
      title={detail}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {label}
    </div>
  );
}

export function CanvasMutationOutboxIndicator({
  status,
}: {
  status: {
    status: "healthy" | "degraded";
    pendingCount: number;
  };
}) {
  const hasPending = status.pendingCount > 0;
  if (status.status === "healthy" && !hasPending) return null;

  const degraded = status.status === "degraded";
  const label = degraded
    ? hasPending
      ? `本地恢复已降级 · 待确认 ${status.pendingCount} 项`
      : "本地恢复已降级"
    : `待确认 ${status.pendingCount} 项`;
  const detail = degraded
    ? "浏览器本地持久暂存暂时不可用，当前操作仍保留在页面内；恢复后会自动写回。"
    : "画布操作已即时显示，正在等待权威保存确认。刷新前请保留当前页面。";
  const palette = degraded
    ? "border-red-400/45 bg-red-500/10 text-red-100"
    : "border-amber-300/40 bg-amber-300/10 text-amber-100";
  const dot = degraded ? "bg-red-400" : "bg-amber-300 animate-pulse";
  return (
    <div
      role={degraded ? "alert" : "status"}
      className={`village-canvas-status absolute bottom-24 right-3 z-30 inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] leading-none shadow-sm ${palette}`}
      title={detail}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {label}
    </div>
  );
}

/**
 * Non-blocking warning for a save that failed without any server answer
 * (offline / timeout / transient 5xx). The local copy is intact and autosave
 * keeps retrying with backoff, so unlike `CanvasErrorOverlay` this must not
 * cover the canvas — the user can keep editing while it reconnects.
 */
export function CanvasOfflineSaveBanner({
  message,
  onRetry,
}: {
  message: string | null;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="village-canvas-offline-banner absolute left-1/2 top-3 z-30 flex max-w-[min(560px,calc(100%-24px))] -translate-x-1/2 items-center gap-3 rounded-lg border border-amber-300/40 bg-amber-300/10 px-3 py-2 text-xs text-amber-100 shadow-lg backdrop-blur-xl"
    >
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-300 animate-pulse" />
      <span className="min-w-0 flex-1 break-words leading-5">
        {message ?? "保存暂时失败，正在自动重试"}
      </span>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 rounded-md border border-amber-200/35 px-2 py-0.5 text-[11px] font-medium text-amber-50 transition-colors hover:border-amber-100/60 hover:bg-amber-200/10"
      >
        立即重试
      </button>
    </div>
  );
}

export function CanvasLoadingScreen() {
  return (
    <div className="village-canvas-loading flex h-full w-full items-center justify-center">
      <div className="village-canvas-loading-card" role="status" aria-live="polite">
        <span className="village-canvas-loading-mark" aria-hidden="true" />
        <span className="village-canvas-loading-title">正在加载画布</span>
        <span className="village-canvas-loading-detail">正在同步当前工作区</span>
      </div>
    </div>
  );
}

export function CanvasLoadingOverlay() {
  // hydrate 还在飞时画布上的编辑既不会入队保存，也会被随后的 setCanvasData(remote)
  // 整个盖掉。所以这层遮罩必须真的吃掉指针事件，不能只是视觉上蒙一层。
  return (
    <div
      className={cn(
        "absolute inset-0 z-20 cursor-wait bg-bg-dark/10 backdrop-blur-[1px]",
        "village-canvas-loading-overlay",
      )}
      aria-hidden="true"
    />
  );
}

export function CanvasErrorOverlay({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <div className="village-canvas-error absolute inset-0 flex items-center justify-center bg-bg-dark/45 px-6">
      <div className="flex w-full max-w-2xl flex-col gap-3 rounded-xl border border-red-400/25 bg-red-950/[0.14] px-4 py-3 text-sm shadow-[0_18px_60px_rgba(0,0,0,0.28)] backdrop-blur-xl">
        <div className="font-medium text-red-200">画布同步失败</div>
        <div className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2 text-xs leading-5 text-red-100/75">
          {error}
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="self-start rounded-lg border border-red-300/25 bg-red-950/20 px-3 py-1.5 text-xs font-medium text-red-100/80 transition-colors hover:border-red-200/40 hover:bg-red-500/10 hover:text-red-50"
        >
          重试
        </button>
      </div>
    </div>
  );
}
