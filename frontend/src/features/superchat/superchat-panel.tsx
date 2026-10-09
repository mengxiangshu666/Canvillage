// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  ArrowDown,
  ArrowUp,
  Brain,
  Braces,
  Check,
  File,
  Image,
  ListTree,
  Mic,
  MicOff,
  MoreHorizontal,
  PanelRightClose,
  PictureInPicture2,
  Plus,
  Search,
  ShieldAlert,
  Settings2,
  Star,
  X,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DragEvent as ReactDragEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "@tanstack/react-router";
import { attachBorderBeam, type BorderBeamController } from "border-beam-vanilla";
import { toast } from "sonner";
import { useRetainedComposer, claimHomeAutoSend, hasUnresolvedStoreSkills, type ComposerHandoffSnapshot } from "./use-retained-composer";
export type { ComposerHandoffSnapshot } from "./use-retained-composer";

import { Button } from "@/components/ui/button";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuthStore } from "@/stores/auth-store";
import { useCanvasStore, type CanvasNode } from "@/stores/canvasStore";
import { canvasOnlyProduct } from "@/lib/product-mode";
import { PRODUCT_NAME } from "@/lib/product-identity";
import { cn } from "@/lib/utils";
import { resolveMediaUrl } from "@/lib/media-url";
import {
  getSkillStoreCatalog,
  skillStoreAgentId,
  type SkillStoreCatalog,
  type SkillStoreItem,
} from "@/api/skill-store";
import {
  commandWorkflowRun,
  listWorkflowRuns,
  recordWorkflowRunEvent,
} from "@/api/workflow-runtime";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { useModelGatewayConfig } from "@/lib/queries/model-gateway";
import { useSuperChat } from "@/features/superchat/use-superchat";
import {
  DEFAULT_MESSAGE_RENDER_LIMIT,
  MESSAGE_RENDER_BATCH_SIZE,
  deriveMessageRenderWindow,
  nextMessageRenderLimit,
} from "@/features/superchat/message-render-window";
import {
  resolveLiveSelectedNodeId,
} from "@/features/superchat/canvas-fast-command";
import { routeCanvasAgentExecution } from "@/features/superchat/canvas-agent-execution-router";
import { estimateAgentContextUsage } from "@/features/superchat/agent-context-usage";
import { buildAgentExecutionTimeline } from "@/features/superchat/agent-execution-timeline";
import { AgentContextRing } from "@/features/superchat/AgentContextRing";
import {
  executeXiaoshuTaskControl,
  routeXiaoshuRequest,
} from "@/features/superchat/xiaoshu-agent-runtime";
import {
  agentComposerCanSteer,
  agentComposerPrimaryAction,
  agentComposerPrimaryActionDisabled,
} from "@/features/superchat/agent-composer-action";
import { buildChatTaskLabel } from "@/features/superchat/task-notification-label";
import {
  buildCanvasAgentRequest,
  CANVAS_AGENT_SKILLS,
  loadCanvasAgentSkillIds,
  saveCanvasAgentSkillIds,
  type CanvasAgentSkill,
} from "@/features/superchat/canvas-agent-skills";
import {
  loadAigcResearchEnabled,
  saveAigcResearchEnabled,
} from "@/features/superchat/agent-research-toggle";
import {
  buildCanvasNodeIdentity,
  buildCanvasAgentModelCatalog,
} from "@/features/superchat/canvas-agent-director-state";
import { currentCanvasAgentContext } from "@/features/superchat/canvas-agent-context";
import { inferVideoReferenceRole } from "@/features/canvas/domain/videoReferenceRoles";
import {
  applyPendingWorkflowCanvasCommand,
  canvasWorkflowFailureCheckpoint,
  canvasWorkflowMediaBatchCheckpoint,
  canvasWorkflowRuntimeContextFromRun,
  pendingWorkflowCanvasCommand,
} from "@/features/superchat/canvas-workflow-fast-start";
import {
  activeWorkflowRunFromList,
  mergeWorkflowRun,
  subscribeWorkflowRunLive,
  WORKFLOW_RUN_EVENT,
} from "@/features/superchat/workflow-run-live";
import type { CanvasWorkflowRuntimeContext, WorkflowRun } from "@/types/workflow-runtime";
import {
  CANVAS_AGENT_RUN_MODE_OPTIONS,
  canvasAgentRunModeLabel,
  DEFAULT_CANVAS_AGENT_RUN_MODE,
  loadCanvasAgentRunMode,
  saveCanvasAgentRunMode,
  type CanvasAgentRunMode,
} from "@/features/superchat/canvas-agent-run-mode";
import {
  AgentMark,
  deriveAgentPlanFromMessages,
  FREEZONE_AGENT_DRAWER_CLASS,
  FreezoneAgentContextCard,
  FreezoneAgentCompactStatus,
  FreezoneAgentRunBar,
  FreezoneComposerTags,
  FreezoneComposerSkillDrawer,
  FreezoneDirectorConsole,
  LIBTV_CHAT_RICH_INPUT_PLACEHOLDER,
  LIBTV_EXECUTING,
  LIBTV_HEADER_SUBTITLE,
  FreezoneSlashSkillMenu,
  FreezoneWelcomeSkills,
  type WorkflowItemSelectionGroup,
} from "@/features/superchat/freezone-canvas-agent-ui";
import {
  loadDismissedWorkflowFailureKeys,
  saveDismissedWorkflowFailureKey,
  workflowRunHasActionableFailures,
  workflowFailureDisplayKey,
} from "@/features/superchat/workflow-failure-dismissal";
import {
  workflowReleaseReadinessFromRun,
  workflowReleaseRequiresNotice,
} from "@/features/superchat/workflow-release-readiness";
import { useWorkflowFailureRetry } from "@/features/superchat/useWorkflowFailureRetry";
import { useWorkflowCanvasRecoveryActions } from "@/features/superchat/use-workflow-canvas-recovery-actions";
import type { CanvasAgentNodeRef } from "@/features/superchat/canvas-agent-skills";
import { SkillStoreDialog } from "@/features/superchat/SkillStoreDialog";
import { AgentMemoryDialog } from "@/features/superchat/AgentMemoryDialog";
import { AgentCliSkillDialog } from "@/features/superchat/AgentCliSkillDialog";
import { ConversationHistoryMenu } from "@/features/superchat/ConversationHistoryMenu";
import {
  buildCanvasReferenceAttachments,
  fileToImageAttachment,
  hasExplicitUserImageAttachment,
  mergeReferenceAttachments,
} from "@/features/superchat/canvas-agent-references";
import {
  CANVAS_AGENT_PIN_EVENT,
  loadPinnedCanvasNodeIds,
  setPinnedCanvasNodesForAgent,
} from "@/features/superchat/canvas-agent-pin-store";
import { deriveDirectorConsoleState } from "@/features/superchat/director-console-model";
import {
  clearLastStructureApply,
  dismissStructureProposal,
  getLastStructureApply,
  getPendingStructureProposals,
  requestStructureApply,
  STRUCTURE_PROPOSAL_EVENT,
  type StructureApplyRecord,
  type StructureProposal,
} from "@/features/superchat/structure-proposal-store";
import { ComposerWaitingStatus } from "@/features/superchat/composer-waiting-status";
import { useEventBus } from "@/task-center/event-bus-context";
import type { AgentEngine, ChatMessage } from "@/features/superchat/types";
import { DotsIndicator } from "./superchat-message-rendering";
import type { ApprovalRequest, ChatAttachment } from "@/features/superchat/types";
import { FormatCheckDetailsDialog } from "@/components/ingest/FormatCheckDetailsDialog";
import type { FormatCheck } from "@/lib/queries/ingest";
import { isToolMessage, MessageBubble, ChatTimeline, ControlBar, VillageAgentSelect, HeaderControlPortal, SearchBar, PinnedPanel, MessageDetailPanel, SpecMediaDetailModal } from "@/features/superchat/superchat-panel-presentation";
import type { SpecMediaDetail } from "@/features/superchat/superchat-panel-presentation";
import { VIDEO_CREATION_RE, isAllowedScriptUpload, isAllowedScriptDragItem, isOverwriteChoice, isFinalOverwriteConfirmation, shouldReportUploadedFiles, isNovelAttachment, loadUploadedIngestFiles, saveUploadedIngestFiles, mergeUploadedIngestFiles, uploadedFileFromPrepared, buildUploadedFilesContext, buildReingestConfirmationContext, buildReingestCancelledContext, uploadAttachmentsForIngest, startNovelIngest, projectHasIngestedContent, buildAttachmentAnalysisContext, surfaceFormatCheckWarnings, appendIngestAutomationContext, appendAttachmentAnalysisContext } from "@/features/superchat/superchat-ingest-context";
import "./village-agent-libtv-v4.css";

import type { PreparedIngestAttachment, UploadedIngestFile, ReingestConfirmation } from "@/features/superchat/superchat-ingest-context";
export { MessageBubble, shouldRenderAttachmentChip } from "@/features/superchat/superchat-panel-presentation";

type QueuedSendItem = {
  id: string;
  text: string;
  displayText: string;
  attachments: ChatAttachment[];
  engine: AgentEngine;
  createdAt: number;
};

const ENABLE_SUPERCHAT_FILE_UPLOAD = false;

function ApprovalCard({
  approval,
  onResolve,
}: {
  approval: ApprovalRequest;
  onResolve: (decision: "allow-once" | "allow-always" | "deny") => void;
}) {
  const { t } = useTranslation();
  const remaining = approval.expiresAtMs
    ? Math.max(0, Math.ceil((approval.expiresAtMs - Date.now()) / 1000))
    : null;

  return (
    <div className="border-b border-amber-500/20 bg-amber-500/8 px-3 py-3">
      <div className="mb-2 flex items-start gap-2">
        <ShieldAlert className="mt-0.5 size-4 shrink-0 text-amber-500" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-foreground">{approval.title}</div>
          {remaining !== null && (
            <div className="text-xs text-muted-foreground">
              {t("aiAssistant.approvalExpires", { seconds: remaining })}
            </div>
          )}
        </div>
        <Badge variant="outline" className="rounded-md uppercase">
          {approval.kind}
        </Badge>
      </div>
      {approval.description && (
        <p className="mb-2 text-xs leading-5 text-muted-foreground">{approval.description}</p>
      )}
      {approval.command && (
        <pre className="max-h-32 overflow-auto rounded-md border border-border/70 bg-background/60 px-2 py-1.5 text-xs whitespace-pre-wrap break-all">
          {approval.command}
        </pre>
      )}
      <div className="mt-2 grid gap-1 text-xs text-muted-foreground">
        {approval.cwd && <div className="truncate">CWD: {approval.cwd}</div>}
        {approval.host && <div className="truncate">Host: {approval.host}</div>}
        {approval.security && <div className="truncate">Security: {approval.security}</div>}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="xs" onClick={() => onResolve("allow-once")}>
          {t("aiAssistant.allowOnce")}
        </Button>
        <Button size="xs" variant="outline" onClick={() => onResolve("allow-always")}>
          {t("aiAssistant.allowAlways")}
        </Button>
        <Button size="xs" variant="destructive" onClick={() => onResolve("deny")}>
          {t("aiAssistant.deny")}
        </Button>
      </div>
    </div>
  );
}

type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string; isFinal?: boolean }>> }) => void) | null;
  onend: (() => void) | null;
};

function createSpeechRecognition(): SpeechRecognitionLike | null {
  const candidate = (window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  });
  const Ctor = candidate.SpeechRecognition ?? candidate.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

type SuperChatPanelVariant = "default" | "freezone";

interface SuperChatPanelProps {
  variant?: SuperChatPanelVariant;
  onRequestClose?: () => void;
  onRequestPresentationToggle?: () => void;
  presentation?: "docked" | "floating";
  canvasId?: string;
  canvasRevision?: number | null;
  projectStyleId?: string | null;
  /**
   * Seeds the composer once, on mount. Used by the project centre so an idea
   * typed on the home hero arrives as an editable message instead of being
   * sent automatically on the user's behalf.
   */
  initialDraft?: string;
  initialAttachments?: ChatAttachment[];
  initialSkillIds?: string[];
  autoSendInitialDraft?: boolean;
  composerHandoffRef?: React.MutableRefObject<ComposerHandoffSnapshot>;
}


function skillStoreItemToAgentSkill(item: SkillStoreItem): CanvasAgentSkill {
  return {
    id: skillStoreAgentId(item.id),
    skillKey: item.skill_key,
    label: item.name,
    description: item.description,
    activation: item.activation,
    executionContract: item.contract,
    coverImage: item.cover_image ?? undefined,
    triggers: item.tags,
    accent: item.source === "custom"
      ? "from-cyan-500/20 to-sky-500/5 border-cyan-300/20"
      : "from-violet-500/20 to-fuchsia-500/5 border-violet-300/20",
  };
}

export function SuperChatPanel({
  variant = "default",
  onRequestClose,
  onRequestPresentationToggle,
  presentation = "docked",
  canvasId,
  canvasRevision,
  projectStyleId,
  initialDraft,
  initialAttachments,
  initialSkillIds,
  autoSendInitialDraft,
  composerHandoffRef,
}: SuperChatPanelProps = {}) {
  const { t } = useTranslation();
  const params = useParams({ strict: false }) as { project?: string };
  const username = useAuthStore((s) => s.username);
  const isFreezoneLayout = variant === "freezone";
  const isVillageFloating =
    isFreezoneLayout && canvasOnlyProduct && presentation === "floating";
  const agentBrandName = "小树";
  const freezoneProjectId = params.project ?? "";
  const freezoneCanvasId = canvasId ?? "";
  const { draft, setDraft, attachments, setAttachments } = useRetainedComposer({
    draft: initialDraft ?? "", attachments: initialAttachments ?? [], autoSendClaimed: false,
  }, composerHandoffRef);
  const [researchEnabled, setResearchEnabled] = useState(() =>
    isFreezoneLayout
      ? loadAigcResearchEnabled(freezoneProjectId, freezoneCanvasId)
      : false,
  );
  useEffect(() => {
    setResearchEnabled(
      isFreezoneLayout
        ? loadAigcResearchEnabled(freezoneProjectId, freezoneCanvasId)
        : false,
    );
  }, [freezoneCanvasId, freezoneProjectId, isFreezoneLayout]);
  // Empty means automatic skill routing; this state only stores manual overrides.
  const [selectedCanvasSkillIds, setSelectedCanvasSkillIds] = useState<string[]>(() =>
    composerHandoffRef?.current.skillIds ?? initialSkillIds ?? (variant === "freezone"
      ? loadCanvasAgentSkillIds(freezoneProjectId, freezoneCanvasId)
      : []),
  );
  useLayoutEffect(() => {
    if (composerHandoffRef) composerHandoffRef.current.skillIds = selectedCanvasSkillIds;
  }, [composerHandoffRef, selectedCanvasSkillIds]);
  const [canvasAgentRunMode, setCanvasAgentRunMode] = useState<CanvasAgentRunMode>(() =>
    canvasOnlyProduct && variant === "freezone" ? loadCanvasAgentRunMode() : DEFAULT_CANVAS_AGENT_RUN_MODE,
  );
  const [pinnedCanvasNodeIds, setPinnedCanvasNodeIds] = useState<string[]>([]);
  // Chat-first: director workbench stays compact; execution receipts open it only when useful.
  const [directorConsoleCollapsed, setDirectorConsoleCollapsed] = useState(true);
  const [structureProposals, setStructureProposals] = useState<StructureProposal[]>([]);
  const [lastStructureApply, setLastStructureApply] = useState<StructureApplyRecord | null>(null);
  const [slashMenuOpen, setSlashMenuOpen] = useState(false);
  const [skillDrawerOpen, setSkillDrawerOpen] = useState(false);
  const [skillStoreOpen, setSkillStoreOpen] = useState(false);
  const [agentMemoryOpen, setAgentMemoryOpen] = useState(false);
  const [agentCliSkillOpen, setAgentCliSkillOpen] = useState(false);
  const [agentSettingsOpen, setAgentSettingsOpen] = useState(false);
  const [skillStoreCatalog, setSkillStoreCatalog] = useState<SkillStoreCatalog | null>(null);
  const [skillCatalogFailed, setSkillCatalogFailed] = useState(false);
  const installedStoreAgentSkills = useMemo(
    () => (skillStoreCatalog?.items ?? []).filter((item) => item.installed).map(skillStoreItemToAgentSkill),
    [skillStoreCatalog?.items],
  );
  const availableCanvasAgentSkills = useMemo(
    () => [...CANVAS_AGENT_SKILLS, ...installedStoreAgentSkills],
    [installedStoreAgentSkills],
  );
  const unresolvedStoreSkills = hasUnresolvedStoreSkills(selectedCanvasSkillIds, availableCanvasAgentSkills);
  const skillReadinessMessage = unresolvedStoreSkills
    ? skillCatalogFailed ? "已选技能加载失败，请打开技能库重试或移除该技能"
      : skillStoreCatalog ? "已选技能不可用，请重新安装或移除该技能"
        : "正在加载已选技能，请稍候"
    : null;
  const selectedNodeId = useCanvasStore((state) => state.selectedNodeId);
  const canvasNodes = useCanvasStore((state) => state.nodes);
  const toAgentNodeRef = useCallback((
    nodeId: string,
    sourceNodes: readonly CanvasNode[] = canvasNodes,
  ): CanvasAgentNodeRef | null => {
    const node = sourceNodes.find((item) => item.id === nodeId);
    if (!node) return null;
    const data = node.data as Record<string, unknown> | undefined;
    const resolveFirstMediaUrl = (...candidates: unknown[]): string | undefined => {
      for (const candidate of candidates) {
        if (typeof candidate !== "string" || !candidate.trim()) continue;
        const resolved = resolveMediaUrl(candidate.trim());
        if (resolved) return resolved;
      }
      return undefined;
    };
    // Prefer a node's poster or image result; only fall back to a video first frame.
    const imagePreviewUrl = resolveFirstMediaUrl(
      data?.imageUrl,
      data?.committed_slot_url,
      data?.previewImageUrl,
      data?.thumbnailUrl,
      data?.coverUrl,
    );
    const videoPreviewUrl = resolveFirstMediaUrl(data?.videoUrl, data?.sourceVideoUrl, data?.resultVideoUrl);
    const audioPreviewUrl = resolveFirstMediaUrl(data?.audioUrl, data?.audio_url, data?.resultAudioUrl, data?.audioOutputUrl);
    const nodeType = String(node.type ?? "node");
    const identity = buildCanvasNodeIdentity(node, freezoneCanvasId || "default");
    const textLikeNode = new Set(["textAnnotationNode", "scriptNode", "beatContextNode", "storyboardGenNode"]).has(nodeType);
    const snapshot = data?.snapshot && typeof data.snapshot === "object"
      ? data.snapshot as Record<string, unknown>
      : null;
    const textPreviewContent = [
      data?.content,
      data?.scriptTitle,
      data?.prompt,
      snapshot?.visualDescription,
      snapshot?.narrationSegment,
    ].some((value) => typeof value === "string" && value.trim().length > 0)
      || (Array.isArray(data?.scriptResult) && data.scriptResult.length > 0)
      || Boolean(data?.scriptResult && typeof data.scriptResult === "object");
    return {
      id: node.id,
      type: nodeType,
      label: String(data?.displayName ?? data?.label ?? node.type ?? node.id),
      assetId: typeof data?.assetId === "string"
        ? data.assetId
        : typeof data?.asset_id === "string"
          ? data.asset_id
          : null,
      assetUri: identity.asset_uri,
      nodeUri: identity.node_uri,
      parentId: identity.parent_id,
      role: inferVideoReferenceRole(data),
      hasPrompt: typeof data?.prompt === "string" && Boolean(String(data.prompt).trim()),
      hasImage: Boolean(data?.imageUrl ?? data?.previewImageUrl),
      hasVideo: Boolean(videoPreviewUrl),
      ...(videoPreviewUrl
        ? {
          previewUrl: videoPreviewUrl,
          previewKind: "video" as const,
          ...(imagePreviewUrl ? { previewPosterUrl: imagePreviewUrl } : {}),
        }
        : imagePreviewUrl
          ? { previewUrl: imagePreviewUrl, previewKind: "image" as const }
          : audioPreviewUrl
            ? { previewUrl: audioPreviewUrl, previewKind: "audio" as const }
            : textLikeNode && textPreviewContent
              ? { previewKind: "text" as const }
              : {}),
    };
  }, [canvasNodes, freezoneCanvasId]);
  const selectedCanvasNode = useMemo(() => {
    if (!isFreezoneLayout || !selectedNodeId) return null;
    return toAgentNodeRef(selectedNodeId);
  }, [isFreezoneLayout, selectedNodeId, toAgentNodeRef]);
  const pinnedCanvasNodes = useMemo(
    () => pinnedCanvasNodeIds.map((id) => toAgentNodeRef(id)).filter((node): node is CanvasAgentNodeRef => Boolean(node)),
    [pinnedCanvasNodeIds, toAgentNodeRef],
  );

  // Load + sync pinned nodes from shared store (Canvas right-click 「加入 Agent」).
  useEffect(() => {
    if (!isFreezoneLayout || !freezoneProjectId || !freezoneCanvasId) return;
    setPinnedCanvasNodeIds(loadPinnedCanvasNodeIds(freezoneProjectId, freezoneCanvasId));
    const onPins = (event: Event) => {
      const detail = (event as CustomEvent<{ projectId?: string; canvasId?: string; ids?: string[] }>).detail;
      if (!detail) return;
      if (detail.projectId && detail.projectId !== freezoneProjectId) return;
      if (detail.canvasId && detail.canvasId !== freezoneCanvasId) return;
      if (Array.isArray(detail.ids)) {
        setPinnedCanvasNodeIds(detail.ids);
      } else {
        setPinnedCanvasNodeIds(loadPinnedCanvasNodeIds(freezoneProjectId, freezoneCanvasId));
      }
    };
    window.addEventListener(CANVAS_AGENT_PIN_EVENT, onPins);
    return () => window.removeEventListener(CANVAS_AGENT_PIN_EVENT, onPins);
  }, [freezoneCanvasId, freezoneProjectId, isFreezoneLayout]);

  useEffect(() => {
    if (!isFreezoneLayout) return;
    let cancelled = false;
    getSkillStoreCatalog()
      .then((next) => {
        if (!cancelled) setSkillStoreCatalog(next);
      })
      .catch(() => {
        if (!cancelled) setSkillCatalogFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [isFreezoneLayout]);

  // Fallback queue for future structure operations not yet understood by the executor.
  useEffect(() => {
    if (!isFreezoneLayout) return;
    const scope = { projectId: freezoneProjectId, canvasId: freezoneCanvasId };
    const sync = () => {
      const nextProposals = getPendingStructureProposals(scope);
      setStructureProposals(nextProposals);
      setLastStructureApply(getLastStructureApply(scope));
      // LibTV-like calm drawer: compact by default, but never hide a decision.
      if (nextProposals.length > 0) setDirectorConsoleCollapsed(false);
    };
    sync();
    window.addEventListener(STRUCTURE_PROPOSAL_EVENT, sync);
    return () => window.removeEventListener(STRUCTURE_PROPOSAL_EVENT, sync);
  }, [freezoneCanvasId, freezoneProjectId, isFreezoneLayout]);

  const updatePinnedCanvasNodeIds = useCallback(
    (updater: (current: string[]) => string[]) => {
      setPinnedCanvasNodeIds((current) => {
        const next = updater(current);
        if (isFreezoneLayout && freezoneProjectId && freezoneCanvasId) {
          setPinnedCanvasNodesForAgent({
            projectId: freezoneProjectId,
            canvasId: freezoneCanvasId,
            ids: next,
          });
        }
        return next;
      });
    },
    [freezoneCanvasId, freezoneProjectId, isFreezoneLayout],
  );

  const slashQuery = useMemo(() => {
    if (!isFreezoneLayout || !slashMenuOpen) return "";
    const match = draft.match(/(?:^|\s)\/([^\s]*)$/);
    return match?.[1] ?? "";
  }, [draft, isFreezoneLayout, slashMenuOpen]);
  const [search, setSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [detailMessage, setDetailMessage] = useState<ChatMessage | null>(null);
  const [mediaDetail, setMediaDetail] = useState<SpecMediaDetail | null>(null);
  const [uploadedIngestFiles, setUploadedIngestFiles] = useState<UploadedIngestFile[]>(() =>
    loadUploadedIngestFiles(params.project?.trim()),
  );
  const [reingestConfirmation, setReingestConfirmation] =
    useState<ReingestConfirmation | null>(null);
  const [formatCheckDetails, setFormatCheckDetails] = useState<{
    formatCheck: FormatCheck;
    filename: string;
  } | null>(null);
  const [queuedMessages, setQueuedMessages] = useState<QueuedSendItem[]>([]);
  const [selectedQueuedMessageId, setSelectedQueuedMessageId] = useState<string | null>(null);
  const [selectedHistoryMessageIndex, setSelectedHistoryMessageIndex] = useState<number | null>(null);
  const [preparingSend, setPreparingSend] = useState(false);
  const [activeWorkflowRun, setActiveWorkflowRun] = useState<WorkflowRun | null>(null);
  const [dismissedWorkflowFailureKeys, setDismissedWorkflowFailureKeys] = useState<Set<string>>(
    () => loadDismissedWorkflowFailureKeys(),
  );
  const [dismissingWorkflowItems, setDismissingWorkflowItems] = useState(false);
  useEffect(() => {
    const onWorkflowRun = (event: Event) => {
      const run = (event as CustomEvent<WorkflowRun>).detail;
      const projectId = (freezoneProjectId || params.project || "").trim();
      if (
        !run
        || run.project_id !== projectId
        || run.canvas_id !== freezoneCanvasId
      ) return;
      setActiveWorkflowRun((current) => mergeWorkflowRun(current, run));
    };
    window.addEventListener(WORKFLOW_RUN_EVENT, onWorkflowRun);
    return () => window.removeEventListener(WORKFLOW_RUN_EVENT, onWorkflowRun);
  }, [freezoneCanvasId, freezoneProjectId, params.project]);
  const [composerInputFocused, setComposerInputFocused] = useState(false);
  const [recording, setRecording] = useState(false);
  const [dragFileState, setDragFileState] = useState<"valid" | "invalid" | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [messageRenderLimit, setMessageRenderLimit] = useState(DEFAULT_MESSAGE_RENDER_LIMIT);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const draftInputRef = useRef<HTMLTextAreaElement | null>(null);
  const restoreDraftFocusRef = useRef(false);
  const dragDepthRef = useRef(0);
  const speechRef = useRef<SpeechRecognitionLike | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const messageHistoryAnchorRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const historyScrollKeyRef = useRef<string | null>(null);
  const composerShellRef = useRef<HTMLDivElement | null>(null);
  const composerBeamRef = useRef<BorderBeamController | null>(null);
  const notifiedTaskKeysRef = useRef<Set<string>>(new Set());
  const recordedWorkflowFailuresRef = useRef<Set<string>>(new Set());
  const recordedWorkflowMediaEventsRef = useRef<Set<string>>(new Set());
  const appliedWorkflowCanvasCommandsRef = useRef<Set<string>>(new Set());
  const taskEventBus = useEventBus();
  const modelGateway = useModelGatewayConfig(isFreezoneLayout && canvasOnlyProduct);
  const agentModelCatalog = useMemo(
    () => buildCanvasAgentModelCatalog(modelGateway.data?.data),
    [modelGateway.data?.data],
  );
  const chat = useSuperChat({
    project: params.project,
    canvasId: isFreezoneLayout ? freezoneCanvasId : canvasId,
    displayName: username || PRODUCT_NAME,
    researchEnabled,
  });
  const {
    retryingItems: retryingWorkflowItems,
    retryingStepId: retryingWorkflowStepId,
    retryItems: retryWorkflowItems,
    retryStep: retryWorkflowStep,
  } = useWorkflowFailureRetry({
    projectId: (freezoneProjectId || params.project || "").trim(),
    run: activeWorkflowRun,
    setRun: setActiveWorkflowRun,
  });
  const workflowRecovery = useWorkflowCanvasRecoveryActions({
    projectId: (freezoneProjectId || params.project || "").trim(),
    run: activeWorkflowRun,
    setRun: setActiveWorkflowRun,
    canvasContext: currentCanvasAgentContext(
      canvasId,
      (freezoneProjectId || params.project || "").trim(),
      canvasRevision,
      projectStyleId,
      agentModelCatalog,
    ),
    skillIds: selectedCanvasSkillIds,
    pinnedNodes: pinnedCanvasNodes,
    runMode: canvasAgentRunMode,
    additionalSkills: installedStoreAgentSkills,
    connected: chat.connected,
    busy: chat.busy,
    send: chat.send,
  });
  const dismissWorkflowItems = useCallback(async (groups: WorkflowItemSelectionGroup[]) => {
    const projectId = (freezoneProjectId || params.project || "").trim();
    const run = activeWorkflowRun;
    if (!projectId || !run || groups.length === 0 || dismissingWorkflowItems) return;
    let currentRun = run;
    let dismissedCount = 0;
    setDismissingWorkflowItems(true);
    try {
      for (const group of groups) {
        const itemIds = [...new Set(group.itemIds.map((itemId) => itemId.trim()).filter(Boolean))];
        if (!group.stepId || itemIds.length === 0) continue;
        const updated = await commandWorkflowRun(projectId, currentRun.id, {
          command: "dismiss_failed_items",
          step_id: group.stepId,
          item_ids: itemIds,
          idempotency_key: `ui-item-dismiss:${currentRun.id}:${currentRun.revision}:${group.stepId}:${itemIds.join(",")}`,
          expected_revision: currentRun.revision,
        });
        currentRun = mergeWorkflowRun(currentRun, updated) ?? updated;
        dismissedCount += itemIds.length;
        setActiveWorkflowRun((current) => mergeWorkflowRun(current, updated));
      }
      if (dismissedCount > 0) toast.success(`已删除 ${dismissedCount} 条失败记录`);
    } catch (error) {
      const prefix = dismissedCount > 0 ? `已删除 ${dismissedCount} 条；` : "";
      toast.error(`${prefix}其余失败记录未删除：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setDismissingWorkflowItems(false);
    }
  }, [activeWorkflowRun, dismissingWorkflowItems, freezoneProjectId, params.project]);
  const toggleResearch = useCallback(() => {
    if (chat.busy) return;
    setResearchEnabled((current) => {
      const next = !current;
      saveAigcResearchEnabled(freezoneProjectId, freezoneCanvasId, next);
      return next;
    });
  }, [chat.busy, freezoneCanvasId, freezoneProjectId]);
  useEffect(() => {
    const projectId = (freezoneProjectId || params.project || "").trim();
    if (!isFreezoneLayout || !projectId || !freezoneCanvasId) {
      setActiveWorkflowRun(null);
      return;
    }
    setActiveWorkflowRun((current) => (
      current?.project_id === projectId && current.canvas_id === freezoneCanvasId
        ? current
        : null
    ));
    const controller = new AbortController();
    void listWorkflowRuns(projectId, freezoneCanvasId, controller.signal)
      .then((runs) => {
        const candidate = activeWorkflowRunFromList(runs);
        setActiveWorkflowRun((current) => mergeWorkflowRun(current, candidate));
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [freezoneCanvasId, freezoneProjectId, isFreezoneLayout, params.project]);

  useEffect(() => {
    const projectId = (freezoneProjectId || params.project || "").trim();
    const runId = activeWorkflowRun?.id;
    if (!projectId || !runId || !["running", "paused", "failed"].includes(activeWorkflowRun.status)) {
      return;
    }
    const subscription = subscribeWorkflowRunLive({
      projectId,
      canvasId: freezoneCanvasId,
      runId,
      onRun: (run) => {
        setActiveWorkflowRun((current) => mergeWorkflowRun(current, run));
        if (["completed", "failed", "cancelled"].includes(run.status)) {
          window.setTimeout(() => chat.requestHistory(), 0);
        }
      },
    });
    return subscription.close;
  }, [
    activeWorkflowRun?.id,
    activeWorkflowRun?.status,
    chat.requestHistory,
    freezoneCanvasId,
    freezoneProjectId,
    params.project,
  ]);
  useEffect(() => {
    const run = activeWorkflowRun;
    const projectId = (freezoneProjectId || params.project || "").trim();
    if ((run?.contract_version ?? 1) >= 2) return;
    const pending = pendingWorkflowCanvasCommand(run);
    if (!projectId || !freezoneCanvasId || !run || !pending) return;
    const commandId = pending.envelope.command_id;
    if (appliedWorkflowCanvasCommandsRef.current.has(commandId)) return;
    appliedWorkflowCanvasCommandsRef.current.add(commandId);
    void applyPendingWorkflowCanvasCommand({
      projectId,
      canvasId: freezoneCanvasId,
      run,
      pending,
    }).then((run) => {
      setActiveWorkflowRun((current) => mergeWorkflowRun(current, run));
    }).catch((error) => {
      appliedWorkflowCanvasCommandsRef.current.delete(commandId);
      const message = error instanceof Error ? error.message : String(error);
      toast.error(`工作流画布步骤执行失败：${message}`);
    });
  }, [activeWorkflowRun, freezoneCanvasId, freezoneProjectId, params.project]);
  useEffect(() => {
    const run = activeWorkflowRun;
    const projectId = (freezoneProjectId || params.project || "").trim();
    if (!projectId || !run) return;
    const checkpoint = canvasWorkflowMediaBatchCheckpoint(run, canvasNodes);
    if (!checkpoint || recordedWorkflowMediaEventsRef.current.has(checkpoint.eventId)) return;
    recordedWorkflowMediaEventsRef.current.add(checkpoint.eventId);
    void recordWorkflowRunEvent(projectId, run.id, {
      event_id: checkpoint.eventId,
      type: checkpoint.eventType,
      step_id: checkpoint.stepId,
      success: checkpoint.success,
      error: checkpoint.error ?? "",
      payload: checkpoint.payload,
      expected_revision: run.revision,
    }).then((run) => {
      setActiveWorkflowRun((current) => mergeWorkflowRun(current, run));
    }).catch(() => {
      recordedWorkflowMediaEventsRef.current.delete(checkpoint.eventId);
    });
  }, [activeWorkflowRun, canvasNodes, freezoneProjectId, params.project]);
  useEffect(() => {
    const run = activeWorkflowRun;
    const projectId = (freezoneProjectId || params.project || "").trim();
    if (!projectId || !run) return;
    const checkpoint = canvasWorkflowFailureCheckpoint(run, chat.messages, chat.busy);
    if (!checkpoint || recordedWorkflowFailuresRef.current.has(checkpoint.eventId)) return;
    recordedWorkflowFailuresRef.current.add(checkpoint.eventId);
    void recordWorkflowRunEvent(projectId, run.id, {
      event_id: checkpoint.eventId,
      type: "step_failed",
      step_id: checkpoint.stepId,
      success: false,
      error: checkpoint.error,
      payload: {
        source: "agent_turn",
        turn_id: checkpoint.turnId,
      },
      expected_revision: run.revision,
    }).then((run) => {
      setActiveWorkflowRun((current) => mergeWorkflowRun(current, run));
    }).catch(() => {
      recordedWorkflowFailuresRef.current.delete(checkpoint.eventId);
    });
  }, [
    activeWorkflowRun,
    chat.busy,
    chat.messages,
    freezoneProjectId,
    params.project,
  ]);
  const freezonePlanSteps = useMemo(
    () => (isFreezoneLayout
      ? deriveAgentPlanFromMessages(chat.messages, chat.busy, chat.progress?.workflow)
      : []),
    [isFreezoneLayout, chat.messages, chat.busy, chat.progress?.workflow],
  );
  const activeWorkflowFailureKey = workflowFailureDisplayKey(activeWorkflowRun);
  const activeWorkflowHasActionableFailures = workflowRunHasActionableFailures(activeWorkflowRun);
  const activeWorkflowFailureDismissed = Boolean(
    activeWorkflowFailureKey && dismissedWorkflowFailureKeys.has(activeWorkflowFailureKey),
  );
  const suppressHistoricalWorkflowFailure = Boolean(
    activeWorkflowRun
    && (
      activeWorkflowFailureDismissed
      || (activeWorkflowRun.status === "failed" && !activeWorkflowHasActionableFailures)
    ),
  );
  const compactWorkflowRun = suppressHistoricalWorkflowFailure ? null : activeWorkflowRun;
  const compactExecutionBusy = chat.busy || compactWorkflowRun?.status === "running";
  const activeWorkflowStepId = compactWorkflowRun?.current_frontier[0];
  const activeWorkflowStepLabel = activeWorkflowStepId
    ? compactWorkflowRun?.step_states[activeWorkflowStepId]?.label
    : null;
  const compactExecutionLabel = chat.progress?.message?.trim()
    || activeWorkflowStepLabel
    || freezonePlanSteps.find((step) => step.status === "running")?.label
    || "正在执行画布任务";
  const workflowStepStates = compactWorkflowRun?.status === "running"
    ? Object.values(compactWorkflowRun.step_states)
    : [];
  const compactCompletedCount = workflowStepStates.length > 0
    ? workflowStepStates.filter((step) => step.status === "completed" || step.status === "skipped").length
    : freezonePlanSteps.filter((step) => step.status === "done" || step.status === "skipped").length;
  const compactTotalCount = workflowStepStates.length || freezonePlanSteps.length;
  const compactExecutionProgressLabel = compactTotalCount > 0
    ? `步骤 ${compactCompletedCount}/${compactTotalCount}`
    : null;
  const releaseReadinessRequired = workflowReleaseRequiresNotice(
    workflowReleaseReadinessFromRun(compactWorkflowRun),
  );
  const showCompactExecutionStatus = isFreezoneLayout
    && (compactExecutionBusy
      || Boolean(chat.recovery)
      || compactWorkflowRun?.status === "failed"
      || compactWorkflowRun?.status === "paused"
      || releaseReadinessRequired);
  const agentExecutionTimeline = useMemo(
    () => buildAgentExecutionTimeline({
      events: chat.villageAgentEvents,
      activeTurnId: chat.activeTurnId,
      busy: compactExecutionBusy,
      progress: chat.progress,
      workflowRun: compactWorkflowRun,
      planSteps: freezonePlanSteps,
    }),
    [
      compactWorkflowRun,
      chat.activeTurnId,
      chat.progress,
      chat.villageAgentEvents,
      compactExecutionBusy,
      freezonePlanSteps,
    ],
  );
  const activeWorkflowRuntime = useMemo(() => {
    if (!compactWorkflowRun || !["running", "paused", "failed"].includes(compactWorkflowRun.status)) {
      return null;
    }
    return canvasWorkflowRuntimeContextFromRun(compactWorkflowRun);
  }, [compactWorkflowRun]);
  // The context console keeps a compact three-state presentation while the
  // durable workflow remains the authoritative execution state.
  const directorConsolePlanSteps = useMemo(
    () => freezonePlanSteps.map((step) => ({
      ...step,
      status: step.status === "done" || step.status === "running"
        ? step.status
        : "pending" as const,
    })),
    [freezonePlanSteps],
  );
  const freezoneHasUserMessages = useMemo(
    () => isFreezoneLayout && chat.messages.some((message) => message.role === "user"),
    [isFreezoneLayout, chat.messages],
  );
  const showFreezoneAgentContext =
    !canvasOnlyProduct
    || freezoneHasUserMessages
    || chat.busy
    || pinnedCanvasNodes.length > 0
    || attachments.length > 0
    || structureProposals.length > 0;
  const directorConsoleState = useMemo(() => {
    if (!isFreezoneLayout) return null;
    return deriveDirectorConsoleState({
      skillIds: selectedCanvasSkillIds,
      pinnedNodes: pinnedCanvasNodes,
      selectedNode: selectedCanvasNode,
      planSteps: directorConsolePlanSteps,
      attachmentCount: attachments.length,
      busy: chat.busy,
      hasUserMessages: freezoneHasUserMessages,
      runMode: canvasAgentRunMode,
      availableSkills: availableCanvasAgentSkills,
    });
  }, [
    isFreezoneLayout,
    selectedCanvasSkillIds,
    pinnedCanvasNodes,
    selectedCanvasNode,
    directorConsolePlanSteps,
    attachments.length,
    chat.busy,
    freezoneHasUserMessages,
    canvasAgentRunMode,
    availableCanvasAgentSkills,
  ]);
  const isChatInitializing = !chat.historyReady && chat.messages.length === 0 && (chat.connecting || chat.connected);

  const hasSendableContent = draft.trim().length > 0 || attachments.length > 0;
  const canSend =
    hasSendableContent
    && chat.connected
    && chat.activeModelReady
    && !unresolvedStoreSkills
    && !preparingSend;
  const composerPrimaryAction = agentComposerPrimaryAction({
    busy: chat.busy,
    canvasProduct: canvasOnlyProduct,
  });
  const composerWaiting = chat.busy && (!hasSendableContent || !chat.connected || preparingSend);
  const composerBeamActive =
    composerInputFocused
    && chat.connected
    && !chat.busy
    && !preparingSend
    && queuedMessages.length === 0;
  const activeMessages = useMemo(
    () =>
      chat.messages.filter(
        (message) => !chat.deletedIds.has(message.id) && (chat.settings.showToolEvents || !isToolMessage(message)),
      ),
    [chat.deletedIds, chat.messages, chat.settings.showToolEvents],
  );
  const contextModel = useMemo(() => {
    const catalog = chat.modelCatalogs.village;
    const selectedId = chat.activeModel;
    return catalog?.models.find((model) => model.id === selectedId)
      ?? catalog?.models[0]
      ?? chat.models.find((model) => model.id === chat.activeModel)
      ?? chat.models[0];
  }, [chat.activeModel, chat.modelCatalogs, chat.models]);
  const agentContextUsage = useMemo(() => estimateAgentContextUsage({
    model: contextModel,
    messages: activeMessages,
    draft,
    streamText: chat.streamText,
    attachments,
  }), [activeMessages, attachments, chat.streamText, contextModel, draft]);
  const userMessageHistory = useMemo(
    () =>
      activeMessages
        .filter((message) => message.role === "user" && message.text.trim().length > 0)
        .map((message) => message.text),
    [activeMessages],
  );
  const pinnedMessages = useMemo(
    () => activeMessages.filter((message) => chat.pinnedIds.has(message.id)),
    [activeMessages, chat.pinnedIds],
  );

  useEffect(() => {
    const project = params.project?.trim();
    if (!project) return;
    return taskEventBus.on("*", (event) => {
      if (event.type !== "task_complete" && event.type !== "task_failed") return;
      // Freezone: left-bottom task center already owns run/fail status.
      // Do not spam Agent chat with generation lifecycle noise.
      if (isFreezoneLayout) return;
      const taskProject = (event.task.project_id ?? event.task.project).trim();
      if (taskProject !== project) return;

      const dedupeKey = `${event.type}:${event.task.task_key || event.task.task_id}`;
      if (notifiedTaskKeysRef.current.has(dedupeKey)) return;
      notifiedTaskKeysRef.current.add(dedupeKey);

      const label = buildChatTaskLabel(event.task, t);
      const text =
        event.type === "task_complete"
          ? `✅ ${label}已完成。你可以让我查看结果，或继续下一步。`
          : `${label}失败：${event.task.error || event.task.current_task || "未提供具体错误原因"}\n请根据错误处理前置条件后再继续。`;
      void chat.appendNotification(text);
    });
  }, [chat.appendNotification, isFreezoneLayout, params.project, t, taskEventBus]);

  const searchQuery = search.trim().toLowerCase();
  const visibleMessages = useMemo(
    () =>
      searchQuery
        ? activeMessages.filter((message) => message.text.toLowerCase().includes(searchQuery))
        : activeMessages,
    [activeMessages, searchQuery],
  );
  const messageWindow = useMemo(
    () => deriveMessageRenderWindow(visibleMessages, messageRenderLimit),
    [messageRenderLimit, visibleMessages],
  );
  const renderedMessages = messageWindow.items;
  const hiddenMessageCount = messageWindow.hiddenCount;
  const activeMessageCount = activeMessages.length;
  const lastActiveMessageId = activeMessages[activeMessages.length - 1]?.id ?? "";
  const deferStructuredRender =
    chat.busy && !chat.settings.showStructuredSourceWhileStreaming;
  const streamTextAlreadyRendered =
    Boolean(chat.streamText)
    && visibleMessages.some(
      (message) => message.role === "assistant" && message.text === chat.streamText,
    );
  const lastConversationalMessage = [...activeMessages]
    .reverse()
    .find((message) => message.role === "user" || message.role === "assistant");
  const lastUserMessage = [...activeMessages]
    .reverse()
    .find((message) => message.role === "user" && message.text.trim().length > 0);
  const activeTurnUserMessage = chat.activeTurnId
    ? activeMessages.find(
      (message) =>
        message.role === "user"
        && message.turnId === chat.activeTurnId
        && message.text.trim().length > 0,
    )
    : null;
  const activeTurnHasAssistantReply = Boolean(
    chat.activeTurnId
    && activeMessages.some(
      (message) =>
        message.role === "assistant"
        && message.turnId === chat.activeTurnId
        && message.text.trim().length > 0,
    ),
  );
  const lastUserHasAssistantReply = Boolean(
    lastUserMessage?.turnId
    && activeMessages.some(
      (message) =>
        message.role === "assistant"
        && message.turnId === lastUserMessage.turnId
        && message.text.trim().length > 0,
    ),
  );
  const currentStreamingAssistantId =
    deferStructuredRender && lastConversationalMessage?.role === "assistant"
      ? lastConversationalMessage.id
      : null;
  const isCurrentStreamingAssistantMessage = (message: ChatMessage): boolean =>
    message.role === "assistant" && message.id === currentStreamingAssistantId;
  const isStreamingAssistantMessage = (message: ChatMessage): boolean =>
    chat.busy
    && message.role === "assistant"
    && (
      message.id === currentStreamingAssistantId
      || (lastConversationalMessage?.role === "assistant" && message.id === lastConversationalMessage.id)
    );
  const showWaitingIndicator =
    chat.busy
    && !chat.streamText.trim()
    && (
      composerWaiting
      || (
        activeTurnUserMessage
          ? !activeTurnHasAssistantReply
          : (!lastUserMessage || !lastUserHasAssistantReply)
      )
    );
  useEffect(() => {
    messageHistoryAnchorRef.current = null;
    setMessageRenderLimit(DEFAULT_MESSAGE_RENDER_LIMIT);
  }, [params.project, searchQuery]);

  const revealOlderMessages = useCallback(() => {
    if (hiddenMessageCount <= 0) return;
    const el = scrollRef.current;
    if (el) {
      messageHistoryAnchorRef.current = {
        scrollHeight: el.scrollHeight,
        scrollTop: el.scrollTop,
      };
    }
    shouldStickToBottomRef.current = false;
    setMessageRenderLimit((current) =>
      nextMessageRenderLimit(current, visibleMessages.length),
    );
  }, [hiddenMessageCount, visibleMessages.length]);

  useLayoutEffect(() => {
    const anchor = messageHistoryAnchorRef.current;
    const el = scrollRef.current;
    if (!anchor || !el) return;
    el.scrollTop = anchor.scrollTop + (el.scrollHeight - anchor.scrollHeight);
    messageHistoryAnchorRef.current = null;
  }, [messageRenderLimit, renderedMessages.length]);

  const scrollToChatBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = scrollRef.current;
    if (!el) return;
    const top = Math.max(0, el.scrollHeight - el.clientHeight);
    el.scrollTo({ top, behavior });
    shouldStickToBottomRef.current = true;
    setShowScrollToBottom(false);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const updateStickiness = () => {
      const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      shouldStickToBottomRef.current = distanceToBottom < 96;
      setShowScrollToBottom(distanceToBottom > 180);
    };
    updateStickiness();
    el.addEventListener("scroll", updateStickiness, { passive: true });
    return () => el.removeEventListener("scroll", updateStickiness);
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      if (shouldStickToBottomRef.current || chat.busy) {
        scrollToChatBottom();
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [chat.busy, chat.messages, chat.streamText, showWaitingIndicator, scrollToChatBottom]);

  useEffect(() => {
    const list = messageListRef.current;
    if (!list || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (!shouldStickToBottomRef.current && !chat.busy) return;
      window.requestAnimationFrame(() => scrollToChatBottom());
    });
    observer.observe(list);
    return () => observer.disconnect();
  }, [chat.busy, scrollToChatBottom]);

  useEffect(() => {
    if (!chat.historyReady) return;
    const scrollKey = `${params.project ?? ""}:${activeMessageCount}:${lastActiveMessageId}`;
    if (historyScrollKeyRef.current === scrollKey) return;
    historyScrollKeyRef.current = scrollKey;
    shouldStickToBottomRef.current = true;
    let secondFrame = 0;
    const firstTimeout = window.setTimeout(scrollToChatBottom, 120);
    const secondTimeout = window.setTimeout(scrollToChatBottom, 360);
    const thirdTimeout = window.setTimeout(scrollToChatBottom, 800);
    const firstFrame = window.requestAnimationFrame(() => {
      scrollToChatBottom();
      secondFrame = window.requestAnimationFrame(() => scrollToChatBottom());
    });
    return () => {
      window.cancelAnimationFrame(firstFrame);
      if (secondFrame) window.cancelAnimationFrame(secondFrame);
      window.clearTimeout(firstTimeout);
      window.clearTimeout(secondTimeout);
      window.clearTimeout(thirdTimeout);
    };
  }, [activeMessageCount, chat.historyReady, lastActiveMessageId, params.project, scrollToChatBottom]);

  useEffect(() => {
    setQueuedMessages([]);
    setSelectedQueuedMessageId(null);
    setSelectedHistoryMessageIndex(null);
    setSelectedCanvasSkillIds(
      composerHandoffRef?.current.skillIds ?? initialSkillIds ?? (isFreezoneLayout
        ? loadCanvasAgentSkillIds(freezoneProjectId, freezoneCanvasId)
        : []),
    );
    setUploadedIngestFiles(loadUploadedIngestFiles(params.project?.trim()));
    setReingestConfirmation(null);
  }, [freezoneCanvasId, freezoneProjectId, isFreezoneLayout, params.project]);

  const recordUploadedFiles = useCallback(
    (project: string | undefined, prepared: PreparedIngestAttachment[]): UploadedIngestFile[] => {
      const additions = prepared
        .map(uploadedFileFromPrepared)
        .filter((item): item is UploadedIngestFile => Boolean(item));
      if (additions.length === 0) return uploadedIngestFiles;

      const next = mergeUploadedIngestFiles(uploadedIngestFiles, additions);
      setUploadedIngestFiles(next);
      saveUploadedIngestFiles(project, next);
      return next;
    },
    [uploadedIngestFiles],
  );

  const sendWithIngestAutomation = useCallback(
    async (
      text: string,
      messageAttachments: ChatAttachment[],
      displayText = text,
      engine: AgentEngine = chat.activeEngine,
    ): Promise<boolean> => {
      let nextText = text;
      let transportAttachments = messageAttachments;
      let contextUploadedFiles = uploadedIngestFiles;
      const project = params.project?.trim();
      const videoIntent = VIDEO_CREATION_RE.test(text);
      const hasNovelAttachments = messageAttachments.some(isNovelAttachment);

      if (reingestConfirmation) {
        if (reingestConfirmation.stage === "choose_overwrite") {
          if (!isOverwriteChoice(text)) {
            const pending = reingestConfirmation;
            setReingestConfirmation(null);
            return chat.send(
              displayText,
              [],
              appendAttachmentAnalysisContext(text, buildReingestCancelledContext(pending)),
              { engine },
            );
          }

          const nextPending = {
            ...reingestConfirmation,
            stage: "confirm_clear" as const,
          };
          setReingestConfirmation(nextPending);
          return chat.send(
            text,
            [],
            appendAttachmentAnalysisContext(text, buildReingestConfirmationContext(nextPending)),
            { engine },
          );
        }

        if (!isFinalOverwriteConfirmation(text)) {
          const pending = reingestConfirmation;
          setReingestConfirmation(null);
          return chat.send(
            text,
            [],
            appendAttachmentAnalysisContext(text, buildReingestCancelledContext(pending)),
            { engine },
          );
        }

        setPreparingSend(true);
        try {
          const started = await startNovelIngest(
            reingestConfirmation.project,
            reingestConfirmation.filename,
            { rebuild: true },
          );
          nextText = appendIngestAutomationContext(text, {
            filename: reingestConfirmation.filename,
            taskType: started.task_type,
            taskKey: started.task_key,
            message: started.message,
            rebuild: true,
          });
          toast.success(t("aiAssistant.ingestAutomationStarted", { filename: reingestConfirmation.filename }));
          setReingestConfirmation(null);
          return chat.send(displayText, [], nextText, { engine });
        } catch (error) {
          const message = backendErrorToastMessage(error, t);
          toast.error(t("aiAssistant.ingestAutomationFailed", { message }));
          return false;
        } finally {
          setPreparingSend(false);
        }
      }

      if (videoIntent && hasNovelAttachments) {
        const project = params.project?.trim();
        if (!project) {
          toast.error(t("aiAssistant.ingestAutomationNoProject"));
          return false;
        }

        setPreparingSend(true);
        try {
          const prepared = await uploadAttachmentsForIngest(project, messageAttachments, t);
          surfaceFormatCheckWarnings(prepared, t, (formatCheck, filename) =>
            setFormatCheckDetails({ formatCheck, filename }),
          );
          transportAttachments = prepared.map((item) => item.attachment);
          contextUploadedFiles = recordUploadedFiles(project, prepared);
          const uploaded = prepared.find((item) => item.upload)?.upload;
          if (!uploaded) {
            const error = prepared.find((item) => item.error)?.error;
            throw new Error(error || t("aiAssistant.ingestAutomationMissingFile"));
          }
          if (await projectHasIngestedContent(project)) {
            const pending: ReingestConfirmation = {
              stage: "choose_overwrite",
              filename: uploaded.filename,
              project,
              originalText: text,
            };
            setReingestConfirmation(pending);
            nextText = appendAttachmentAnalysisContext(
              text,
              buildReingestConfirmationContext(pending),
            );
            return chat.send(displayText, transportAttachments, nextText, { engine });
          }
          const started = await startNovelIngest(project, uploaded.filename);
          nextText = appendIngestAutomationContext(text, {
            filename: uploaded.filename,
            taskType: started.task_type,
            taskKey: started.task_key,
            message: started.message,
            rebuild: false,
          });
          toast.success(t("aiAssistant.ingestAutomationStarted", { filename: uploaded.filename }));
        } catch (error) {
          const message = backendErrorToastMessage(error, t);
          toast.error(t("aiAssistant.ingestAutomationFailed", { message }));
          return false;
        } finally {
          setPreparingSend(false);
        }
      } else if (videoIntent && !hasNovelAttachments && uploadedIngestFiles.length > 0) {
        if (!project) {
          toast.error(t("aiAssistant.ingestAutomationNoProject"));
          return false;
        }

        setPreparingSend(true);
        try {
          const uploaded = uploadedIngestFiles[uploadedIngestFiles.length - 1];
          if (await projectHasIngestedContent(project)) {
            const pending: ReingestConfirmation = {
              stage: "choose_overwrite",
              filename: uploaded.filename,
              project,
              originalText: text,
            };
            setReingestConfirmation(pending);
            nextText = appendAttachmentAnalysisContext(
              text,
              buildReingestConfirmationContext(pending),
            );
            return chat.send(displayText, [], nextText, { engine });
          }
          const started = await startNovelIngest(project, uploaded.filename);
          nextText = appendIngestAutomationContext(text, {
            filename: uploaded.filename,
            taskType: started.task_type,
            taskKey: started.task_key,
            message: started.message,
            rebuild: false,
          });
          toast.success(t("aiAssistant.ingestAutomationStarted", { filename: uploaded.filename }));
        } catch (error) {
          const message = backendErrorToastMessage(error, t);
          toast.error(t("aiAssistant.ingestAutomationFailed", { message }));
          return false;
        } finally {
          setPreparingSend(false);
        }
      } else if (messageAttachments.length > 0) {
        setPreparingSend(true);
        try {
          const prepared = project
            ? await uploadAttachmentsForIngest(project, messageAttachments, t)
            : messageAttachments.map((attachment) => ({ attachment, original: attachment }));
          surfaceFormatCheckWarnings(prepared, t, (formatCheck, filename) =>
            setFormatCheckDetails({ formatCheck, filename }),
          );
          transportAttachments = prepared.map((item) => item.attachment);
          contextUploadedFiles = recordUploadedFiles(project, prepared);
          const context = await buildAttachmentAnalysisContext(
            project,
            prepared,
          );
          nextText = appendAttachmentAnalysisContext(text, context);
        } finally {
          setPreparingSend(false);
        }
      }

      if (shouldReportUploadedFiles(text)) {
        nextText = appendAttachmentAnalysisContext(
          nextText,
          buildUploadedFilesContext(project, contextUploadedFiles),
        );
      }

      return chat.send(displayText, transportAttachments, nextText, { engine });
    },
    [chat, params.project, recordUploadedFiles, reingestConfirmation, t, uploadedIngestFiles],
  );

  const updateCanvasAgentSkills = useCallback((updater: (current: string[]) => string[]) => {
    setSelectedCanvasSkillIds((current) => {
      const next = updater(current);
      return isFreezoneLayout
        ? saveCanvasAgentSkillIds(freezoneProjectId, freezoneCanvasId, next)
        : next;
    });
  }, [freezoneCanvasId, freezoneProjectId, isFreezoneLayout]);

  const toggleCanvasAgentSkill = useCallback((skillId: string) => {
    updateCanvasAgentSkills((current) => current.includes(skillId)
      ? current.filter((id) => id !== skillId)
      : [...current, skillId]);
    draftInputRef.current?.focus();
  }, [updateCanvasAgentSkills]);

  const switchCanvasAgentRunMode = useCallback((mode: CanvasAgentRunMode) => {
    setCanvasAgentRunMode(saveCanvasAgentRunMode(mode));
    draftInputRef.current?.focus();
  }, []);

  const mountCanvasAgentSkillFromSlash = useCallback((skillId: string) => {
    updateCanvasAgentSkills((current) => (current.includes(skillId) ? current : [...current, skillId]));
    setDraft((current) => current.replace(/(?:^|\s)\/[^\s]*$/, (chunk) => (chunk.startsWith(" ") ? " " : "")).replace(/\s+$/, ""));
    setSlashMenuOpen(false);
    draftInputRef.current?.focus();
  }, [updateCanvasAgentSkills]);

  const undoLastAgentApply = useCallback(() => {
    const scope = { projectId: freezoneProjectId, canvasId: freezoneCanvasId };
    const record = getLastStructureApply(scope);
    if (!record) return;
    const store = useCanvasStore.getState();
    let undone = 0;
    for (let index = 0; index < Math.max(1, record.undoSteps); index += 1) {
      if (!store.undo()) break;
      undone += 1;
    }
    clearLastStructureApply(scope);
    if (undone > 0) {
      toast.success(`已撤销结构应用 · ${undone} 步画布历史`);
    } else {
      toast.error("无法撤销（历史栈为空）");
    }
  }, [freezoneCanvasId, freezoneProjectId]);


  useEffect(() => {
    const shell = composerShellRef.current;
    if (!shell) return;
    const beam = attachBorderBeam(shell, {
      size: "md",
      colorVariant: "colorful",
      theme: "dark",
      active: false,
      borderRadius: 16,
      strength: 0.9,
      duration: 1.96,
    });
    composerBeamRef.current = beam;
    return () => {
      composerBeamRef.current = null;
      beam.destroy();
    };
  }, []);

  useEffect(() => {
    composerBeamRef.current?.setActive(composerBeamActive);
  }, [composerBeamActive]);

  useEffect(() => {
    if (chat.busy || !chat.connected || preparingSend || queuedMessages.length === 0) return;
    const selectedIndex = selectedQueuedMessageId
      ? queuedMessages.findIndex((message) => message.id === selectedQueuedMessageId)
      : -1;
    const nextIndex = selectedIndex >= 0 ? selectedIndex : 0;
    const nextMessage = queuedMessages[nextIndex];
    const remainingMessages = queuedMessages.filter((_, index) => index !== nextIndex);
    void sendWithIngestAutomation(
      nextMessage.text,
      nextMessage.attachments,
      nextMessage.displayText,
      nextMessage.engine,
    ).then((sent) => {
      if (!sent) return;
      setQueuedMessages(remainingMessages);
      setSelectedQueuedMessageId(remainingMessages[0]?.id ?? null);
    });
  }, [
    chat.busy,
    chat.connected,
    preparingSend,
    queuedMessages,
    selectedQueuedMessageId,
    sendWithIngestAutomation,
  ]);

  useEffect(() => {
    if (queuedMessages.length === 0) {
      if (selectedQueuedMessageId) setSelectedQueuedMessageId(null);
      return;
    }
    if (selectedQueuedMessageId && queuedMessages.some((message) => message.id === selectedQueuedMessageId)) return;
    setSelectedQueuedMessageId(queuedMessages[0].id);
  }, [queuedMessages, selectedQueuedMessageId]);

  useLayoutEffect(() => {
    if (!restoreDraftFocusRef.current) return;
    restoreDraftFocusRef.current = false;
    const textarea = draftInputRef.current;
    if (!textarea || textarea.disabled) return;
    if (document.activeElement === textarea) return;
    textarea.focus({ preventScroll: true });
    const end = textarea.value.length;
    textarea.setSelectionRange(end, end);
  }, [draft]);

  const submit = () => {
    if (skillReadinessMessage) {
      toast.error(skillReadinessMessage);
      return;
    }
    // Explicit paste/upload images win over auto canvas_image refs.
    // Mixing selected-node media (often a different character) with paste vision
    // caused models to describe the wrong image color/subject.
    const skipAutoCanvasRefs =
      isFreezoneLayout && hasExplicitUserImageAttachment(attachments);
    const canvasRefs =
      isFreezoneLayout && !skipAutoCanvasRefs
        ? buildCanvasReferenceAttachments({
          pinnedNodes: pinnedCanvasNodes,
        })
        : [];
    const outboundAttachments = mergeReferenceAttachments(attachments, canvasRefs);
    const hasCurrentContent =
      draft.trim().length > 0
      || outboundAttachments.length > 0
      || (isFreezoneLayout && pinnedCanvasNodes.length > 0);
    if (!hasCurrentContent || preparingSend) return;
    setSelectedHistoryMessageIndex(null);
    const displayText = draft.trim()
      || (outboundAttachments.length > 0
        ? `请分析我引用的 ${outboundAttachments.length} 个素材/节点`
        : t("aiAssistant.attachmentOnlyPrompt"));
    if (!chat.busy) {
      setActiveWorkflowRun((current) => (
        current && activeWorkflowRunFromList([current]) ? current : null
      ));
    }
    const liveCanvasState = useCanvasStore.getState();
    const liveSelectedNodeId = resolveLiveSelectedNodeId(
      liveCanvasState.nodes,
      liveCanvasState.selectedNodeId,
    );
    // A stale persisted `selected` bit must not trigger direct task controls.
    const controlSelectedNodeId = liveCanvasState.selectedNodeId
      && liveCanvasState.selectedNodeId === liveSelectedNodeId
      ? liveSelectedNodeId
      : null;
    const xiaoshuRoute = routeXiaoshuRequest({
      userText: displayText,
      hasExplicitAttachments: attachments.length > 0,
      canvasContext: isFreezoneLayout
        ? {
          selectedNodeId: controlSelectedNodeId,
        }
        : null,
    });
    if (xiaoshuRoute.kind === "direct_task_control") {
      if (xiaoshuRoute.operation !== "stop_task" && canvasAgentRunMode !== "auto") {
        toast.info("当前是草稿模式；切换到实战生成模式后，小树会直接运行节点");
        return;
      }
      setPreparingSend(true);
      void executeXiaoshuTaskControl({
        projectId: freezoneProjectId || params.project || "",
        canvasId: freezoneCanvasId,
        nodeId: xiaoshuRoute.nodeId,
        operation: xiaoshuRoute.operation,
        reply: xiaoshuRoute.reply,
        runMode: canvasAgentRunMode,
        userText: displayText,
        appendExchange: chat.appendLocalExchange,
      }).then(() => {
        setDraft("");
        setAttachments([]);
        updatePinnedCanvasNodeIds(() => []);
      }).catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        toast.error(`节点任务控制失败：${message}`);
      }).finally(() => setPreparingSend(false));
      return;
    }
    if (!chat.connected) {
      toast.error("小树的模型协作通道正在重连；确定性节点操作仍可直接执行");
      return;
    }
    const executionRoute = routeCanvasAgentExecution(displayText);
    const buildAgentText = (
      workflowRuntime: CanvasWorkflowRuntimeContext | null | undefined = activeWorkflowRuntime,
    ) => isFreezoneLayout
      ? buildCanvasAgentRequest({
        userText: displayText,
        executionLane: executionRoute.lane,
        skillIds: selectedCanvasSkillIds,
        explicitSkillIds: selectedCanvasSkillIds,
        additionalSkills: installedStoreAgentSkills,
        canvasContext: currentCanvasAgentContext(
          canvasId,
          freezoneProjectId || params.project,
          canvasRevision,
          projectStyleId,
          agentModelCatalog,
        ),
        pinnedNodes: pinnedCanvasNodes,
        runMode: canvasAgentRunMode,
        workflowRuntime,
        forceStructured: true,
      })
      : displayText;
    const text = buildAgentText();
    const queuedAttachments = outboundAttachments.map((attachment) => ({ ...attachment }));
    if (chat.busy) {
      const pendingDirection: QueuedSendItem = {
        id: `queue-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        text,
        displayText,
        attachments: queuedAttachments,
        engine: "village",
        createdAt: Date.now(),
      };
      const enqueueDirection = () => {
        setQueuedMessages((current) => [...current, pendingDirection]);
      };
      const canSteerActiveTurn = agentComposerCanSteer({
        text: draft,
        attachmentCount: queuedAttachments.length,
        pinnedNodeCount: pinnedCanvasNodes.length,
      });
      setDraft("");
      setAttachments([]);
      // The queued message owns a copied snapshot of these references.
      // Consume the current composer pins now so they do not remain attached
      // to the next turn while the agent is still processing this one.
      updatePinnedCanvasNodeIds(() => []);
      if (canSteerActiveTurn) {
        void chat.steer(displayText).then((accepted) => {
          if (accepted) {
            toast.success("已发送调整方向，当前任务继续");
            return;
          }
          enqueueDirection();
        });
      } else {
        enqueueDirection();
      }
      return;
    }
    const sendPrepared = (preparedText: string) => sendWithIngestAutomation(
      preparedText,
      queuedAttachments,
      displayText,
      "village",
    ).then((sent) => {
      if (!sent) return;
      setDraft("");
      setAttachments([]);
      // Consume fixed canvas references only after the transport succeeds.
      // On failure they stay in the composer so the user can retry without
      // losing the referenced media.
      updatePinnedCanvasNodeIds(() => []);
    });
    void sendPrepared(text);
  };

  const homeAutoSentRef = useRef(false);
  useEffect(() => {
    if (!autoSendInitialDraft || homeAutoSentRef.current || composerHandoffRef?.current.autoSendClaimed || !canSend || !chat.historyReady || chat.busy || canvasRevision == null) return;
    homeAutoSentRef.current = true;
    if (composerHandoffRef && !claimHomeAutoSend(composerHandoffRef.current, true)) return;
    submit();
  }, [autoSendInitialDraft, canSend, chat.historyReady, chat.busy, canvasRevision, composerHandoffRef, submit]);

  const handleComposerKeyDown = (event: ReactKeyboardEvent) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.defaultPrevented) return;
    const target = event.target as HTMLElement | null;
    if (
      target &&
      target !== draftInputRef.current &&
      (target.tagName === "BUTTON" || target.tagName === "INPUT" || target.getAttribute("role") === "button")
    ) {
      return;
    }
    event.preventDefault();
    submit();
  };

  const selectQueuedMessageByOffset = (offset: number) => {
    if (queuedMessages.length === 0) return;
    setSelectedQueuedMessageId((current) => {
      const currentIndex = current
        ? queuedMessages.findIndex((message) => message.id === current)
        : -1;
      const baseIndex = currentIndex >= 0 ? currentIndex : 0;
      const nextIndex = (baseIndex + offset + queuedMessages.length) % queuedMessages.length;
      return queuedMessages[nextIndex].id;
    });
  };

  const selectHistoryMessage = (direction: "older" | "newer") => {
    if (userMessageHistory.length === 0) return false;
    if (direction === "older") {
      const nextIndex =
        selectedHistoryMessageIndex === null
          ? userMessageHistory.length - 1
          : Math.max(0, selectedHistoryMessageIndex - 1);
      setSelectedHistoryMessageIndex(nextIndex);
      setDraft(userMessageHistory[nextIndex]);
      restoreDraftFocusRef.current = true;
      return true;
    }
    if (selectedHistoryMessageIndex === null) return false;
    if (selectedHistoryMessageIndex >= userMessageHistory.length - 1) {
      setSelectedHistoryMessageIndex(null);
      setDraft("");
      restoreDraftFocusRef.current = true;
      return true;
    }
    const nextIndex = selectedHistoryMessageIndex + 1;
    setSelectedHistoryMessageIndex(nextIndex);
    setDraft(userMessageHistory[nextIndex]);
    restoreDraftFocusRef.current = true;
    return true;
  };

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach((file) => {
      if (!isAllowedScriptUpload(file)) return;
      const reader = new FileReader();
      reader.addEventListener("load", () => {
        const dataUrl = String(reader.result || "");
        setAttachments((current) => [
          ...current,
          {
            id: `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            type: file.type.startsWith("image/") ? "image" : "file",
            mimeType: file.type || "application/octet-stream",
            fileName: file.name,
            fileSize: file.size,
            content: dataUrl,
          },
        ]);
      });
      reader.readAsDataURL(file);
    });
    if (fileInputRef.current) fileInputRef.current.value = "";
    window.requestAnimationFrame(() => {
      draftInputRef.current?.focus({ preventScroll: true });
    });
  };

  const eventHasFiles = (event: ReactDragEvent<HTMLElement>): boolean =>
    Array.from(event.dataTransfer.types).includes("Files");

  const resolveDragFileState = (event: ReactDragEvent<HTMLElement>): "valid" | "invalid" => {
    const items = Array.from(event.dataTransfer.items).filter((item) => item.kind === "file");
    if (items.length === 0) return "valid";
    if (isFreezoneLayout) {
      return items.every((item) => item.type.startsWith("image/") || (item.getAsFile()?.type.startsWith("image/") ?? false))
        ? "valid"
        : "invalid";
    }
    return items.every((item) => {
      const file = item.getAsFile();
      if (file) return isAllowedScriptDragItem(file);
      return isAllowedScriptDragItem({ type: item.type });
    })
      ? "valid"
      : "invalid";
  };

  const handleComposerDragEnter = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!ENABLE_SUPERCHAT_FILE_UPLOAD && !isFreezoneLayout) return;
    if (!eventHasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current += 1;
    setDragFileState(resolveDragFileState(event));
  };

  const handleComposerDragOver = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!ENABLE_SUPERCHAT_FILE_UPLOAD && !isFreezoneLayout) return;
    if (!eventHasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    const nextState = resolveDragFileState(event);
    setDragFileState(nextState);
    event.dataTransfer.dropEffect = nextState === "valid" ? "copy" : "none";
  };

  const handleComposerDragLeave = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!ENABLE_SUPERCHAT_FILE_UPLOAD && !isFreezoneLayout) return;
    if (!eventHasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragFileState(null);
  };

  const handleComposerDrop = (event: ReactDragEvent<HTMLDivElement>) => {
    // Freezone: accept real image references even when novel file upload is disabled.
    if (isFreezoneLayout && eventHasFiles(event)) {
      event.preventDefault();
      event.stopPropagation();
      dragDepthRef.current = 0;
      setDragFileState(null);
      const files = Array.from(event.dataTransfer.files || []).filter((file) => file.type.startsWith("image/"));
      if (files.length === 0) {
        toast.error("小树目前支持拖入图片真引用（≤5MB）");
        return;
      }
      void (async () => {
        const next: ChatAttachment[] = [];
        for (const file of files) {
          const attachment = await fileToImageAttachment(file);
          if (attachment) next.push(attachment);
        }
        if (next.length === 0) {
          toast.error("图片过大或无法读取");
          return;
        }
        setAttachments((current) => mergeReferenceAttachments(current, next));
        toast.success(`已加入 ${next.length} 张真引用图片`);
      })();
      return;
    }
    if (!ENABLE_SUPERCHAT_FILE_UPLOAD) return;
    if (!eventHasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = 0;
    setDragFileState(null);
    addFiles(event.dataTransfer.files);
  };

  const toggleSpeech = () => {
    if (recording) {
      speechRef.current?.stop();
      setRecording(false);
      return;
    }
    const recognition = createSpeechRecognition();
    if (!recognition) return;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "zh-CN";
    recognition.onresult = (event) => {
      let text = "";
      for (let i = 0; i < event.results.length; i += 1) {
        text += event.results[i][0]?.transcript ?? "";
      }
      setDraft(text);
    };
    recognition.onend = () => setRecording(false);
    speechRef.current = recognition;
    setRecording(true);
    recognition.start();
  };

  return (
    <div
      id={isFreezoneLayout && canvasOnlyProduct ? "village-agent-shell-v4" : undefined}
      className={cn(
        "relative flex h-full min-h-0 overflow-hidden",
        !canvasOnlyProduct && "bg-background",
        isFreezoneLayout && cn(
          !canvasOnlyProduct && "neo-workbench-drawer bg-transparent",
          FREEZONE_AGENT_DRAWER_CLASS,
          canvasOnlyProduct && "village-agent-shell-v4",
        ),
      )}
    >
      {isFreezoneLayout && canvasOnlyProduct && (
        <span className="sr-only" data-agent-surface="village-agent-v4">
          画布内小树创作台
        </span>
      )}
      {!isFreezoneLayout && (
        <HeaderControlPortal
          chat={chat}
          searchOpen={searchOpen}
          onToggleSearch={() => setSearchOpen((value) => !value)}
        />
      )}
      <section className={cn("relative z-10 flex min-w-0 flex-1 flex-col", isFreezoneLayout && "agent-chat-surface neo-agent-chat-surface")}>
        {isFreezoneLayout && (
          <div
            className={cn(
              "agent-drawer-header neo-agent-header flex shrink-0 items-center gap-2",
              !canvasOnlyProduct && "min-h-[64px] border-b border-white/[0.08] bg-[linear-gradient(180deg,rgba(22,22,26,0.98),rgba(9,9,11,0.96))] px-3.5 py-2.5 backdrop-blur-2xl",
              isVillageFloating && "village-agent-floating-header cursor-grab active:cursor-grabbing",
            )}
            data-agent-header="libtv-clean"
            data-agent-drag-handle={isVillageFloating ? "true" : undefined}
            data-ui-revision={canvasOnlyProduct ? "village-agent-libtv-v4" : "libtv-ui-v2"}
          >
            <div className="flex min-w-0 flex-1 items-center gap-3">
              {canvasOnlyProduct ? (
                <div className="village-agent-titlebar flex min-w-0 items-center gap-2">
                  <span className="village-agent-title truncate text-[15px] font-semibold tracking-[-0.025em] text-[#f2f2f3]">
                    {chat.conversations.find((conversation) => conversation.id === chat.activeConversationId)?.title?.trim() || "新对话"}
                  </span>
                  <span className="village-agent-status-pill inline-flex shrink-0 items-center gap-1 rounded-full border border-white/[0.07] bg-white/[0.035] px-1.5 py-0.5 text-[9px] font-medium text-white/45">
                    <span
                      className={cn(
                        "size-1.5 rounded-full",
                        chat.busy
                          ? "animate-pulse bg-amber-300"
                          : chat.connected
                            ? "bg-emerald-400"
                            : "bg-white/25",
                      )}
                    />
                    {chat.busy ? "执行中" : chat.connected ? "在线" : "连接中"}
                  </span>
                </div>
              ) : (
                <>
                  <AgentMark size="lg" withWordmark />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <div className="truncate text-[15px] font-semibold tracking-[-0.02em] text-[#f7f7f8]">
                        {agentBrandName}
                      </div>
                      <span className="neo-agent-status inline-flex shrink-0 items-center gap-1 rounded-full border border-white/[0.07] bg-white/[0.035] px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.055em] text-white/45">
                        <span
                          className={cn(
                            "size-1.5 rounded-full",
                            chat.busy
                              ? "animate-pulse bg-amber-300"
                              : chat.connected
                                ? "bg-emerald-400"
                                : "bg-white/25",
                          )}
                        />
                        {chat.busy ? "思考中" : chat.connected ? "就绪" : "连接中"}
                      </span>
                    </div>
                    <div className="mt-0.5 truncate text-[11px] leading-4 text-white/45">
                      {LIBTV_HEADER_SUBTITLE}
                    </div>
                  </div>
                </>
              )}
            </div>
            <div className="village-agent-header-actions flex shrink-0 items-center gap-1">
              {!canvasOnlyProduct && (
                <ControlBar
                  chat={chat}
                  compact
                  searchOpen={searchOpen}
                  onToggleSearch={() => setSearchOpen((value) => !value)}
                />
              )}
              {canvasOnlyProduct && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => void chat.startNewConversation()}
                  disabled={chat.busy || chat.conversationsLoading}
                  aria-label="新对话"
                  title="新对话"
                  className="village-agent-header-new rounded-full border border-transparent text-white/40 hover:border-white/[0.08] hover:bg-white/[0.06] hover:text-[#f7f7f7]"
                >
                  <Plus className="size-4" strokeWidth={2.05} />
                </Button>
              )}
              {canvasOnlyProduct && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setAgentCliSkillOpen(true)}
                  aria-label="CLI & Skill"
                  title="CLI & Skill"
                  className="village-agent-header-cli rounded-full border border-transparent text-white/40 hover:border-white/[0.08] hover:bg-white/[0.06] hover:text-[#f7f7f7]"
                >
                  <Braces className="size-4" strokeWidth={2.05} />
                </Button>
              )}
              {canvasOnlyProduct && (
                <ConversationHistoryMenu
                  conversations={chat.conversations}
                  activeConversationId={chat.activeConversationId}
                  loading={chat.conversationsLoading}
                  busy={chat.busy}
                  deletingId={chat.conversationDeletingId}
                  onRefresh={chat.refreshConversations}
                  onSwitch={chat.switchConversation}
                  onDelete={chat.deleteConversation}
                />
              )}
              {canvasOnlyProduct && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setSkillDrawerOpen((open) => !open)}
                  aria-expanded={skillDrawerOpen}
                  aria-label="打开技能"
                  title="技能"
                  className="village-agent-header-skill rounded-full border border-transparent text-white/40 hover:border-white/[0.08] hover:bg-white/[0.06] hover:text-[#f7f7f7]"
                >
                  <Star className="size-4" strokeWidth={2.05} />
                </Button>
              )}
              {canvasOnlyProduct && (
                <DropdownMenu>
                  <DropdownMenuTrigger
                    render={
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Agent 设置"
                        title="Agent 设置"
                        className="village-agent-header-more rounded-full border border-transparent text-white/40 hover:border-white/[0.08] hover:bg-white/[0.06] hover:text-[#f7f7f7]"
                      />
                    }
                  >
                    <MoreHorizontal className="size-4" aria-hidden />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    side="bottom"
                    align="end"
                    sideOffset={8}
                    className="village-agent-header-menu w-48"
                  >
                    <DropdownMenuItem
                      closeOnClick
                      onClick={() => setAgentSettingsOpen(true)}
                    >
                      <Settings2 className="size-4" />
                      Agent 设置
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      closeOnClick
                      onClick={() => setSearchOpen((value) => !value)}
                    >
                      <Search className="size-4" />
                      {searchOpen ? "关闭消息搜索" : "搜索消息"}
                    </DropdownMenuItem>
                    <DropdownMenuItem closeOnClick onClick={() => setAgentMemoryOpen(true)}>
                      <Brain className="size-4" />
                      成长记忆
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      closeOnClick
                      onClick={() => chat.setSettings({ showToolEvents: !chat.settings.showToolEvents })}
                    >
                      <ListTree className="size-4" />
                      {chat.settings.showToolEvents ? "隐藏执行事件" : "显示执行事件"}
                    </DropdownMenuItem>
                    {onRequestPresentationToggle && (
                      <DropdownMenuItem closeOnClick onClick={onRequestPresentationToggle}>
                        {isVillageFloating ? (
                          <PanelRightClose className="size-4" />
                        ) : (
                          <PictureInPicture2 className="size-4" />
                        )}
                        {isVillageFloating ? "停靠到画布右侧" : "切换浮动窗口"}
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
            {onRequestClose && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={onRequestClose}
                aria-label={t("freezone.chat.close")}
                title={t("freezone.chat.close")}
                className="rounded-full border border-transparent text-white/40 hover:border-white/[0.08] hover:bg-white/[0.06] hover:text-[#f7f7f7]"
              >
                <X className="size-4" />
              </Button>
            )}
          </div>
        )}
        {chat.recovery && !canvasOnlyProduct && (
          <div className="border-b border-amber-400/25 bg-amber-400/10 px-3 py-2.5 text-xs text-amber-100">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="font-semibold">已保存小树恢复点</div>
                <div className="mt-0.5 text-amber-100/80">{chat.recovery.message}</div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-amber-100/60">
                  <span>原因：{chat.recovery.packet.retry_reason || "worker_lost"}</span>
                  {chat.recovery.packet.pending_tool && (
                    <span>阶段：{chat.recovery.packet.pending_tool}</span>
                  )}
                  {chat.recovery.packet.canvas?.revision != null && (
                    <span>Canvas rev {chat.recovery.packet.canvas.revision}</span>
                  )}
                </div>
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!chat.connected || chat.busy}
                onClick={() => chat.resumeRecovery()}
                className="h-7 shrink-0 border-amber-300/35 bg-amber-300/10 px-2.5 text-[11px] text-amber-50 hover:bg-amber-300/20"
              >
                {chat.recovery.autoRetrying ? "自动续跑中…" : "从恢复点继续"}
              </Button>
            </div>
          </div>
        )}
        {chat.error && !chat.recovery && (
          <div
            className={cn(
              "text-xs text-destructive",
              canvasOnlyProduct
                ? "mx-3 mt-2 rounded-xl border border-red-300/15 bg-red-400/[0.055] px-3 py-2.5 text-red-100/78"
                : "border-b border-destructive/20 bg-destructive/8 px-3 py-2",
            )}
            role="alert"
          >
            {canvasOnlyProduct && <div className="mb-0.5 font-medium text-red-100">本轮未完成</div>}
            <div className={cn(canvasOnlyProduct && "line-clamp-3 break-words text-[10px] leading-4 text-red-100/55")} title={chat.error}>
              {chat.error}
            </div>
            {canvasOnlyProduct && (
              <div className="mt-1.5 text-[9px] text-white/32">当前输入和画布现场已保留，可调整要求后重新发送。</div>
            )}
          </div>
        )}

        {isFreezoneLayout && !canvasOnlyProduct && (!isVillageFloating || chat.busy) && (
          <FreezoneAgentRunBar
            running={chat.busy}
            connected={chat.connected}
            toolHint={chat.progress?.message ?? (chat.busy ? LIBTV_EXECUTING : null)}
            stage={chat.progress?.stage}
            workflow={chat.progress?.workflow}
          />
        )}
        {/* Chat-first: hide dense director workbench unless expanded or structure decision pending. */}
        {isFreezoneLayout && directorConsoleState && showFreezoneAgentContext && !canvasOnlyProduct && (
          (!directorConsoleCollapsed || structureProposals.length > 0) ? (
            <FreezoneDirectorConsole
              state={directorConsoleState}
              collapsed={directorConsoleCollapsed && structureProposals.length === 0}
              onToggleCollapsed={() => setDirectorConsoleCollapsed((value) => !value)}
              onRemoveSkill={toggleCanvasAgentSkill}
              onToggleSkill={toggleCanvasAgentSkill}
              onUnpinNode={(nodeId) => {
                updatePinnedCanvasNodeIds((current) => current.filter((id) => id !== nodeId));
              }}
              proposals={structureProposals}
              lastApply={lastStructureApply}
              onApplyProposal={(commandId) => {
                requestStructureApply(commandId, {
                  projectId: freezoneProjectId,
                  canvasId: freezoneCanvasId,
                });
              }}
              onDismissProposal={(commandId) => {
                dismissStructureProposal(commandId, {
                  projectId: freezoneProjectId,
                  canvasId: freezoneCanvasId,
                });
              }}
              onUndoLastApply={undoLastAgentApply}
            />
          ) : isVillageFloating ? null : (
            <FreezoneAgentContextCard
              state={directorConsoleState}
              onExpand={() => setDirectorConsoleCollapsed(false)}
            />
          )
        )}
        {chat.approvals.map((approval) => (
          <ApprovalCard
            key={approval.id}
            approval={approval}
            onResolve={(decision) => chat.resolveApproval(approval, decision)}
          />
        ))}

        <PinnedPanel
          messages={pinnedMessages}
          onClear={chat.clearPinned}
          onTogglePin={chat.togglePin}
        />

        {searchOpen && (
          <SearchBar
            query={search}
            onChange={setSearch}
            onClose={() => setSearchOpen(false)}
          />
        )}

        <div className="relative min-h-0 flex-1">
          <div
            ref={scrollRef}
            className={cn(
              "h-full overflow-y-auto px-3 py-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
              isFreezoneLayout && "flex flex-col px-4 py-3 [scrollbar-gutter:stable] [scrollbar-width:thin] [&::-webkit-scrollbar]:block [&::-webkit-scrollbar]:w-1.5",
              isVillageFloating && "village-agent-floating-scroll",
            )}
          >
            {isChatInitializing ? (
              <div className={cn("mx-auto flex h-full w-full max-w-[760px] items-center justify-center text-center", isFreezoneLayout && "max-w-none")}>
                <div className="max-w-72 text-sm text-muted-foreground">
                  <div className="mb-3 flex justify-center text-primary" aria-hidden="true">
                    <DotsIndicator />
                  </div>
                  <div className="mb-2 font-medium text-foreground">
                    {chat.connected ? t("aiAssistant.syncingHistoryTitle") : t("aiAssistant.connecting")}
                  </div>
                  <div className="text-xs leading-5">{t("aiAssistant.syncingHistoryDescription")}</div>
                </div>
              </div>
            ) : chat.messages.length === 0 && !chat.streamText && !showWaitingIndicator ? (
              isFreezoneLayout ? (
                <div
                  className={cn(
                    "flex w-full flex-col pt-6",
                    canvasOnlyProduct ? "mt-5 justify-start" : "mt-auto justify-end",
                    isVillageFloating && "village-agent-floating-welcome",
                  )}
                >
                  <FreezoneWelcomeSkills
                    selectedIds={selectedCanvasSkillIds}
                    onToggle={toggleCanvasAgentSkill}
                    disabled={preparingSend}
                    displayName={username || "创作者"}
                    showSkillPicker
                    compact={canvasOnlyProduct}
                  />
                </div>
              ) : (
                <div className="mx-auto flex h-full w-full max-w-[760px] items-center justify-center text-center">
                  <div className="max-w-64 text-sm text-muted-foreground">
                    <div className="mb-2 font-medium text-foreground">{t("aiAssistant.emptyTitle")}</div>
                    <div className="text-xs leading-5">{t("aiAssistant.emptyDescription")}</div>
                  </div>
                </div>
              )
            ) : (
              <div ref={messageListRef} className={cn("mx-auto w-full max-w-[760px] space-y-5", isFreezoneLayout && "max-w-none space-y-4", canvasOnlyProduct && "village-agent-message-list")}>
                {hiddenMessageCount > 0 && (
                  <button
                    type="button"
                    className="mx-auto flex min-h-8 items-center rounded-full border border-white/[0.08] bg-white/[0.035] px-3 text-[11px] text-muted-foreground transition-colors hover:border-white/[0.16] hover:bg-white/[0.07] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                    onClick={revealOlderMessages}
                    aria-label={`显示更早的 ${Math.min(MESSAGE_RENDER_BATCH_SIZE, hiddenMessageCount)} 条消息`}
                  >
                    显示更早的 {Math.min(MESSAGE_RENDER_BATCH_SIZE, hiddenMessageCount)} 条消息
                    <span className="ml-1 text-muted-foreground/55">· 还有 {hiddenMessageCount} 条</span>
                  </button>
                )}
                {renderedMessages.map((message) => (
                  <div
                    key={message.id}
                    data-message-id={message.id}
                    data-turn-id={message.role === "user" ? message.id : undefined}
                  >
                    <MessageBubble
                      message={message}
                      variant={variant}
                      onOpenDetail={setDetailMessage}
                      onOpenMedia={setMediaDetail}
                      pinned={chat.pinnedIds.has(message.id)}
                      onDelete={chat.deleteMessage}
                      onTogglePin={chat.togglePin}
                      deferStructuredRender={deferStructuredRender && isCurrentStreamingAssistantMessage(message)}
                      streaming={isStreamingAssistantMessage(message)}
                    />
                  </div>
                ))}
                {chat.streamText && !streamTextAlreadyRendered && (
                  <MessageBubble
                    message={{
                      id: "streaming",
                      role: "assistant",
                      text: chat.streamText,
                      timestamp: Date.now(),
                    }}
                    variant={variant}
                    onOpenDetail={setDetailMessage}
                    onOpenMedia={setMediaDetail}
                    pinned={false}
                    onDelete={() => undefined}
                    onTogglePin={() => undefined}
                    deferStructuredRender={deferStructuredRender}
                    streaming={chat.busy}
                  />
                )}
              </div>
            )}
          </div>
          {showScrollToBottom && (
            <Button
              type="button"
              size="icon"
              variant="secondary"
              className={cn(
                "absolute bottom-4 left-1/2 z-30 h-9 w-9 -translate-x-1/2 rounded-full border border-white/12 bg-background/88 text-foreground shadow-lg backdrop-blur transition hover:bg-background",
                isFreezoneLayout && "bottom-3",
              )}
              title="回到底部"
              aria-label="回到底部"
              onClick={() => scrollToChatBottom("auto")}
            >
              <ArrowDown className="h-4 w-4" />
            </Button>
          )}
          {!isFreezoneLayout && (
            <ChatTimeline messages={renderedMessages} scrollRef={scrollRef} />
          )}
        </div>

        <div className={cn("sticky bottom-0 z-40 shrink-0 bg-transparent p-3", isFreezoneLayout && "agent-composer-dock neo-agent-composer-dock px-3.5 pb-3.5 pt-2")}>
          <div
            className={cn(
              "relative mx-auto w-full max-w-[760px]",
              isFreezoneLayout
                ? showCompactExecutionStatus
                  ? "mb-2 min-h-8 max-w-none"
                  : "hidden"
                : "mb-2 h-7",
            )}
          >
            {isFreezoneLayout ? (
              <FreezoneAgentCompactStatus
                busy={compactExecutionBusy}
                connected={chat.connected}
                label={compactExecutionLabel}
                progressLabel={compactExecutionProgressLabel}
                stage={chat.progress?.stage}
                elapsedSeconds={chat.progress?.elapsedSeconds}
                lastProgressAgeSeconds={chat.progress?.lastProgressAgeSeconds}
                workerAlive={chat.progress?.workerAlive}
                timeline={agentExecutionTimeline}
                workflowRun={compactWorkflowRun}
                recovery={chat.recovery}
                onStop={chat.abort}
                onResumeRecovery={() => chat.resumeRecovery()}
                onDismiss={(failureKey) => {
                  const next = saveDismissedWorkflowFailureKey(failureKey);
                  setDismissedWorkflowFailureKeys(next);
                }}
                onRetryWorkflowItems={(groups) => { void retryWorkflowItems(groups); }}
                onRetryWorkflowStep={(stepId) => { void retryWorkflowStep(stepId); }}
                onDismissWorkflowItems={(groups) => { void dismissWorkflowItems(groups); }}
                onRepairWorkflowAssetBindings={() => { void workflowRecovery.repairWorkflowAssetBindings(); }}
                onAuthorizeCompose={() => { void workflowRecovery.authorizeCompose(); }}
                onAuthorizeMedia={(stepId, itemIds) => {
                  void workflowRecovery.authorizeMedia(stepId, itemIds);
                }}
                retryingWorkflowItems={retryingWorkflowItems}
                retryingWorkflowStepId={retryingWorkflowStepId}
                dismissingWorkflowItems={dismissingWorkflowItems}
                repairingWorkflowAssetBindings={workflowRecovery.repairingWorkflowAssetBindings}
                authorizingCompose={workflowRecovery.authorizingCompose}
                authorizingMediaStep={workflowRecovery.authorizingMediaStep}
                failureDismissed={activeWorkflowFailureDismissed}
              />
            ) : (
              <ComposerWaitingStatus
                label={t("aiAssistant.waitingResponse")}
                visible={showWaitingIndicator}
              />
            )}
          </div>
          <div
            ref={composerShellRef}
            className={cn(
              "relative mx-auto w-full max-w-[760px] overflow-hidden",
              !canvasOnlyProduct && "rounded-2xl border border-white/10 bg-white/[0.022] shadow-none backdrop-blur-xl",
              dragFileState === "valid" && "border-primary/70 bg-primary/5",
              dragFileState === "invalid" && "border-destructive/80 bg-destructive/10",
              isFreezoneLayout && "agent-composer-shell neo-agent-composer village-agent-composer-shell max-w-none",
              isFreezoneLayout && !canvasOnlyProduct && "rounded-[18px] border-white/[0.11] bg-[#151518]/92 shadow-[0_16px_46px_rgba(0,0,0,0.42),inset_0_1px_0_rgba(255,255,255,0.035)] backdrop-blur-2xl",
            )}
            onDragEnter={handleComposerDragEnter}
            onDragOver={handleComposerDragOver}
            onDragLeave={handleComposerDragLeave}
            onDrop={handleComposerDrop}
            onKeyDown={handleComposerKeyDown}
          >
            {isFreezoneLayout && slashMenuOpen && (
              <FreezoneSlashSkillMenu
                query={slashQuery}
                onPick={mountCanvasAgentSkillFromSlash}
                onClose={() => setSlashMenuOpen(false)}
                skills={availableCanvasAgentSkills}
              />
            )}
            {isFreezoneLayout && canvasOnlyProduct && skillDrawerOpen && (
              <FreezoneComposerSkillDrawer
                skillIds={selectedCanvasSkillIds}
                onToggleSkill={toggleCanvasAgentSkill}
                skills={availableCanvasAgentSkills}
                onOpenStore={() => {
                  setSkillDrawerOpen(false);
                  setSkillStoreOpen(true);
                }}
                disabled={chat.busy}
              />
            )}
            {ENABLE_SUPERCHAT_FILE_UPLOAD && (
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                accept=".txt,.md,.doc,.docx"
                onChange={(event) => addFiles(event.target.files)}
              />
            )}
            {(ENABLE_SUPERCHAT_FILE_UPLOAD || isFreezoneLayout) && dragFileState && (
              <div
                className={cn(
                  "pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-background/72 text-sm font-medium backdrop-blur-sm",
                  dragFileState === "invalid" ? "text-destructive" : "text-foreground",
                )}
              >
                {dragFileState === "invalid"
                  ? (isFreezoneLayout ? "只支持图片真引用（≤5MB）" : t("aiAssistant.unsupportedDropFiles"))
                  : (isFreezoneLayout ? "松开以加入真引用图片" : t("aiAssistant.dropFiles"))}
              </div>
            )}
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5 px-4 pt-3">
                {attachments.map((attachment) => (
                  <span
                    key={attachment.id}
                    className="inline-flex max-w-48 items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1 text-xs"
                  >
                    {attachment.mimeType?.startsWith("image/") ? <Image className="size-3.5" /> : <File className="size-3.5" />}
                    <span className="truncate">{attachment.fileName}</span>
                    <button
                      type="button"
                      onClick={() => setAttachments((current) => current.filter((item) => item.id !== attachment.id))}
                      className="text-muted-foreground hover:text-foreground"
                      aria-label={t("aiAssistant.removeAttachment")}
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            {queuedMessages.length > 0 && (
              <div
                className={cn(
                  "border-t border-white/[0.05] px-4 py-2",
                  canvasOnlyProduct && "village-agent-direction-queue",
                )}
              >
                <div className="mb-1.5 flex items-center justify-between gap-3 text-xs font-normal text-foreground/40">
                  <span>
                    {canvasOnlyProduct && chat.busy
                      ? `调整方向 ${queuedMessages.length} 条`
                      : t("aiAssistant.queuedCount", { count: queuedMessages.length })}
                  </span>
                  {canvasOnlyProduct && chat.busy && (
                    <span className="text-[10px] text-white/28">当前任务继续 · 完成后立即承接</span>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {queuedMessages.map((message) => {
                    const showSelectedState = queuedMessages.length > 1 && selectedQueuedMessageId === message.id;
                    return (
                      <div
                        key={message.id}
                        className={cn(
                          "inline-flex max-w-full items-center overflow-hidden rounded-[6px] border border-white/[0.08] bg-white/[0.035] text-xs text-foreground/70 transition-colors hover:bg-white/[0.055] focus-within:border-white/[0.18]",
                          showSelectedState && "border-primary/35 bg-primary/[0.07] text-foreground/90 focus-within:border-primary/45",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => setSelectedQueuedMessageId(message.id)}
                          className="flex min-w-0 items-center gap-1.5 px-2 py-1 text-left focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/25"
                          aria-label={t("aiAssistant.selectQueuedMessage")}
                          aria-pressed={showSelectedState}
                        >
                          <span className="max-w-56 truncate">{message.displayText}</span>
                          {message.attachments.length > 0 && (
                            <span className="shrink-0 text-foreground/45">
                              {t("aiAssistant.queuedAttachments", { count: message.attachments.length })}
                            </span>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setQueuedMessages((current) => current.filter((item) => item.id !== message.id));
                          }}
                          className="mr-0.5 flex size-5 shrink-0 items-center justify-center rounded-[4px] text-foreground/35 transition-colors hover:bg-white/[0.06] hover:text-foreground/75 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/25"
                          aria-label={t("aiAssistant.removeQueuedMessage")}
                        >
                          <X className="size-3" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            <div className={cn(isFreezoneLayout && "village-agent-composer-input-flow")}>
              {skillReadinessMessage && <p role="status" className="px-2 py-1 text-xs text-muted-foreground">{skillReadinessMessage}</p>}
              {isFreezoneLayout && (
                <FreezoneComposerTags
                  skillIds={selectedCanvasSkillIds}
                  pinnedNodes={pinnedCanvasNodes}
                  onRemoveSkill={toggleCanvasAgentSkill}
                  onUnpinNode={(nodeId) => {
                    updatePinnedCanvasNodeIds((current) => current.filter((id) => id !== nodeId));
                  }}
                  availableSkills={availableCanvasAgentSkills}
                />
              )}
              <Textarea
              ref={draftInputRef}
              value={draft}
              onChange={(event) => {
                setSelectedHistoryMessageIndex(null);
                const next = event.target.value;
                setDraft(next);
                if (isFreezoneLayout) {
                  const nextSlashMenuOpen = /(?:^|\s)\/[^\s]*$/.test(next);
                  setSlashMenuOpen(nextSlashMenuOpen);
                  if (nextSlashMenuOpen) setSkillDrawerOpen(false);
                }
              }}
              onPaste={(event) => {
                if (!isFreezoneLayout) return;
                const items = Array.from(event.clipboardData?.items ?? []);
                const imageItems = items.filter((item) => item.type.startsWith("image/"));
                if (imageItems.length === 0) return;
                event.preventDefault();
                void (async () => {
                  const next: ChatAttachment[] = [];
                  for (const item of imageItems) {
                    const file = item.getAsFile();
                    if (!file) continue;
                    const attachment = await fileToImageAttachment(file);
                    if (attachment) next.push(attachment);
                  }
                  if (next.length === 0) {
                    toast.error("粘贴的图片过大或格式不支持（单张 ≤5MB）");
                    return;
                  }
                  setAttachments((current) => mergeReferenceAttachments(current, next));
                  toast.success(`已加入 ${next.length} 张真引用图片`);
                })();
              }}
              onFocus={() => setComposerInputFocused(true)}
              onBlur={() => {
                setComposerInputFocused(false);
                // Delay so slash menu click can register.
                window.setTimeout(() => setSlashMenuOpen(false), 120);
              }}
              onKeyDown={(event) => {
                if (isFreezoneLayout && event.key === "Escape" && skillDrawerOpen) {
                  event.preventDefault();
                  setSkillDrawerOpen(false);
                  return;
                }
                if (isFreezoneLayout && event.key === "Escape" && slashMenuOpen) {
                  event.preventDefault();
                  setSlashMenuOpen(false);
                  return;
                }
                if (
                  queuedMessages.length > 0
                  && draft.trim().length === 0
                  && (event.key === "ArrowUp" || event.key === "ArrowDown")
                ) {
                  event.preventDefault();
                  selectQueuedMessageByOffset(event.key === "ArrowUp" ? -1 : 1);
                  return;
                }
                if (
                  event.key === "ArrowUp"
                  && queuedMessages.length === 0
                  && (draft.trim().length === 0 || selectedHistoryMessageIndex !== null)
                ) {
                  event.preventDefault();
                  selectHistoryMessage("older");
                  return;
                }
                if (
                  event.key === "ArrowDown"
                  && queuedMessages.length === 0
                  && selectedHistoryMessageIndex !== null
                ) {
                  event.preventDefault();
                  selectHistoryMessage("newer");
                  return;
                }
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              dir="auto"
              placeholder={isFreezoneLayout
                ? chat.busy && canvasOnlyProduct
                  ? "输入调整方向，不会暂停当前任务…"
                  : LIBTV_CHAT_RICH_INPUT_PLACEHOLDER
                : t("aiAssistant.placeholder")}
              className={cn(
                "max-h-[220px] min-h-14 resize-none border-0 bg-transparent px-5 py-4 text-base shadow-none placeholder:text-muted-foreground/70 focus-visible:ring-0 dark:bg-transparent",
                isFreezoneLayout && "neo-agent-textarea min-h-[42px] min-w-[10rem] flex-1 basis-[12rem] px-0 py-1.5 text-[13px] leading-[1.65] text-white/90 placeholder:text-white/28",
              )}
              rows={1}
              />
            </div>
             <div className="flex items-center justify-between px-3 py-2">
               <div className="flex min-w-0 items-center gap-1">
                 {ENABLE_SUPERCHAT_FILE_UPLOAD && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    disabled={!chat.connected}
                    onClick={() => fileInputRef.current?.click()}
                    aria-label={t("aiAssistant.attach")}
                    title={t("aiAssistant.attach")}
                  >
                    <Plus className="size-4" />
                     </Button>
                   )}
                 {canvasOnlyProduct && (
                   <div
                     className="village-agent-control-strip flex min-w-0 flex-1 flex-nowrap items-center gap-1.5"
                     data-agent-composer-layout="libtv"
                   >
                     <button
                       type="button"
                       className={cn(
                         "village-agent-skill-drawer-button relative inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.045] px-2.5 text-[12px] font-semibold text-white/72 transition hover:border-white/[0.18] hover:bg-white/[0.075] hover:text-white",
                         selectedCanvasSkillIds.length > 0 && [
                           "border-amber-200/45",
                           "bg-amber-200/[0.14]",
                           "text-amber-50",
                           "shadow-[0_0_16px_rgba(251,191,36,0.12)]",
                           "ring-1",
                           "ring-amber-200/20",
                         ],
                       )}
                       onPointerDown={(event) => {
                         event.preventDefault();
                         event.stopPropagation();
                         setSkillDrawerOpen((open) => !open);
                         setSlashMenuOpen(false);
                       }}
                       onClick={(event) => event.preventDefault()}
                       onKeyDown={(event) => {
                         if (event.key !== "Enter" && event.key !== " ") return;
                         event.preventDefault();
                         setSkillDrawerOpen((open) => !open);
                         setSlashMenuOpen(false);
                       }}
                       aria-expanded={skillDrawerOpen}
                       aria-pressed={selectedCanvasSkillIds.length > 0}
                       aria-label={selectedCanvasSkillIds.length > 0
                         ? `打开技能栏，已选 ${selectedCanvasSkillIds.length} 个`
                         : "打开技能栏"}
                       title={selectedCanvasSkillIds.length > 0
                         ? `已选 ${selectedCanvasSkillIds.length} 个 Skill`
                         : "技能"}
                       data-selected={selectedCanvasSkillIds.length > 0 ? "true" : "false"}
                     >
                       <Star className="size-4" strokeWidth={2.15} />
                       <span>技能</span>
                      {selectedCanvasSkillIds.length > 0 && (
                         <span className="village-agent-skill-count">{selectedCanvasSkillIds.length}</span>
                       )}
                      </button>
                      <button
                        type="button"
                        onClick={toggleResearch}
                        disabled={chat.busy}
                        aria-pressed={researchEnabled}
                        data-selected={researchEnabled ? "true" : "false"}
                        aria-label={researchEnabled ? "关闭联网研究" : "开启联网研究"}
                        title={researchEnabled
                          ? "联网研究已开启：按需检索 AIGC 资料并沉淀到当前项目"
                          : "开启后，小树可按需检索 AIGC 资料并沉淀到当前项目"}
                        className={cn(
                          "village-agent-research-toggle inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[12px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-45",
                          researchEnabled
                            ? "border-cyan-200/70 bg-cyan-300/[0.22] text-cyan-50 shadow-[0_0_18px_rgba(34,211,238,0.16)] ring-1 ring-cyan-200/20"
                            : "border-white/[0.08] bg-white/[0.045] text-white/55 hover:border-white/[0.18] hover:bg-white/[0.075] hover:text-white/86",
                        )}
                      >
                        {researchEnabled ? (
                          <Check className="size-3.5" strokeWidth={2.5} />
                        ) : (
                          <Search className="size-3.5" strokeWidth={2.15} />
                        )}
                       <span>{researchEnabled ? "联网" : "联网研究"}</span>
                      </button>
                  <div
                        className="village-agent-model-picker flex min-w-0 items-center gap-1.5"
                        data-configured={chat.activeModel ? "true" : "false"}
                        title={
                          chat.activeModelReady
                            ? ""
                            : chat.activeModelBlockedReason
                              ?? (chat.models.length === 0 ? "请先配置小树直连模型" : undefined)
                        }
                      >
                      {chat.models.length > 0 ? (
                       <VillageAgentSelect
                         value={chat.activeModel ?? ""}
                         onValueChange={chat.switchModel}
                         disabled={chat.modelsLoading || chat.busy || !chat.connected}
                            ariaLabel="小树模型"
                            title="选择小树模型"
                         testId="composer-model"
                         options={chat.models.map((model) => ({
                           value: model.id,
                           label: (model.label || model.id).replace(/\s*·\s*深度\s*$/, ""),
                            description: `${model.reasoning ? "深度 · " : ""}${model.description || "小树直连模型"}`,
                            disabled: model.stale === true || model.disabled === true || model.enabled === false,
                         }))}
                         triggerClassName="w-full justify-between text-white/68 hover:bg-white/[0.055] hover:text-white/92"
                       />
                     ) : (
                       <span className="village-agent-model-picker__empty whitespace-nowrap px-2 text-[12px] text-white/35">
                          请先配置小树直连模型
                       </span>
                     )}
                     </div>
                     <label
                       className="village-agent-run-mode-picker inline-flex min-w-0 items-center gap-1.5"
                       data-run-mode={canvasAgentRunMode}
                     >
                       <span className="sr-only">小树运行模式</span>
                       <VillageAgentSelect
                         value={canvasAgentRunMode}
                         onValueChange={(value) => switchCanvasAgentRunMode(value as CanvasAgentRunMode)}
                         disabled={chat.busy}
                         ariaLabel="小树运行模式"
                         title={`${canvasAgentRunModeLabel(canvasAgentRunMode)}：${CANVAS_AGENT_RUN_MODE_OPTIONS.find((option) => option.id === canvasAgentRunMode)?.description ?? ""}`}
                         testId="composer-run-mode"
                         options={CANVAS_AGENT_RUN_MODE_OPTIONS.map((option) => ({
                           value: option.id,
                           label: option.label,
                           description: option.description,
                         }))}
                         triggerClassName="w-full justify-between font-semibold text-white/72 hover:bg-white/[0.055] hover:text-white/92"
                       />
                     </label>
                   </div>
                 )}
               </div>
               <div className="village-agent-primary-actions flex shrink-0 items-center gap-1.5">
                 {canvasOnlyProduct && (
                   <AgentContextRing usage={agentContextUsage} />
                 )}
                 {recording && (
                  <div className="mr-1 flex items-center gap-1.5 text-sm text-primary">
                    <span className="size-2 animate-pulse rounded-full bg-primary" />
                    <span>{t("aiAssistant.listening")}</span>
                  </div>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("neo-agent-voice-action size-8 rounded-full text-white/85 hover:bg-white/[0.08] hover:text-white", recording && "text-primary")}
                  disabled={!chat.connected}
                  onClick={toggleSpeech}
                  aria-label={recording ? t("aiAssistant.stopVoice") : t("aiAssistant.voiceInput")}
                  title={recording ? t("aiAssistant.stopVoice") : t("aiAssistant.voiceInput")}
                >
                  {recording ? <MicOff className="size-4.5" /> : <Mic className="size-4.5" />}
                </Button>
                {composerPrimaryAction === "steer" && (
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="neo-agent-stop-action size-8 rounded-full bg-white/[0.055] text-white/58 shadow-none hover:bg-white/[0.10] hover:text-white"
                    onClick={chat.abort}
                    aria-label="停止当前任务"
                    title="停止当前任务"
                  >
                    <span className="size-2.5 rounded-[2.5px] bg-current" aria-hidden />
                  </Button>
                )}
                <Button
                  type="button"
                  size="icon"
                  className={cn(
                    "neo-agent-send-action size-8 rounded-full shadow-none disabled:bg-white/30 disabled:text-black/45",
                    composerPrimaryAction === "steer"
                      ? "bg-white text-black hover:bg-white/90"
                      : composerPrimaryAction === "stop"
                        ? "bg-white/10 text-white hover:bg-white/15"
                      : "bg-white text-black hover:bg-white/90",
                  )}
                  disabled={agentComposerPrimaryActionDisabled(composerPrimaryAction, canSend)}
                  onClick={composerPrimaryAction === "stop" ? chat.abort : submit}
                  aria-label={composerPrimaryAction === "steer" ? "发送调整方向" : composerPrimaryAction === "stop" ? t("aiAssistant.stop") : t("aiAssistant.send")}
                  title={composerPrimaryAction === "steer" ? "发送调整方向（当前任务继续）" : composerPrimaryAction === "stop" ? t("aiAssistant.stop") : t("aiAssistant.send")}
                >
                  {composerPrimaryAction === "stop" ? (
                    <span className="size-2.5 rounded-[2.5px] bg-current" aria-hidden />
                  ) : (
                    <ArrowUp className="size-[18px]" />
                  )}
                </Button>
              </div>
            </div>
          </div>
          {!isFreezoneLayout && (
            <p className="mx-auto mt-[13px] w-full max-w-[680px] text-center text-[11px] leading-4 text-white/25">
              {t("aiAssistant.disclaimer")}
            </p>
          )}
        </div>
      </section>
      <MessageDetailPanel
        message={detailMessage}
        onClose={() => setDetailMessage(null)}
        onOpenMedia={setMediaDetail}
      />
      <SpecMediaDetailModal
        detail={mediaDetail}
        onClose={() => setMediaDetail(null)}
        onOpenMedia={setMediaDetail}
      />
      <FormatCheckDetailsDialog
        formatCheck={formatCheckDetails?.formatCheck ?? null}
        filename={formatCheckDetails?.filename}
        open={Boolean(formatCheckDetails)}
        onOpenChange={(next) => {
          if (!next) setFormatCheckDetails(null);
        }}
      />
      {isFreezoneLayout && (
        <>
          <SkillStoreDialog
            open={skillStoreOpen}
            onOpenChange={setSkillStoreOpen}
            mountedSkillIds={selectedCanvasSkillIds}
            onToggleMount={toggleCanvasAgentSkill}
            onCatalogChange={setSkillStoreCatalog}
          />
          <AgentMemoryDialog
            open={agentMemoryOpen}
            onOpenChange={setAgentMemoryOpen}
            project={freezoneProjectId || params.project}
          />
          <AgentCliSkillDialog
            open={agentCliSkillOpen}
            onOpenChange={setAgentCliSkillOpen}
            projectId={freezoneProjectId || params.project}
            canvasId={freezoneCanvasId}
            mountedSkillCount={selectedCanvasSkillIds.length}
            totalSkillCount={availableCanvasAgentSkills.length}
            autoMatching={selectedCanvasSkillIds.length === 0}
            onOpenSkillDrawer={() => {
              setAgentCliSkillOpen(false);
              setSkillDrawerOpen(true);
            }}
            onOpenSkillStore={() => {
              setAgentCliSkillOpen(false);
              setSkillStoreOpen(true);
            }}
          />
          <SettingsDialog open={agentSettingsOpen} onOpenChange={setAgentSettingsOpen} />
        </>
      )}
      {!isFreezoneLayout && (
        <img
          src="/images/bg-chat-buttom.png"
          alt=""
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-0 z-0 w-full max-w-none select-none"
        />
      )}
    </div>
  );
}
