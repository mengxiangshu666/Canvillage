// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ChevronDown,
  CircleDashed,
  Clapperboard,
  FileVideo,
  FileText,
  Image,
  Layers3,
  LoaderCircle,
  MoreHorizontal,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Settings2,
  Sparkles,
  Square,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/shadcn/dropdown-menu";
import { WorkflowRunOverview } from "@/features/workflow-runs/workflow-run-overview";
import {
  useProductionControl,
  useProductionOverview,
  useProductionRunCommand,
  useStartProductionRun,
  useUploadProductionNovel,
} from "@/lib/queries/production";
import {
  useModelGatewayConfig,
  type DirectModelConfig,
} from "@/lib/queries/model-gateway";
import { requiresPaidMediaConfirmation } from "@/lib/api-errors";
import { cn } from "@/lib/utils";
import type {
  ProductionAspectRatio,
  ProductionChildExecution,
  ProductionControlRun,
  ProductionControlStart,
  ProductionEntryMode,
  ProductionFinalComposeReceipt,
  ProductionPipelineContract,
  ProductionPipelineRuntime,
  ProductionQualityGateReport,
  ProductionCostSummary,
  ProductionRunCommand,
  ProductionRunStatus,
} from "@/types/production";

export const Route = createFileRoute("/_app/projects/$project/production")({
  component: ProductionCommandCenter,
});

const STATUS_LABEL: Record<ProductionRunStatus, string> = {
  running: "制作中",
  pausing: "正在暂停",
  paused: "已暂停",
  blocked: "需要处理",
  completed: "已完成",
  failed: "执行失败",
  cancelled: "已终止",
};

const STATUS_TONE: Record<ProductionRunStatus, string> = {
  running: "border-sky-400/25 bg-sky-400/[0.08] text-sky-300",
  pausing: "border-amber-400/25 bg-amber-400/[0.08] text-amber-300",
  paused: "border-amber-400/25 bg-amber-400/[0.08] text-amber-300",
  blocked: "border-orange-400/25 bg-orange-400/[0.08] text-orange-300",
  completed: "border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-300",
  failed: "border-red-400/25 bg-red-400/[0.08] text-red-300",
  cancelled: "border-white/10 bg-white/[0.03] text-muted-foreground",
};

const STAGES = [
  {
    id: "story",
    label: "故事",
    description: "故事来源与分集",
    icon: FileText,
    route: "/projects/$project/story-lab" as const,
  },
  {
    id: "assets",
    label: "资产",
    description: "角色、场景与道具",
    icon: Image,
    route: "/projects/$project/characters" as const,
  },
  {
    id: "storyboard",
    label: "分镜",
    description: "剧本、镜头与画面",
    icon: Layers3,
    route: "/projects/$project/episodes" as const,
  },
  {
    id: "making",
    label: "制作",
    description: "声音、视频与成片",
    icon: Clapperboard,
    route: "/projects/$project/making" as const,
  },
] as const;

const ACTION_STAGE: Record<string, (typeof STAGES)[number]["id"]> = {
  ingest_fast: "story",
  configure: "story",
  build_characters: "assets",
  foundation_refs: "assets",
  portraits: "assets",
  identity_planner: "assets",
  identity_images: "assets",
  episode_scene_planner: "assets",
  episode_prop_planner: "assets",
  build_episodes: "storyboard",
  script_writer: "storyboard",
  sketch_generation: "storyboard",
  coloring: "storyboard",
  global_optimize_video: "storyboard",
  selected_regen: "storyboard",
  tts: "making",
  single_video: "making",
  compose_episode: "making",
  done: "making",
};

const ACTION_LABEL: Record<string, string> = {
  ingest_fast: "导入故事",
  configure: "配置项目",
  build_characters: "提炼角色",
  foundation_refs: "生成基础资产",
  build_episodes: "规划分集",
  portraits: "生成角色基准图",
  identity_planner: "规划角色身份",
  identity_images: "生成身份图",
  episode_scene_planner: "规划场景",
  episode_prop_planner: "规划道具",
  script_writer: "生成剧本与镜头",
  sketch_generation: "生成草图",
  coloring: "识别草图内容",
  global_optimize_video: "优化视频提示词",
  selected_regen: "生成正式画面",
  tts: "生成声音",
  single_video: "生成镜头视频",
  compose_episode: "合成成片",
  done: "全部完成",
};

const MODEL_ROLE_LABEL: Record<string, string> = {
  director: "导演",
  text: "文字",
  vision: "视觉",
  image: "图片",
  video: "视频",
  audio: "声音",
  embedding: "知识检索",
};

const SHARED_LLM_ROLES = [
  ["director", "agent"],
  ["text", "text"],
  ["vision", "vision"],
] as const;

const MODEL_SELECTOR_ROLES = [
  ["image", "image"],
  ["video", "video"],
  ["audio", "audio"],
  ["embedding", "embedding"],
] as const;

const DELIVERY_LEVEL_LABEL: Record<string, string> = {
  idea: "创意确认",
  storyboard: "分镜草案",
  shot_draft: "镜头草稿",
  media_draft: "媒体草稿",
  final_film: "最终成片",
};

const CONTRACT_STAGE_LABEL: Record<string, string> = {
  brief: "目标",
  canvas_scaffold: "画布骨架",
  storyboard: "故事与分镜",
  assets: "角色与资产",
  media: "媒体生成",
  review: "质量验收",
  delivery: "交付",
};

const CONTRACT_STAGE_STATUS_LABEL: Record<string, string> = {
  pending: "待开始",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  blocked: "受阻",
  paused: "已暂停",
  waiting_confirmation: "待确认",
  deferred: "已延后",
  not_requested: "未请求",
};

const CONTRACT_STAGE_STATUS_TONE: Record<string, string> = {
  running: "text-sky-300",
  completed: "text-emerald-300",
  failed: "text-red-300",
  blocked: "text-orange-300",
  paused: "text-amber-300",
  waiting_confirmation: "text-amber-300",
  deferred: "text-muted-foreground",
  not_requested: "text-muted-foreground/70",
  pending: "text-muted-foreground",
};

const QUALITY_STATUS_LABEL: Record<string, string> = {
  passed: "通过",
  failed: "失败",
  pending: "待验收",
  blocked: "受阻",
  waiting_confirmation: "待确认",
  not_run: "未运行",
};

const FINAL_COMPOSE_STATUS_LABEL: Record<string, string> = {
  ready: "已就绪",
  running: "合成中",
  failed: "合成失败",
  blocked: "需要处理",
  cancelled: "已终止",
};

function productionCostValues(value?: Record<string, number>) {
  if (!value) return "—";
  const entries = Object.entries(value);
  return entries.length
    ? entries.map(([key, amount]) => `${key} ${amount}`).join(" · ")
    : "—";
}

function formatDurationMs(value?: number) {
  if (!value || value < 1) return "—";
  const seconds = Math.round(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function formatBytes(value?: number) {
  if (!value || value < 1) return "—";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function contractStageStatusTone(status: string) {
  return CONTRACT_STAGE_STATUS_TONE[status] || "text-muted-foreground";
}

function contractStageStatusLabel(status: string) {
  return CONTRACT_STAGE_STATUS_LABEL[status] || status || "待开始";
}

function qualityStatusLabel(status: string) {
  return QUALITY_STATUS_LABEL[status] || status || "待验收";
}

const ACTIVE_TASK_STATUSES = new Set([
  "submitting",
  "queued",
  "pending",
  "starting",
  "running",
]);

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}

function stageState(
  stageId: string,
  ready: boolean,
  children: ProductionChildExecution[],
  currentStage: string,
) {
  const stageChildren = children.filter((child) => child.stage_id === stageId);
  const failed = stageChildren.some((child) =>
    ["failed", "cancelled"].includes(child.status),
  );
  const active = stageChildren.some((child) => ACTIVE_TASK_STATUSES.has(child.status));
  if (failed) return { label: "需要处理", tone: "text-orange-300", active: false };
  if (active || currentStage === stageId) {
    return { label: "进行中", tone: "text-sky-300", active: true };
  }
  if (ready) return { label: "已就绪", tone: "text-emerald-300", active: false };
  return { label: "待开始", tone: "text-muted-foreground", active: false };
}

function primaryAction(run: ProductionControlRun | null) {
  if (!run || ["completed", "cancelled"].includes(run.status)) {
    return { label: "开始制作", icon: Sparkles, action: "start" as const };
  }
  if (["running", "pausing"].includes(run.status)) {
    return { label: "查看当前进度", icon: RefreshCw, action: "view" as const };
  }
  if (run.status === "paused") {
    return { label: "继续制作", icon: Play, action: "resume" as const };
  }
  return { label: "处理失败项", icon: RotateCcw, action: "retry" as const };
}

function ProductionContractPanel({
  contract,
  runtime,
  deliveryLevel,
  contractRevision,
  qualityGateReport,
  costSummary,
  finalComposeReceipt,
}: {
  contract?: ProductionPipelineContract | null;
  runtime?: ProductionPipelineRuntime | null;
  deliveryLevel?: string;
  contractRevision?: string;
  qualityGateReport?: ProductionQualityGateReport | null;
  costSummary?: ProductionCostSummary | null;
  finalComposeReceipt?: ProductionFinalComposeReceipt | null;
}) {
  const effectiveRuntime = runtime ?? contract?.runtime;
  const effectiveDeliveryLevel =
    deliveryLevel || contract?.delivery_level || "";
  const effectiveRevision = contractRevision || contract?.contract_revision || "";
  const effectiveQuality =
    qualityGateReport ??
    effectiveRuntime?.quality_gate_report ??
    contract?.quality_gate_report ??
    null;
  const effectiveCost =
    costSummary ?? effectiveRuntime?.cost_summary ?? contract?.cost_summary ?? null;
  const effectiveReceipt =
    finalComposeReceipt ??
    effectiveRuntime?.final_compose_receipt ??
    contract?.final_compose_receipt ??
    null;
  const runtimeStages =
    effectiveRuntime?.stage_statuses ?? contract?.stage_statuses ?? [];
  const stageSpecs = contract?.stages ?? [];
  const stageRows = stageSpecs.length
    ? stageSpecs.map((stage) => {
        const projected = runtimeStages.find((item) => item.id === stage.id);
        const execution = projected?.execution ?? stage.execution;
        const status =
          projected?.status ??
          (execution === "deferred"
            ? "deferred"
            : execution === "not_requested"
              ? "not_requested"
              : "pending");
        return {
          id: stage.id,
          label: stage.label || CONTRACT_STAGE_LABEL[stage.id] || stage.id,
          execution,
          status,
          current: Boolean(projected?.current),
          progress: projected?.progress,
        };
      })
    : runtimeStages.map((stage) => ({
        id: stage.id,
        label: CONTRACT_STAGE_LABEL[stage.id] || stage.id,
        execution: stage.execution,
        status: stage.status || "pending",
        current: Boolean(stage.current),
        progress: stage.progress,
      }));
  const qualityStatus = effectiveQuality?.status || "pending";
  const qualityGates = Object.entries(effectiveQuality?.gate_statuses ?? {});
  const receiptStatus = effectiveReceipt?.status || "pending";
  const contractVisible = Boolean(
    contract || effectiveQuality || effectiveCost || effectiveReceipt,
  );

  if (!contractVisible) return null;

  return (
    <section className="overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.018]">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.06] p-4 sm:p-5">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">生产合同与交付</h2>
            {effectiveDeliveryLevel ? (
              <Badge variant="outline" className="border-primary/25 bg-primary/[0.08] text-primary-foreground">
                {DELIVERY_LEVEL_LABEL[effectiveDeliveryLevel] || effectiveDeliveryLevel}
              </Badge>
            ) : null}
            {contract?.run_mode ? (
              <span className="text-[10px] text-muted-foreground">
                {contract.run_mode === "auto" ? "自动执行" : "草稿执行"}
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            合同驱动当前阶段与质量门，实时结果来自同一条 WorkflowRun。
          </p>
        </div>
        {effectiveRevision ? (
          <span
            className="max-w-full truncate font-mono text-[10px] text-muted-foreground/60 sm:max-w-[260px]"
            title={effectiveRevision}
          >
            {effectiveRevision}
          </span>
        ) : null}
      </div>

      {stageRows.length ? (
        <div className="grid gap-px bg-white/[0.06] sm:grid-cols-2 lg:grid-cols-4">
          {stageRows.map((stage) => {
            const normalizedProgress = Math.max(
              0,
              Math.min(1, Number(stage.progress ?? 0)),
            );
            const status = String(stage.status || "pending");
            return (
              <div key={stage.id} className="min-w-0 bg-[#101012] px-3.5 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-xs font-medium text-white/85">
                    {stage.label}
                  </span>
                  <span className={cn("shrink-0 text-[10px]", contractStageStatusTone(status))}>
                    {stage.current && status === "running" && stage.progress != null
                      ? `${Math.round(normalizedProgress * 100)}%`
                      : contractStageStatusLabel(status)}
                  </span>
                </div>
                <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/[0.07]">
                  <div
                    className={cn(
                      "h-full rounded-full transition-[width] duration-500",
                      status === "failed" || status === "blocked"
                        ? "bg-orange-300"
                        : status === "completed"
                          ? "bg-emerald-300"
                          : "bg-sky-300",
                    )}
                    style={{
                      width:
                        status === "completed"
                          ? "100%"
                          : `${Math.round(normalizedProgress * 100)}%`,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="grid gap-3 border-t border-white/[0.06] p-4 sm:grid-cols-2 sm:p-5">
        <div className="min-w-0">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-xs font-medium text-white/85">质量门</h3>
            <span
              className={cn(
                "text-[10px]",
                qualityStatus === "passed"
                  ? "text-emerald-300"
                  : qualityStatus === "failed" || qualityStatus === "blocked"
                    ? "text-orange-300"
                    : "text-muted-foreground",
              )}
            >
              {qualityStatusLabel(qualityStatus)}
            </span>
          </div>
          {qualityGates.length ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {qualityGates.map(([gate, status]) => (
                <span
                  key={gate}
                  className={cn(
                    "rounded-md border px-2 py-1 text-[10px]",
                    status === "passed"
                      ? "border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-200"
                      : status === "failed"
                        ? "border-red-400/20 bg-red-400/[0.06] text-red-200"
                        : "border-white/[0.08] bg-white/[0.03] text-muted-foreground",
                  )}
                  title={gate}
                >
                  {gate}: {qualityStatusLabel(status)}
                </span>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-[11px] text-muted-foreground">尚无质量门运行证据。</p>
          )}
        </div>

        <div className="min-w-0">
          <div className="flex items-center justify-between gap-3">
            <h3 className="flex items-center gap-1.5 text-xs font-medium text-white/85">
              <FileVideo className="size-3.5 text-muted-foreground" />最终成片
            </h3>
            <span
              className={cn(
                "text-[10px]",
                receiptStatus === "ready"
                  ? "text-emerald-300"
                  : receiptStatus === "failed" || receiptStatus === "blocked"
                    ? "text-orange-300"
                    : "text-muted-foreground",
              )}
            >
              {FINAL_COMPOSE_STATUS_LABEL[receiptStatus] || "待合成"}
            </span>
          </div>
          {effectiveReceipt ? (
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
              <span className="max-w-full truncate text-white/75" title={effectiveReceipt.filename}>
                {effectiveReceipt.filename || "尚未生成文件"}
              </span>
              {effectiveReceipt.size_bytes ? <span>{formatBytes(effectiveReceipt.size_bytes)}</span> : null}
              {effectiveReceipt.artifact_url ? (
                <a
                  className="text-primary underline-offset-2 hover:underline"
                  href={effectiveReceipt.artifact_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {effectiveReceipt.previous_output_available
                    ? "打开上次成片"
                    : "打开文件"}
                </a>
              ) : null}
            </div>
          ) : (
            <p className="mt-2 text-[11px] text-muted-foreground">最终成片回执尚未产生。</p>
          )}
          {effectiveReceipt?.previous_output_available ? (
            <p className="mt-1 text-[10px] text-amber-200/75">
              当前合成未完成，现有文件可能是此前版本。
            </p>
          ) : null}
        </div>
      </div>

      <div className="border-t border-white/[0.06] px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[11px]">
          <span className="font-medium text-white/75">成本回执</span>
          {effectiveCost?.receipt_count ? (
            <span className="text-muted-foreground">
              {effectiveCost.receipt_count} 条 · {effectiveCost.quantity ?? 0} 个媒体 · 耗时 {formatDurationMs(effectiveCost.duration_ms)}
            </span>
          ) : (
            <span className="text-muted-foreground">尚无媒体成本回执</span>
          )}
        </div>
        {effectiveCost?.receipt_count ? (
          <div className="mt-2 grid gap-x-5 gap-y-1 text-[10px] text-muted-foreground sm:grid-cols-2 lg:grid-cols-4">
            <span>预估：{productionCostValues(effectiveCost.estimated_cost)}</span>
            <span>已占用：{productionCostValues(effectiveCost.reserved_cost)}</span>
            <span className="text-white/70">实际：{productionCostValues(effectiveCost.actual_cost)}</span>
            <span>浪费：{productionCostValues(effectiveCost.wasted_cost)}</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function ProductionCommandCenter() {
  const { project } = Route.useParams();
  const overview = useProductionOverview(project);
  const control = useProductionControl(project);
  const upload = useUploadProductionNovel(project);
  const startRun = useStartProductionRun(project);
  const commandRun = useProductionRunCommand(project);
  const modelCatalog = useModelGatewayConfig();
  const [uploadedFilename, setUploadedFilename] = useState("");
  const [localFilename, setLocalFilename] = useState("");
  const [targetEpisodes, setTargetEpisodes] = useState(1);
  const [aspectRatio, setAspectRatio] = useState<ProductionAspectRatio | "">("");
  const [autoPaidMedia, setAutoPaidMedia] = useState(false);
  const [entryMode, setEntryMode] = useState<ProductionEntryMode>("novel_adapt");
  const [originalGoal, setOriginalGoal] = useState("");
  const [originalScript, setOriginalScript] = useState("");
  const [selectedModelBindings, setSelectedModelBindings] = useState<Record<string, string>>({});

  const snapshot = control.data;
  const run = snapshot?.latest_run ?? null;
  const children = snapshot?.child_executions ?? [];
  const history = snapshot?.history ?? [];
  const effectiveFilename =
    uploadedFilename || snapshot?.uploaded_filename?.trim() || "";
  const nextAction = snapshot?.next_action;
  const currentAction = run?.current_action || nextAction?.id || "";
  const currentStage = ACTION_STAGE[currentAction] || "story";
  const activeStage = run && ["running", "pausing"].includes(run.status)
    ? currentStage
    : "";
  const mutationBusy = upload.isPending || startRun.isPending || commandRun.isPending;
  const needsUpload = entryMode === "novel_adapt"
    && Boolean(nextAction?.requires_upload && !effectiveFilename);
  const originalInputMissing = entryMode === "original"
    && !originalGoal.trim()
    && !originalScript.trim();
  const action = primaryAction(run);
  const ActionIcon = action.icon;

  const stages = useMemo(() => {
    const readyById = new Map(
      (overview.data?.stage_summary ?? []).map((stage) => [stage.id, stage]),
    );
    return STAGES.map((stage) => {
      const summary = readyById.get(stage.id);
      const stageChildren = children.filter((child) => child.stage_id === stage.id);
      const state = stageState(
        stage.id,
        summary?.status === "ready",
        children,
        activeStage,
      );
      const progressValues = stageChildren
        .filter((child) => ACTIVE_TASK_STATUSES.has(child.status))
        .map((child) => Math.max(0, Math.min(1, child.progress || 0)));
      const progress = progressValues.length
        ? Math.round(
            (progressValues.reduce((total, value) => total + value, 0) /
              progressValues.length) *
              100,
          )
        : null;
      const note = [...stageChildren]
        .reverse()
        // 「任务记录已不在任务表中」只是无法确认，不该盖掉该阶段最后一条真实进展。
        .filter((child) => !child.record_missing)
        .map((child) => child.summary?.trim())
        .find(
          (value) =>
            value && value !== "完成" && value !== "任务已进入队列",
        );
      return { ...stage, summary, childCount: stageChildren.length, progress, state, note };
    });
  }, [activeStage, children, overview.data?.stage_summary]);

  const completedStages = stages.filter(
    (stage) => stage.summary?.status === "ready",
  ).length;
  const overallProgress = Math.round((completedStages / STAGES.length) * 100);
  const modelPlan = run?.settings.model_plan_snapshot;
  const effectiveModelBindings = Object.fromEntries(
    [
      ...SHARED_LLM_ROLES.map(([role]) => {
      const override = selectedModelBindings[role];
      const value = override === undefined || override === "__shared__"
        ? selectedModelBindings.llm
        : override;
      return [role, value && value !== "__default__" ? value : ""];
      }),
      ...MODEL_SELECTOR_ROLES.map(([role]) => {
        const value = selectedModelBindings[role];
        return [role, value && value !== "__default__" ? value : ""];
      }),
    ],
  );
  const missingRoles = modelPlan?.missing_roles ?? [];
  const blockingMissingRoles = missingRoles.filter(
    (role) => !["audio", "embedding"].includes(role),
  );
  const readOnlyHistory = history.filter((item) => item.id !== run?.id);

  const handleUpload = async (file: File) => {
    setLocalFilename(file.name);
    try {
      const result = await upload.mutateAsync(file);
      setUploadedFilename(result.filename);
      toast.success(`已导入《${file.name}》`);
    } catch (error) {
      setUploadedFilename("");
      toast.error(`导入失败：${errorMessage(error)}`);
    }
  };

  const handleStart = async (mode: "best" | "next" = "best") => {
    const confirmedPaidMedia = autoPaidMedia
      ? window.confirm("允许本次运行自动提交需要费用的图片、配音和视频任务？")
      : false;
    if (autoPaidMedia && !confirmedPaidMedia) return;
    const payload: ProductionControlStart = {
      mode,
      entry_mode: entryMode,
      uploaded_filename: effectiveFilename,
      target_episodes: Math.max(1, Math.min(100, targetEpisodes || 1)),
      episode: null,
      image_model: effectiveModelBindings.image ?? "",
      video_backend: effectiveModelBindings.video ?? "",
      model_bindings: effectiveModelBindings,
      aspect_ratio: aspectRatio || null,
      auto_generate_paid_media: autoPaidMedia,
      confirmed_paid_media: confirmedPaidMedia,
      ...(entryMode === "original"
        ? {
            goal: originalGoal.trim(),
            original_script: originalScript.trim(),
          }
        : {}),
    };
    try {
      await startRun.mutateAsync(payload);
      toast.success(mode === "best" ? "项目制作已开始" : "下一阶段已开始");
    } catch (error) {
      toast.error(`启动失败：${errorMessage(error)}`);
    }
  };

  const handleCommand = async (command: ProductionRunCommand) => {
    if (!run) return;
    const requiresPaidConfirmation =
      ["resume", "retry"].includes(command) && Boolean(nextAction?.paid);
    const confirmedPaidMedia = requiresPaidConfirmation
      ? window.confirm("继续后将进入需要费用的媒体步骤，确认提交吗？")
      : false;
    if (requiresPaidConfirmation && !confirmedPaidMedia) return;
    const submit = (paidConfirmed: boolean) =>
      commandRun.mutateAsync({
        runId: run.id,
        command,
        confirmedPaidMedia: paidConfirmed,
      });
    try {
      await submit(confirmedPaidMedia);
      toast.success(
        command === "pause"
          ? "将在当前阶段结束后暂停"
          : command === "cancel"
            ? "运行已终止"
            : "操作已生效",
      );
    } catch (error) {
      // The server owns the paid gate. Its decision can differ from the
      // client's guess: a failed media stage whose artifact arrived another
      // way leaves the run pointing at the paid step while the pipeline has
      // already moved on. Re-ask with the server's own reason and resend once
      // instead of leaving the user stuck on a 409.
      if (
        !confirmedPaidMedia &&
        ["resume", "retry"].includes(command) &&
        requiresPaidMediaConfirmation(error) &&
        window.confirm("继续这一步可能产生媒体生成费用，确认提交吗？")
      ) {
        try {
          await submit(true);
          toast.success("操作已生效");
        } catch (retryError) {
          toast.error(`操作失败：${errorMessage(retryError)}`);
        }
        return;
      }
      toast.error(`操作失败：${errorMessage(error)}`);
    }
  };

  const handlePrimaryAction = async () => {
    if (action.action === "start") {
      await handleStart("best");
      return;
    }
    if (action.action === "view") {
      await Promise.all([control.refetch(), overview.refetch()]);
      document.getElementById("workflow-progress")?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
      return;
    }
    await handleCommand(action.action);
  };

  return (
    <main className="village-workflow-page -m-6 min-h-[calc(100%+3rem)] bg-[#0b0b0c] px-6 py-7 text-white sm:px-8">
      <div className="mx-auto w-full max-w-6xl space-y-5">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-primary">村长工作流 · 总控</span>
              {run ? (
                <Badge variant="outline" className={STATUS_TONE[run.status]}>
                  {STATUS_LABEL[run.status]}
                </Badge>
              ) : null}
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight">项目制作总控</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              一个主运行贯穿故事、资产、分镜和制作；模型自动取自模型中心。
            </p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void Promise.all([control.refetch(), overview.refetch()])}
            disabled={control.isFetching || overview.isFetching}
          >
            <RefreshCw
              className={cn(
                "size-3.5",
                (control.isFetching || overview.isFetching) && "animate-spin",
              )}
            />
            刷新
          </Button>
        </header>

        <WorkflowRunOverview projectId={project} />

        {control.isLoading || overview.isLoading ? (
          <div className="h-56 animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02]" />
        ) : control.isError || overview.isError || !snapshot ? (
          <section className="rounded-2xl border border-red-400/20 bg-red-400/[0.04] p-6">
            <h2 className="text-sm font-semibold">总控数据没有加载成功</h2>
            <p className="mt-1 text-xs text-muted-foreground">刷新后会继续读取已有运行，不会新建重复任务。</p>
            <Button className="mt-4" size="sm" onClick={() => void control.refetch()}>
              <RefreshCw className="size-3.5" />重新加载
            </Button>
          </section>
        ) : (
          <>
            <section
              id="workflow-progress"
              className="overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.025] shadow-[0_20px_80px_-60px_rgba(255,255,255,0.28)]"
            >
              <div className="border-b border-white/[0.06] p-5 sm:p-6">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      {run ? "当前执行" : "下一步"}
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                      {run?.status === "running" ? (
                        <LoaderCircle className="size-4 animate-spin text-sky-300" />
                      ) : (
                        <CircleDashed className="size-4 text-primary" />
                      )}
                      <h2 className="truncate text-base font-semibold">
                        {run?.current_action
                          ? ACTION_LABEL[run.current_action] || run.current_action
                          : nextAction?.label || "准备开始"}
                      </h2>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {effectiveFilename ? `故事来源：${localFilename || effectiveFilename}` : "可直接使用项目现有成果继续"}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      onClick={() => void handlePrimaryAction()}
                      disabled={mutationBusy || (action.action === "start" && (needsUpload || originalInputMissing))}
                    >
                      {mutationBusy ? (
                        <LoaderCircle className="size-4 animate-spin" />
                      ) : (
                        <ActionIcon className="size-4" />
                      )}
                      {action.label}
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          size="icon"
                          variant="outline"
                          aria-label="更多运行操作"
                          disabled={mutationBusy}
                        >
                          <MoreHorizontal className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-48">
                        <DropdownMenuLabel>运行控制</DropdownMenuLabel>
                        {!run || ["completed", "cancelled"].includes(run.status) ? (
                          <DropdownMenuItem onSelect={() => void handleStart("next")}>
                            <Play />只执行下一阶段
                          </DropdownMenuItem>
                        ) : null}
                        {run?.status === "running" ? (
                          <DropdownMenuItem onSelect={() => void handleCommand("pause")}>
                            <Pause />本阶段后暂停
                          </DropdownMenuItem>
                        ) : null}
                        {run && ["paused", "blocked"].includes(run.status) ? (
                          <DropdownMenuItem onSelect={() => void handleCommand("skip")}>
                            <ChevronDown />跳过当前非必需阶段
                          </DropdownMenuItem>
                        ) : null}
                        {run && ["running", "pausing", "paused", "blocked"].includes(run.status) ? (
                          <DropdownMenuItem onSelect={() => void handleCommand("take_over")}>
                            <Settings2 />人工接管
                          </DropdownMenuItem>
                        ) : null}
                        {run && ["running", "pausing", "paused", "blocked"].includes(run.status) ? (
                          <>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              className="text-red-300 focus:text-red-200"
                              onSelect={() => void handleCommand("cancel")}
                            >
                              <Square />终止运行
                            </DropdownMenuItem>
                          </>
                        ) : null}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>

                {run?.error ? (
                  <div className="mt-4 flex items-start gap-2 rounded-xl border border-orange-400/20 bg-orange-400/[0.05] px-3.5 py-3 text-xs text-orange-100">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-orange-300" />
                    <span>{run.error}</span>
                  </div>
                ) : null}

                {needsUpload ? (
                  <label className="mt-4 flex cursor-pointer items-center justify-between gap-4 rounded-xl border border-dashed border-primary/30 bg-primary/[0.04] p-4 transition-colors hover:bg-primary/[0.07]">
                    <span>
                      <strong className="block text-sm">导入故事文件</strong>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        支持当前故事导入格式，大文件不再受 30 秒前端超时限制。
                      </span>
                    </span>
                    <span className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground">
                      {upload.isPending ? <LoaderCircle className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
                      选择文件
                    </span>
                    <input
                      className="sr-only"
                      type="file"
                      disabled={upload.isPending}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void handleUpload(file);
                        event.currentTarget.value = "";
                      }}
                    />
                  </label>
                ) : null}
              </div>

              <div className="grid divide-y divide-white/[0.06] sm:grid-cols-4 sm:divide-x sm:divide-y-0">
                {stages.map((stage, index) => {
                  const Icon = stage.icon;
                  return (
                    <Link
                      key={stage.id}
                      to={stage.route}
                      params={{ project }}
                      className="group relative p-4 transition-colors hover:bg-white/[0.035]"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="flex size-8 items-center justify-center rounded-lg bg-white/[0.045] text-muted-foreground transition-colors group-hover:text-white">
                          <Icon className="size-4" />
                        </span>
                        <span className={cn("text-[11px]", stage.state.tone)}>
                          {stage.state.active && stage.progress !== null
                            ? `${stage.progress}%`
                            : stage.state.label}
                        </span>
                      </div>
                      <h3 className="mt-3 text-sm font-semibold">
                        <span className="mr-1.5 text-muted-foreground">{index + 1}</span>
                        {stage.label}
                      </h3>
                      <p className="mt-1 text-[11px] text-muted-foreground">{stage.description}</p>
                      {stage.childCount > 0 ? (
                        <p className="mt-2 text-[10px] text-muted-foreground/70">
                          {stage.note || `${stage.childCount} 个真实子任务`}
                        </p>
                      ) : null}
                    </Link>
                  );
                })}
              </div>

              <div className="h-1 bg-white/[0.04]">
                <div
                  className="h-full bg-primary transition-[width] duration-500 ease-out"
                  style={{ width: `${overallProgress}%` }}
                />
              </div>
            </section>

            <ProductionContractPanel
              contract={snapshot.production_contract}
              runtime={snapshot.production_contract_runtime}
              deliveryLevel={snapshot.delivery_level}
              contractRevision={snapshot.contract_revision}
              qualityGateReport={snapshot.quality_gate_report}
              costSummary={snapshot.cost_summary}
              finalComposeReceipt={snapshot.final_compose_receipt}
            />

            <section className="grid gap-3 lg:grid-cols-2">
              <details className="group rounded-2xl border border-white/[0.07] bg-white/[0.018] p-4">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-medium">
                  <span className="flex items-center gap-2">
                    <Settings2 className="size-4 text-muted-foreground" />
                    本次制作设置
                  </span>
                  <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
                </summary>
                <div className="mt-4 grid gap-4 border-t border-white/[0.06] pt-4 sm:grid-cols-2">
                  <div className="space-y-1.5 text-xs text-muted-foreground sm:col-span-2">
                    <span>制作入口</span>
                    <div className="grid grid-cols-2 gap-1 rounded-lg border border-white/[0.08] bg-black/20 p-1" role="tablist" aria-label="制作入口模式">
                      {([
                        ["novel_adapt", "小说改编", "从已导入故事与项目资产继续"],
                        ["original", "个人原创", "从一句话创意或剧本开始"],
                      ] as const).map(([value, label, hint]) => (
                        <button
                          key={value}
                          type="button"
                          role="tab"
                          aria-selected={entryMode === value}
                          onClick={() => setEntryMode(value)}
                          disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                          className={cn(
                            "rounded-md px-2.5 py-2 text-left transition-colors",
                            entryMode === value
                              ? "bg-primary/20 text-white ring-1 ring-primary/35"
                              : "text-muted-foreground hover:bg-white/[0.05] hover:text-white",
                          )}
                        >
                          <span className="block text-xs font-medium">{label}</span>
                          <span className="mt-0.5 block text-[10px] leading-4 opacity-70">{hint}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                  {entryMode === "original" ? (
                    <>
                      <label className="space-y-1.5 text-xs text-muted-foreground sm:col-span-2">
                        <span>一句话创意</span>
                        <input
                          value={originalGoal}
                          onChange={(event) => setOriginalGoal(event.target.value)}
                          placeholder="例如：雨夜古刹里，失忆刀客追查自己的最后一场战斗"
                          disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                          className="h-9 w-full rounded-lg border border-white/[0.09] bg-black/20 px-3 text-sm text-white outline-none placeholder:text-white/25 focus:border-primary/50"
                        />
                      </label>
                      <label className="space-y-1.5 text-xs text-muted-foreground sm:col-span-2">
                        <span>原创剧本（可选）</span>
                        <textarea
                          value={originalScript}
                          onChange={(event) => setOriginalScript(event.target.value)}
                          rows={4}
                          placeholder="粘贴已有剧本，系统会在 ep000 原创入口中继续拆解"
                          disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                          className="w-full resize-y rounded-lg border border-white/[0.09] bg-black/20 px-3 py-2 text-sm leading-5 text-white outline-none placeholder:text-white/25 focus:border-primary/50"
                        />
                      </label>
                      {originalInputMissing ? (
                        <p className="text-[11px] text-orange-200 sm:col-span-2">原创入口需要填写一句话创意或原创剧本。</p>
                      ) : null}
                    </>
                  ) : null}
                  <label className="space-y-1.5 text-xs text-muted-foreground">
                    <span>目标集数</span>
                    <input
                      type="number"
                      min={1}
                      max={100}
                      value={targetEpisodes}
                      onChange={(event) => setTargetEpisodes(Number(event.target.value) || 1)}
                      disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                      className="h-9 w-full rounded-lg border border-white/[0.09] bg-black/20 px-3 text-sm text-white outline-none focus:border-primary/50"
                    />
                  </label>
                  <label className="space-y-1.5 text-xs text-muted-foreground">
                    <span>画面比例</span>
                    <select
                      value={aspectRatio}
                      onChange={(event) => setAspectRatio(event.target.value as ProductionAspectRatio | "")}
                      disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                      className="h-9 w-full rounded-lg border border-white/[0.09] bg-[#111214] px-3 text-sm text-white outline-none focus:border-primary/50"
                    >
                      <option value="">跟随项目设置</option>
                      <option value="16:9">16:9 横屏</option>
                      <option value="9:16">9:16 竖屏</option>
                      <option value="2:3">2:3 海报</option>
                      <option value="1:1">1:1 方形</option>
                    </select>
                  </label>
                  <label className="flex items-center justify-between gap-4 sm:col-span-2">
                    <span>
                      <strong className="block text-xs font-medium text-white">自动执行付费媒体步骤</strong>
                      <span className="mt-1 block text-[11px] text-muted-foreground">启动时仍会明确确认一次。</span>
                    </span>
                    <input
                      type="checkbox"
                      checked={autoPaidMedia}
                      onChange={(event) => setAutoPaidMedia(event.target.checked)}
                      disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                      className="size-4 accent-[hsl(var(--primary))]"
                    />
                  </label>
                </div>
              </details>

              <details className="group rounded-2xl border border-white/[0.07] bg-white/[0.018] p-4">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-medium">
                  <span className="flex items-center gap-2">
                    <Sparkles className="size-4 text-muted-foreground" />
                    模型方案与诊断
                    {blockingMissingRoles.length ? (
                      <Badge variant="outline" className="border-orange-400/25 text-orange-300">
                        缺 {blockingMissingRoles.length} 项
                      </Badge>
                    ) : null}
                  </span>
                  <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
                </summary>
                <div className="mt-4 border-t border-white/[0.06] pt-4">
                  <div className="mb-4 space-y-2">
                    {(() => {
                      const directModels = modelCatalog.data?.data.directModels as
                        | Record<string, DirectModelConfig[] | undefined>
                        | undefined;
                      const options = (directModels?.chat ?? []).filter(
                        (item) => item.enabled && item.runtimeReady,
                      );
                      return (
                        <label className="grid grid-cols-[5rem_minmax(0,1fr)] items-center gap-3 text-xs">
                          <span className="text-muted-foreground">统一 LLM</span>
                          <select
                            aria-label="统一 LLM 模型"
                            value={selectedModelBindings.llm ?? "__default__"}
                            onChange={(event) => setSelectedModelBindings((current) => ({
                              ...current,
                              llm: event.target.value,
                            }))}
                            disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                            className="h-8 min-w-0 rounded-md border border-white/[0.09] bg-[#111214] px-2 text-[11px] text-white outline-none focus:border-primary/50"
                          >
                            <option value="__default__">使用模型中心默认项</option>
                            {options.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.label} · {item.modelId}
                              </option>
                            ))}
                          </select>
                        </label>
                      );
                    })()}
                    <details className="rounded-md border border-white/[0.07] px-3 py-2">
                      <summary className="cursor-pointer text-[11px] text-muted-foreground">
                        按用途覆盖 LLM（可选）
                      </summary>
                      <div className="mt-2 space-y-2">
                        {SHARED_LLM_ROLES.map(([role, kind]) => {
                          const directModels = modelCatalog.data?.data.directModels as
                            | Record<string, DirectModelConfig[] | undefined>
                            | undefined;
                          const options = (directModels?.[kind] ?? []).filter(
                            (item) => item.enabled && item.runtimeReady,
                          );
                          return (
                            <label key={role} className="grid grid-cols-[5rem_minmax(0,1fr)] items-center gap-3 text-xs">
                              <span className="text-muted-foreground">{MODEL_ROLE_LABEL[role]}</span>
                              <select
                                aria-label={`${MODEL_ROLE_LABEL[role]} LLM 覆盖`}
                                value={selectedModelBindings[role] ?? "__shared__"}
                                onChange={(event) => setSelectedModelBindings((current) => ({
                                  ...current,
                                  [role]: event.target.value,
                                }))}
                                disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                                className="h-8 min-w-0 rounded-md border border-white/[0.09] bg-[#111214] px-2 text-[11px] text-white outline-none focus:border-primary/50"
                              >
                                <option value="__shared__">跟随统一 LLM</option>
                                <option value="__default__">使用该用途的模型中心默认项</option>
                                {options.map((item) => (
                                  <option key={item.id} value={item.id}>
                                    {item.label} · {item.modelId}
                                  </option>
                                ))}
                              </select>
                            </label>
                          );
                        })}
                      </div>
                    </details>
                    {MODEL_SELECTOR_ROLES.map(([role, kind]) => {
                      const directModels = modelCatalog.data?.data.directModels as
                        | Record<string, DirectModelConfig[] | undefined>
                        | undefined;
                      const options = kind === "video"
                        ? (modelCatalog.data?.data.directVideoModels ?? []).filter(
                            (item) => item.enabled && item.runtimeReady,
                          )
                        : (directModels?.[kind] ?? []).filter(
                            (item) => item.enabled && item.runtimeReady,
                          );
                      return (
                        <label key={role} className="grid grid-cols-[5rem_minmax(0,1fr)] items-center gap-3 text-xs">
                          <span className="text-muted-foreground">{MODEL_ROLE_LABEL[role]}</span>
                          <select
                            aria-label={`${MODEL_ROLE_LABEL[role]}模型`}
                            value={selectedModelBindings[role] ?? "__default__"}
                            onChange={(event) => setSelectedModelBindings((current) => ({
                              ...current,
                              [role]: event.target.value,
                            }))}
                            disabled={Boolean(run && ["running", "pausing", "paused", "blocked"].includes(run.status))}
                            className="h-8 min-w-0 rounded-md border border-white/[0.09] bg-[#111214] px-2 text-[11px] text-white outline-none focus:border-primary/50"
                          >
                            <option value="__default__">使用模型中心默认项</option>
                            {options.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.label} · {item.modelId}
                              </option>
                            ))}
                          </select>
                        </label>
                      );
                    })}
                    <p className="text-[10px] text-muted-foreground/70">
                      视频模型自带声音时无需单独配置声音模型；只有独立音频任务才使用声音模型。
                    </p>
                  </div>
                  {blockingMissingRoles.length ? (
                    <div className="mt-3 rounded-lg border border-orange-400/15 bg-orange-400/[0.04] p-3 text-[11px] text-orange-200">
                      请先配置：{blockingMissingRoles.map((role) => MODEL_ROLE_LABEL[role] || role).join("、")}
                    </div>
                  ) : null}
                </div>
              </details>
            </section>

            {readOnlyHistory.length ? (
              <details className="group rounded-2xl border border-white/[0.06] bg-white/[0.012] px-4 py-3">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-xs text-muted-foreground">
                  <span>历史运行 · {readOnlyHistory.length} 条（只读）</span>
                  <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" />
                </summary>
                <div className="mt-3 divide-y divide-white/[0.05] border-t border-white/[0.05]">
                  {readOnlyHistory.map((item) => (
                    <div key={item.id} className="flex items-center justify-between gap-4 py-3 text-xs">
                      <span className="min-w-0 truncate text-muted-foreground">
                        {new Date(item.created_at).toLocaleString("zh-CN")} · {item.current_action || "未开始"}
                      </span>
                      <span className={STATUS_TONE[item.status].split(" ").slice(-1)[0]}>
                        {STATUS_LABEL[item.status]}
                      </span>
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
          </>
        )}
      </div>
    </main>
  );
}
