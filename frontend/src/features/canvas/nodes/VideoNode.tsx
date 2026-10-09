// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from "@xyflow/react";
import {
  AlertTriangle,
  ArrowUp,
  ChevronDown,
  Download,
  Languages,
  Loader2,
  Play,
  WandSparkles,
  Upload as UploadIcon,
  Video as VideoIcon,
  X as XIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  CANVAS_NODE_TYPES,
  type VideoGenCount,
  type VideoGenMode,
  type VideoNodeData,
} from "@/features/canvas/domain/canvasNodes";
import {
  resolveImageDisplayUrl,
  snapToAllowedAspectRatio,
} from "@/features/canvas/application/imageData";
import {
  getNodeMediaCurrentTime,
  isNodeMediaActive,
  setNodeMediaActive,
} from "@/features/canvas/application/canvasLod";
import {
  normalizeAspectRatioForLayout,
  resolveResizeMinConstraintsByAspect,
} from "@/features/canvas/application/imageNodeSizing";
import { ensureWebSafeVideo } from "@/features/canvas/application/videoTranscode";
import { isVideoFile, VIDEO_FILE_ACCEPT } from "@/features/canvas/application/videoFileTypes";
import { spawnExternalAssetNodes } from "@/features/canvas/application/spawnExternalAssets";
import { resolveNodeDisplayName } from "@/features/canvas/domain/nodeDisplay";
import { toast } from "sonner";
import { downloadUrlAsFile } from "@/lib/browserDownload";
import { videoFrameCaptureMetadata } from '@/features/canvas/application/extractedFrames';
import { useAlbumPendingTotal } from "@/features/canvas/nodes/shared/albumPendingTotals";
import { canvasEventBus } from "@/features/canvas/application/canvasServices";
import { ReferenceTextChip } from "@/features/canvas/nodes/shared/ReferenceTextChip";
import {
  referenceInsertText,
} from "@/features/canvas/nodes/shared/referenceStripBadges";
import { CanvasReferencePickChip } from "@/features/canvas/nodes/shared/CanvasReferencePickChip";
import { useNodeGenerationTaskState } from "@/features/canvas/application/useNodeGenerationTaskState";
import { resolveErrorContent } from "@/features/canvas/application/errorDialog";
import { backendErrorToastMessage } from "@/lib/api-errors";
import {
  extractGenerationErrorCode,
  extractProviderTaskId,
  resolveGenerationErrorDiagnostics,
} from "@/features/canvas/application/generationErrorReport";
import {
  PromptMentionEditor,
  type PromptMentionEditorHandle,
} from "@/features/canvas/nodes/PromptMentionEditor";
import { NodeContextPromptPaletteButton } from "@/features/canvas/nodes/ContextPromptPaletteButton";
import {
  contextPromptPaletteInsertionText,
  type ContextPromptPaletteEntry,
} from "@/features/canvas/nodes/contextPromptPalette";
import {
  NodeHeader,
  NODE_HEADER_FLOATING_POSITION_CLASS,
} from "@/features/canvas/ui/NodeHeader";
import { NodeResizeHandle } from "@/features/canvas/ui/NodeResizeHandle";
import { PanelExpandButton } from "@/features/canvas/ui/PanelExpandButton";
import {
  NODE_OPS_PANEL_ENTER_CLASS,
  NodePanelZoomAnchor,
  OperationPanelShell,
} from "@/features/canvas/ui/OperationPanelShell";
import { NodeGenerationOverlay } from "@/features/canvas/ui/NodeGenerationOverlay";
import { NodeGenerationErrorCard } from "@/features/canvas/ui/NodeGenerationErrorCard";
import type { VideoFrameCaptureMode } from "@/features/canvas/ui/VideoFrameCaptureMenu";
import {
  CANVAS_NODE_INPUT_BODY_FRAME_CLASS,
  CANVAS_NODE_INPUT_PLACEHOLDER_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  CANVAS_NODE_OPS_PANEL_CLASS,
  CANVAS_NODE_PANEL_SURFACE_CLASS,
  canvasNodeFrameClass,
} from "@/features/canvas/ui/nodeFrameStyles";
import {
  hasMainlineContexts,
  NodeContextBadges,
} from "@/features/freezone/context/NodeContextBadges";
import {
  NODE_CREDIT_PILL_FLAT_CLASS,
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
  NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS,
  NODE_INLINE_ICON_BUTTON_CLASS,
  NODE_TEXT_CONTROL_ICON_CLASS,
  NODE_TEXT_CONTROL_TRIGGER_CLASS,
} from "@/features/canvas/ui/nodeControlStyles";
import { VideoClipPanel } from "@/features/canvas/nodes/VideoClipPanel";
import {
  CAMERA_MOVEMENT_PRESETS,
  findCameraMovementPreset,
  type CameraMovementPreset,
} from "@/features/canvas/domain/cameraMovementPresets";
import {
  cameraDirectionText,
  resetCameraDirection,
  resolveCameraPresetForText,
  setCameraDirection,
} from "@/features/canvas/domain/promptCamera";
import { SCRIPT_SHOT_VIDEO_PROMPT_FIELD } from "@/features/canvas/nodes/script/scriptShotVideos";
import { useFreezoneVideoCameraTemplates } from "@/features/canvas/hooks/useFreezoneVideoCameraTemplates";
import { useFreezoneVideoModels } from "@/features/canvas/hooks/useFreezoneVideoModels";
import {
  AssetLibraryModal,
  type AssetLibrarySelection,
} from "@/features/canvas/ui/AssetLibraryModal";
import { useCanvasStore, useIsBoxSelecting } from "@/stores/canvasStore";
import {
  fetchFreezoneJobResult,
  fetchFreezonePromptOptimizeResult,
  recoverFreezoneVideoJob,
  fetchFreezoneTextTranslateResult,
  submitFreezonePromptOptimize,
  submitFreezoneTextTranslate,
  submitFreezoneVideoCompose,
  submitFreezoneVideoErase,
  uploadFreezoneImage,
  uploadFreezoneVideo,
  type FreezonePromptOptimizePayload,
  type FreezonePromptOptimizeResult,
  type FreezoneVideoAspectRatio,
} from "@/api/ops";
import { awaitTaskCompletion } from "@/api/tasks";
import {
  awaitFreezoneJobMediaResult,
  resolveFreezoneTaskPreviewUrl,
} from "@/features/canvas/application/awaitFreezoneJobMediaResult";
import { videoGenerationSourcePatch } from '@/features/canvas/application/videoGenerationSource';
import {
  registerNodeGenerationTask,
  useCancelNodeGeneration,
} from "@/features/canvas/application/useCancelNodeGeneration";
import {
  buildGenerationTerminalPatch,
  buildVideoMetadataPatch,
  CLEARED_GENERATION_ERROR_PATCH,
} from "@/features/canvas/application/generationTaskArbitration";
import { useNodeGenerationHistory } from "@/features/canvas/hooks/useNodeGenerationHistory";
import { useVideoModeReconciliation } from "@/features/canvas/hooks/useVideoModeReconciliation";
import { useVideoGenerationSubmission } from "./useVideoGenerationSubmission";
import { useVideoNodePreview } from "@/features/canvas/hooks/useVideoNodePreview";
import {
  estimateVideoDialogueTiming,
  resolveVideoDialogueText,
} from "./videoDialogueTiming";
import { clampGenerationBatchCount } from "@/features/canvas/application/generationConcurrency";
import {
  SubtitleEraseBoxOverlay,
  SubtitleEraseOpsPanel,
  VideoPlayerControls,
} from "./VideoPlaybackOverlays";
import {
  CameraMovementChip,
  CharacterLibraryChip,
  CountPicker,
  ExternalAssetChip,
  GenModeSelect,
  ReferenceMediaRow,
  VideoAdvancedParametersChip,
  VideoConfigChip,
} from "./VideoNodeControls";
import {
  DEFAULT_DURATION_MIN,
  REFERENCE_CAPS_BY_MODE,
  RECOVERABLE_VIDEO_QUERY_ERROR_CODES,
  advertisedVideoQualityOptionsForModel,
  captureVideoFrameBlob,
  clampVideoDuration,
  defaultSceneOptimizeForModel,
  modelParameterDefaults,
  normalizeSceneOptimize,
  normalizeVideoQuality,
  qualityToResolution,
  resolutionToQuality,
  resolveDroppedVideoFile,
  sceneOptimizeOptionsForModel,
  videoAspectRatioOptionsForModel,
  videoDurationBoundsForModel,
  videoDurationOptionsForModel,
  videoDurationParameterEnabledForModel,
  videoQualityOptionsForModel,
  rejectedVideoQualityOptionsForModel,
  isValidVideoAspectRatio,
} from "./videoNodeModelRules";
import {
  useVideoReferences,
} from "@/features/canvas/hooks/useVideoReferences";
import {
  NodeGenerationHistory,
  hasCompletedHistoryRecords,
  historyRecordOutputUrl,
} from "@/features/canvas/ui/NodeGenerationHistory";
import type { FreezoneGenerationHistoryRecord } from "@/api/ops";
import { readUrl } from "@/lib/url-params";
import {
  DEFAULT_VIDEO_MODEL_ID,
  ProviderModelPicker,
} from "@/features/canvas/ui/ProviderModelPicker";
import { ResolutionHonestyBadge } from "@/features/canvas/ui/ResolutionHonestyBadge";
import { NodeRightsBadge } from "@/features/canvas/ui/NodeRightsBadge";
import { writeLastVideoModel } from "@/features/canvas/domain/lastVideoModel";
import { buildGeneratedRightsPatch } from "@/features/canvas/domain/nodeRights";
import { parseResourceMeta } from "@/features/canvas/domain/canvasResourceMeta";
import { evaluateDurationHonesty } from "@/features/canvas/domain/durationHonesty";
import { areVideoNodePropsEqual, videoNodePreload } from "./videoNodeRenderProps";
import {
  canSubmitVideoGeneration,
  compileVideoModelFamily,
  isSeedance2VideoFamily,
  resolveVideoModelFamily,
  resolveVideoModeForCapability,
  resolveVideoNodeAudioSwitch,
  videoReferenceDisabledReason,
  videoSubmitMediaRejectionReason,
  videoPromptContractIssue,
  videoSubmitDisabledReason,
  videoModesForCapability,
} from "@/features/canvas/domain/videoCapabilityCompiler";
import {
  findPersistedModel,
  selectVideoModel,
} from "@/features/canvas/domain/videoModelSelection";
import {
  resolveDirectCanvasModelId,
  useDirectModelCatalog,
} from "@/features/canvas/hooks/useDirectModelCatalog";
import {
  CreditCostPill,
  formatCreditCost,
} from "@/components/credits/credit-visual";
import { useGenerationCreditCost } from "@/lib/queries/generation-credit-cost";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import {
  PromptOptimizationPreview,
  type PromptOptimizationResearchMode,
} from "@/features/freezone/PromptOptimizationPreview";
import { buildVideoPromptOptimizationReferences } from "@/features/freezone/promptOptimizationPayload";
import {
  promptOptimizationProgressLabel,
  usePromptOptimizationElapsed,
} from "@/features/freezone/usePromptOptimizationElapsed";

type VideoNodeProps = NodeProps & {
  id: string;
  data: VideoNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 580;
const DEFAULT_HEIGHT = 380;
const MIN_WIDTH = 480;
const MIN_HEIGHT = 280;
const MAX_WIDTH = 1100;
const MAX_HEIGHT = 1000;

// 图片节点的默认落位尺寸（与 ImageGenNode 的 DEFAULT_WIDTH/HEIGHT 对齐）。
// 空态参考 CTA 会在视频节点左侧新建图片节点，排版要按它的真实尺寸算。

const OPERATIONS_PANEL_HEIGHT = 336;
const OPERATIONS_PANEL_GAP = 12;
// Extend the ops panel beyond the node's left/right edges so the textarea +
// chips have more room than the video frame itself.
const OPERATIONS_PANEL_OVERHANG = 120;
// 「放大」后用居中弹窗展示，给提示词编辑更舒适的空间。
const OPERATIONS_PANEL_EXPANDED_HEIGHT = 560;
const OPERATIONS_PANEL_EXPANDED_WIDTH = 1040;


// 各 genMode 对上游引用数量的硬上限。UI 用这张表把后端字段约束（多图 / 多模态
// 场景下）显式表达出来：超额 chip 标灰 + 从 @ 候选剔除，避免「prompt 引用了
// @图片10 但提交时被静默丢掉」。
//
// 表里没出现的模式默认不限制（textToVideo 不消费上游、imageToVideo 走
// 原有路径）。allReference 的真实上限优先来自选中模型的能力合同；这张表只
// 是旧模型和未探测模型的兼容包络。每条音频另按 1.8~15.2s 校验；探测不到
// 时长的交给服务端兜底。
//   - firstLastFrame       ：仅图片 2 张（首帧 + 尾帧），不允许任何视频 / 音频。
//                            图片 >2 时另有自动切到 allReference 的兜底（见
//                            VideoNode 内部 effect）。
export const VideoNode = memo(
  ({ id, data, selected, width, height }: VideoNodeProps) => {
    const { t } = useTranslation();
    const updateNodeInternals = useUpdateNodeInternals();
    const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
    const isBoxSelecting = useIsBoxSelecting();
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);
    const addDerivedUploadNode = useCanvasStore(
      (state) => state.addDerivedUploadNode,
    );
    const addNode = useCanvasStore((state) => state.addNode);
    const addEdge = useCanvasStore((state) => state.addEdge);
    const setActiveOverlayNodeId = useCanvasStore(
      (state) => state.setActiveOverlayNodeId,
    );
    const inputRef = useRef<HTMLInputElement>(null);
    // 与 inputRef 分开：前者替换本节点视频；这里多选并生成图片/视频/音频上游素材。
    const externalAssetInputRef = useRef<HTMLInputElement>(null);
    // 在途守卫：持到本批所有并发任务 allSettled 才释放（见 handleSubmit）。
    const submittingRef = useRef(false);
    /** 上游还没出完时用它兜底重查闸门（正常路径靠上游出结果后的重渲染）。 */
    const [autoSubmitRetryTick, setAutoSubmitRetryTick] = useState(0);
    const generationQueueAbortRef = useRef<AbortController | null>(null);
    const cancelLocalGeneration = useCallback((reason: Error) => {
      generationQueueAbortRef.current?.abort(reason);
    }, []);
    const {
      cancel: cancelNodeTask,
      isCancelling: isCancellingNodeTask,
    } = useCancelNodeGeneration(id, data, cancelLocalGeneration);
    useEffect(() => () => {
      generationQueueAbortRef.current?.abort();
      generationQueueAbortRef.current = null;
    }, []);
    // Mirror the actual <video> element into state so VideoPlayerControls 能
    // 在挂载/卸载时重新订阅事件（仅 ref 不会触发重渲染）。同时保留可写的
    // ref，给非 React 路径（capture frame 之类）继续用 .current。
    const videoRef = useRef<HTMLVideoElement | null>(null);
    const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null);
    const setVideoRef = useCallback((el: HTMLVideoElement | null) => {
      // Callback refs may be invoked during commit even when the actual
      // element did not change. Avoid mirroring identical values into React
      // state; otherwise a canvas video remount can trip React #185.
      if (videoRef.current === el) return;
      videoRef.current = el;
      setVideoEl(el);
    }, []);
    const handlePlaybackIntent = useCallback(
      (playing: boolean) => {
        setNodeMediaActive(id, playing, videoRef.current?.currentTime);
      },
      [id],
    );
    useEffect(() => {
      if (!videoEl) {
        return;
      }
      const resumePlayback = isNodeMediaActive(id);
      const savedCurrentTime = getNodeMediaCurrentTime(id) ?? 0;
      const sync = () => {
        const active = !videoEl.paused && !videoEl.ended;
        setNodeMediaActive(id, active, videoEl.currentTime);
      };
      const resume = () => {
        if (!resumePlayback) {
          sync();
          return;
        }
        if (savedCurrentTime > 0 && Number.isFinite(videoEl.duration)) {
          videoEl.currentTime = Math.min(
            savedCurrentTime,
            Math.max(0, videoEl.duration - 0.01),
          );
        }
        void videoEl.play().then(sync).catch(() => setNodeMediaActive(id, false));
      };
      if (resumePlayback && videoEl.readyState < 1) {
        videoEl.addEventListener("loadedmetadata", resume, { once: true });
      } else {
        resume();
      }
      videoEl.addEventListener("play", sync);
      videoEl.addEventListener("ended", sync);
      return () => {
        videoEl.removeEventListener("loadedmetadata", resume);
        videoEl.removeEventListener("play", sync);
        videoEl.removeEventListener("ended", sync);
        // React Flow may recycle a visible node while changing LOD. Preserve
        // the active playhead so the next mount can restore the user's intent.
        if (!videoEl.paused && !videoEl.ended) {
          setNodeMediaActive(id, true, videoEl.currentTime);
        }
      };
    }, [id, videoEl]);
    const transientUrlRef = useRef<string | null>(null);
    const [transientPreviewUrl, setTransientPreviewUrl] = useState<
      string | null
    >(null);
    const [isCapturingFrame, setIsCapturingFrame] = useState(false);
    const [isTranslatingPrompt, setIsTranslatingPrompt] = useState(false);
    const [isOptimizingPrompt, setIsOptimizingPrompt] = useState(false);
    const promptOptimizationElapsed = usePromptOptimizationElapsed(isOptimizingPrompt);
    const [promptOptimization, setPromptOptimization] = useState<{
      originalPrompt: string;
      request: FreezonePromptOptimizePayload;
      result: FreezonePromptOptimizeResult;
      jobId: string;
      taskKey: string;
      contextSignature: string;
    } | null>(null);
    const [promptOptimizationRerunningMode, setPromptOptimizationRerunningMode] =
      useState<PromptOptimizationResearchMode | null>(null);
    const [isCharacterLibraryOpen, setIsCharacterLibraryOpen] = useState(false);
    const [isComposingClip, setIsComposingClip] = useState(false);
    const [clipError, setClipError] = useState<string | null>(null);
    const [isRecovering, setIsRecovering] = useState(false);

    // 每节点生成历史：仅在节点被选中时拉取，避免画布上每个视频节点都各发一次
    // 请求。生成完成后调用 refreshHistory 把新记录拉进来。
    const {
      records: historyRecords,
      isLoading: historyLoading,
      refresh: refreshHistory,
    } = useNodeGenerationHistory(id, { enabled: Boolean(selected) });

    // 生成进行中时，点击历史记录走「非破坏性预览」：不覆写 videoUrl、不打断在途
    // 任务，仅把这条历史视频临时显示在主体上（见 isGenerating 渲染分支）。新视频
    // 生成完成后由下方 effect 自动清空，回到最新结果。非生成态恢复历史时也清掉它。
    const [historyPreviewUrl, setHistoryPreviewUrl] = useState<string | null>(
      null,
    );

    const prompt = typeof data.prompt === "string" ? data.prompt : "";
    // Local draft + composition guard so IME (中文输入法) candidates stop being
    // wiped by the store-driven re-render. Same fix pattern as
    // `docs/changes/2026-05-12-image-gen-ime-fix.md`.
    const [promptDraft, setPromptDraft] = useState(prompt);
    const isComposingRef = useRef(false);
    const promptEditorRef = useRef<PromptMentionEditorHandle | null>(null);
    useEffect(() => {
      if (isComposingRef.current) return;
      setPromptDraft(prompt);
    }, [prompt]);

    // 「上下文调色盘」：与图生节点同款，把镜头里人物/道具的标记颜色快速插进提示词。
    // palette 的全量 nodes/edges 订阅下沉到 NodeContextPromptPaletteButton，避免本节点
    // 为它订阅整图、被任意节点拖动牵连重渲染。插入直接走编辑器命令式 API：弹层与编辑器
    // 同在面板里、编辑器恒已挂载，故回调无需依赖 prompt（保持稳定引用）。
    const insertContextPaletteEntry = useCallback(
      (entry: ContextPromptPaletteEntry) => {
        promptEditorRef.current?.insertTextAtCursor(
          contextPromptPaletteInsertionText(entry),
        );
      },
      [],
    );

    // 点引用缩略图 → 把它的 `@图片N` 写进提示词。
    //
    // 只写、不重排：序号 = 「这张在当前队列里的第几位」，点一下就悄悄改别人的号会让
    // 提示词里已有的 `@图片N` 全部错位；要换位用户自己拖 chip（拖完 mention 会跟着重
    // 编号）。同一张引用点几次就写几次：用户要在句子不同位置重复提同一张时，这里
    // 不该拦（插入位置由编辑器光标决定）。
    const handleInsertReference = useCallback(
      (mentionName: string) => {
        if (!mentionName) return;
        promptEditorRef.current?.insertTextAtCursor(
          referenceInsertText(mentionName),
        );
      },
      [],
    );
    const requestedGenMode: VideoGenMode = data.genMode ?? "textToVideo";
    const {
      models: availableVideoModels,
      isLoading: videoModelsLoading,
      isFallback: videoModelsFallback,
      channelEnabled: videoChannelEnabled,
      channelDisabledReason: videoChannelDisabledReason,
    } = useFreezoneVideoModels();
    const { models: textModels } = useDirectModelCatalog("text");
    const resolvedTextModel = resolveDirectCanvasModelId(undefined, textModels);
    const persistedVideoModelId =
      typeof data.model === "string" && data.model.trim().length > 0
        ? data.model.trim()
        : null;
    const selectedVideoModel = useMemo(() => {
      return selectVideoModel(availableVideoModels, persistedVideoModelId);
    }, [availableVideoModels, persistedVideoModelId]);
    const boundVideoModel = useMemo(
      () => findPersistedModel(availableVideoModels, persistedVideoModelId),
      [availableVideoModels, persistedVideoModelId],
    );
    // A disabled exact binding is not executable, but its contract is still
    // authoritative for presentation. Keeping these roles separate prevents
    // empty upstream capabilities from being replaced by family presets.
    const capabilityVideoModel = selectedVideoModel ?? boundVideoModel;
    // Preserve a stale node binding for display and diagnostics, but never
    // replace it with a different model or send an empty/default id upstream.
    const modelId = capabilityVideoModel?.id ?? persistedVideoModelId ?? DEFAULT_VIDEO_MODEL_ID;
    const selectedVideoModelId = capabilityVideoModel?.apiModel
      ?? capabilityVideoModel?.id
      ?? persistedVideoModelId
      ?? "";
    const isDirectVideoModel = capabilityVideoModel?.providerId === "direct";
    // 后端旧目录会把普通 Seedance 2.0 标成 generic；模型 id 的明确事实优先，
    // 只有本地识别不出时才采用后端 family。
    const videoModelFamily = resolveVideoModelFamily(
      selectedVideoModelId,
      capabilityVideoModel?.family,
    );
    const genMode = resolveVideoModeForCapability(
      requestedGenMode,
      videoModelFamily,
      capabilityVideoModel,
    );
    const declaredVideoModes = useMemo(
      () => capabilityVideoModel?.supportedModes === undefined
        ? undefined
        : videoModesForCapability(capabilityVideoModel),
      [capabilityVideoModel],
    );
    const isHappyHorseModel = videoModelFamily === "happyhorse";
    const isKacangKlingV2vModel = videoModelFamily === "kacang-kling-v2v";
    // aspectRatio 只认合法的比例预设（含 "auto"）；历史上曾被写成像素串(如
    // "1248:704")的旧节点在这里吸附到最接近的合法视频比例，保证 chip 显示干净。
    const parameterDefaults = modelParameterDefaults(capabilityVideoModel);
    const aspectRatioOptions = videoAspectRatioOptionsForModel(capabilityVideoModel);
    const generationAspectRatioOptions = aspectRatioOptions.filter(
      (value): value is Exclude<FreezoneVideoAspectRatio, "auto"> => value !== "auto",
    );
    const requestedAspectRatio = String(
      data.aspectRatio ?? parameterDefaults?.aspectRatio ?? "",
    );
    const customAspectRatio = capabilityVideoModel?.supportsCustomAspectRatio === true
      && isValidVideoAspectRatio(requestedAspectRatio);
    const aspectRatio: FreezoneVideoAspectRatio = customAspectRatio
      ? (requestedAspectRatio as FreezoneVideoAspectRatio)
      : (aspectRatioOptions as readonly string[]).includes(requestedAspectRatio)
        ? (requestedAspectRatio as FreezoneVideoAspectRatio)
        : generationAspectRatioOptions.length > 0
          ? (snapToAllowedAspectRatio(
              requestedAspectRatio,
              generationAspectRatioOptions,
              parameterDefaults?.aspectRatio ?? generationAspectRatioOptions[0],
            ) as FreezoneVideoAspectRatio)
          : "";
    // 没有数值比例能力时保持空值，让上游自行决定；不要用全局预设重新
    // 填回一个模型明确没有声明的 aspect_ratio。
    const submitAspectRatio: FreezoneVideoAspectRatio =
      aspectRatio === "auto"
        ? generationAspectRatioOptions.length > 0
          ? (snapToAllowedAspectRatio(
              typeof data.widthPx === "number" &&
                typeof data.heightPx === "number" &&
                data.widthPx > 0 &&
                data.heightPx > 0
                ? `${data.widthPx}:${data.heightPx}`
                : "",
              generationAspectRatioOptions,
              generationAspectRatioOptions[0],
            ) as FreezoneVideoAspectRatio)
          : ""
        : aspectRatio;
    const qualityOptions = useMemo(
      () => videoQualityOptionsForModel(capabilityVideoModel),
      [capabilityVideoModel],
    );
    const resolutionOptions = useMemo(
      () =>
        capabilityVideoModel?.runtimeResolutionOptions ??
        capabilityVideoModel?.resolutionOptions ??
        [],
      [capabilityVideoModel],
    );
    const advertisedQualityOptions = useMemo(
      () => advertisedVideoQualityOptionsForModel(capabilityVideoModel, qualityOptions),
      [capabilityVideoModel, qualityOptions],
    );
    const rejectedQualityOptions = useMemo(
      () => rejectedVideoQualityOptionsForModel(capabilityVideoModel),
      [capabilityVideoModel],
    );
    // Pixel slots are an upstream transport detail. Keep the node value a
    // real aspect ratio; the backend maps it to a declared size slot if needed.
    const effectiveAspectRatio: string = submitAspectRatio;
    const quality = normalizeVideoQuality(
      data.quality,
      qualityOptions,
      resolutionToQuality(parameterDefaults?.resolution ?? ""),
    );
    const durationBounds = useMemo(
      () => videoDurationBoundsForModel(capabilityVideoModel),
      [capabilityVideoModel],
    );
    const durationOptions = useMemo(
      () => videoDurationOptionsForModel(capabilityVideoModel),
      [capabilityVideoModel],
    );
    const durationParameterEnabled = videoDurationParameterEnabledForModel(
      capabilityVideoModel,
    );
    const durationSec = clampVideoDuration(
      typeof data.durationSec === "number"
        ? data.durationSec
        : parameterDefaults?.durationSeconds ?? DEFAULT_DURATION_MIN,
      durationBounds,
      durationOptions,
    );
    const dialogueTiming = estimateVideoDialogueTiming(
      resolveVideoDialogueText(data),
    );
    const sceneOptimizeOptions = useMemo(
      () => sceneOptimizeOptionsForModel(capabilityVideoModel),
      [capabilityVideoModel],
    );
    const sceneOptimize = normalizeSceneOptimize(
      data.sceneOptimize,
      sceneOptimizeOptions,
      defaultSceneOptimizeForModel(capabilityVideoModel),
    );
    // 音频开关：用户拨过就听用户的，否则脚本派生的原生声音路由也算权威
    // （`resolveVideoNodeAudioSwitch` 里写了为什么不这么算会静音）。
    const generateAudio = resolveVideoNodeAudioSwitch(data);
    // 全能参考只对 Seedance 2.0 系列模型生效。
    const isSeedance20Model = isSeedance2VideoFamily(videoModelFamily);
    const humanReview = isSeedance20Model;
    const count: VideoGenCount = (data.count ?? 1) as VideoGenCount;
    // 只有拿到权威目录（已加载完成、非兜底、且该节点确实解析出了模型合同）时，
    // 派生出的参数才允许写回节点。目录未就绪时上面那些 normalize* 拿到的是一组
    // 退化值（qualityOptions/aspectionRatioOptions 全空 → 480P / 空字符串），
    // 一旦落盘就会被 autosave 带走：空 aspectRatio 在下次加载时被 store 补成默认
    // 比例，而 1:1 又恰好是 H3 的合法档位，于是永远不会自我纠正。
    // 实测事故：2026-09-14 用户画布 user_local_17cvc3s rev1634 三个视频节点从
    // 16:9 / 480p横 被写成 1:1 / 480P。
    const catalogAuthoritative =
      !videoModelsLoading && !videoModelsFallback && Boolean(capabilityVideoModel);
    useEffect(() => {
      if (!catalogAuthoritative) return;
      const patch: Partial<VideoNodeData> = {};
      if (data.quality !== quality) {
        patch.quality = quality;
      }
      if (data.durationSec !== durationSec) {
        patch.durationSec = durationSec;
      }
      if (data.generateAudio !== generateAudio) {
        patch.generateAudio = generateAudio;
      }
      if (data.aspectRatio !== aspectRatio) {
        patch.aspectRatio = aspectRatio;
      }
      if (data.sceneOptimize !== sceneOptimize) {
        patch.sceneOptimize = sceneOptimize;
      }
      if (selectedVideoModel && data.model !== modelId) {
        patch.model = modelId;
      }
      if (Object.keys(patch).length > 0) {
        updateNodeData(id, patch);
      }
    }, [
      aspectRatio,
      catalogAuthoritative,
      data.aspectRatio,
      data.durationSec,
      data.generateAudio,
      data.generateAudioUserSet,
      data.nativeAudioStrategy,
      data.quality,
      data.sceneOptimize,
      data.model,
      durationSec,
      generateAudio,
      id,
      modelId,
      quality,
      sceneOptimize,
      selectedVideoModel,
      updateNodeData,
    ]);
    const videoBackendForCost =
      videoModelsLoading || videoModelsFallback
        ? null
        : (selectedVideoModel?.apiModel ?? null);
    // Debounce the cost-estimate inputs: dragging the duration slider (and,
    // to a lesser degree, flipping count/quality/model) churns the query key
    // and TanStack Query aborts each in-flight request, spraying "Canceled"
    // rows across the Network tab. Coalesce to one request once the params
    // settle (~350ms). Primitives only — see useDebouncedValue's contract.
    const debouncedBackend = useDebouncedValue(videoBackendForCost, 350);
    const debouncedQuality = useDebouncedValue(quality, 350);
    const debouncedCount = useDebouncedValue(count, 350);
    const debouncedDurationSec = useDebouncedValue(durationSec, 350);
    const videoCreditCost = useGenerationCreditCost(
      "video_backend",
      debouncedBackend,
      {
        surface: "canvas",
        params: { resolution: qualityToResolution(debouncedQuality) },
        quantity: clampGenerationBatchCount(debouncedCount) * debouncedDurationSec,
      },
    );
    const totalCreditCostDisplay = useMemo(() => {
      const total = videoCreditCost.data?.data.cost;
      if (typeof total !== "number") return null;
      return formatCreditCost(total);
    }, [videoCreditCost.data?.data.cost]);
    // 运镜的真相是提示词里那一段（`domain/promptCamera.ts`）：这里只从提示词**反解**
    // 当前点亮的是目录里哪一条，节点上不再有「预设 id」字段。旧画布上的
    // `data.cameraMovement` 由 `canvasStore.normalizeNodes` 水合时折进提示词。
    //
    // Pull the camera-template catalog from `/freezone/video/camera-templates`.
    // Fall back to the bundled `CAMERA_MOVEMENT_PRESETS` while loading or if the
    // backend is unreachable so the chip never goes blank.
    const cameraTemplatesQuery = useFreezoneVideoCameraTemplates();
    const cameraTemplates = useMemo<ReadonlyArray<CameraMovementPreset>>(
      () =>
        cameraTemplatesQuery.templates.length > 0
          ? cameraTemplatesQuery.templates
          : CAMERA_MOVEMENT_PRESETS,
      [cameraTemplatesQuery.templates],
    );
    const cameraTemplatesLoading = cameraTemplatesQuery.isLoading;
    const cameraDirection = useMemo(() => cameraDirectionText(prompt), [prompt]);
    const cameraMovementPreset = useMemo(
      () => resolveCameraPresetForText(cameraTemplates, cameraDirection),
      [cameraTemplates, cameraDirection],
    );
    const cameraMovementId = cameraMovementPreset?.id ?? null;
    // 脚本派生的节点留着该行运动稿的原文快照；「清除」要还原回它，而不是删掉运镜段
    // ——脚本行是 6 段式，删一段会直接撞服务端 `script.motion.segments.v1`（blocking）。
    const cameraOriginalPrompt =
      typeof data[SCRIPT_SHOT_VIDEO_PROMPT_FIELD] === "string"
        ? (data[SCRIPT_SHOT_VIDEO_PROMPT_FIELD] as string)
        : "";
    const { isGenerating, task: generationTask } = useNodeGenerationTaskState(data);
    const upstreamPreviewUrl = isGenerating
      ? resolveFreezoneTaskPreviewUrl(generationTask)
      : null;
    const generationPreviewUrl = historyPreviewUrl ?? upstreamPreviewUrl;
    const generationError =
      typeof data.generationError === 'string' ? data.generationError.trim() : '';
    // Only treat as a failure-state once generation has stopped and produced no
    // video — a stale error must never hide a successfully generated clip.
    const hasGenerationError =
      !isGenerating && !data.videoUrl && generationError.length > 0;
    const generationErrorRequestId =
      typeof data.generationErrorRequestId === "string" && data.generationErrorRequestId
        ? data.generationErrorRequestId
        : "";
    const generationErrorDetails =
      typeof data.generationErrorDetails === "string" ? data.generationErrorDetails.trim() : "";
    const generationErrorStage =
      typeof data.generationErrorStage === "string" ? data.generationErrorStage.trim() : "";
    const generationErrorSuggestedAction =
      typeof data.generationErrorSuggestedAction === "string"
        ? data.generationErrorSuggestedAction.trim()
        : "";
    const generationErrorCode =
      typeof data.generationErrorCode === "string" ? data.generationErrorCode.trim() : "";
    const generationErrorRetryable = data.generationErrorRetryable === true;
    const generationRecoveryJobId =
      typeof data.generationRecoveryJobId === "string"
        ? data.generationRecoveryJobId.trim()
        : "";
    const legacyRecoveryText = `${generationErrorDetails}\n${generationError}`;
    const recoveryIdentifier =
      generationRecoveryJobId || extractProviderTaskId(legacyRecoveryText) || "";
    const effectiveGenerationErrorCode =
      generationErrorCode || extractGenerationErrorCode(legacyRecoveryText) || "";
    const canRecoverVideoTask = Boolean(
      recoveryIdentifier
      && (generationErrorStage === "query" || generationErrorStage === "poll")
      && (
        RECOVERABLE_VIDEO_QUERY_ERROR_CODES.has(effectiveGenerationErrorCode)
        || generationErrorRetryable
      ),
    );

    // 生成结束（成功/失败）后清掉临时历史预览，让主体回到最新结果。
    useEffect(() => {
      if (!isGenerating) setHistoryPreviewUrl(null);
    }, [isGenerating]);

    const handleRestoreHistory = useCallback(
      (record: FreezoneGenerationHistoryRecord) => {
        const url = historyRecordOutputUrl(record);
        if (!url) return;
        // 生成进行中：仅做非破坏性预览，绝不动 videoUrl，也不打断在途任务。
        if (isGenerating) {
          setHistoryPreviewUrl(url);
          return;
        }
        setHistoryPreviewUrl(null);
        updateNodeData(id, {
          videoUrl: url,
          ...videoGenerationSourcePatch(url, record.result, record.job_id),
          isGenerating: false,
          generationStartedAt: null,
          sourceFileName: null,
          generationError: null,
          generationErrorDetails: null,
          generationErrorRequestId: null,
          generationErrorCode: null,
          generationErrorRetryable: null,
          generationRecoveryJobId: null,
          generationRecoveryTaskType: null,
          // 恢复单条历史结果时旧批次画册已与主视频脱钩——一并清掉。
          generationBatch: null,
          generationBatchSources: null,
        });
      },
      [id, isGenerating, updateNodeData],
    );

    const handleRecoverVideoTask = useCallback(async () => {
      if (isRecovering) return;
      const projectId = readUrl().project;
      const recoveryJobId = recoveryIdentifier;
      if (!projectId || !recoveryJobId) {
        toast.error("没有可重新获取的上游视频任务");
        return;
      }
      setIsRecovering(true);
      updateNodeData(id, {
        isGenerating: true,
        generationStartedAt: Date.now(),
        generationError: null,
        generationErrorDetails: null,
        generationErrorRequestId: null,
        generationErrorStage: null,
        generationErrorSuggestedAction: null,
        generationErrorCode: null,
        generationErrorRetryable: null,
      });
      try {
        const ref = await recoverFreezoneVideoJob(projectId, recoveryJobId);
        registerNodeGenerationTask(id, ref, { primary: true });
        const mediaResult = await awaitFreezoneJobMediaResult(projectId, ref, {
          probeDelayMs: 900,
          maxProbeMs: 120_000,
        });
        if (!mediaResult.url) {
          throw new Error("重新获取完成但没有返回视频地址");
        }
        updateNodeData(id, {
          ...buildGenerationTerminalPatch({
            videoUrl: mediaResult.url,
            ...videoGenerationSourcePatch(mediaResult.url, mediaResult.result, ref.job_id),
            ...buildGeneratedRightsPatch(data),
            sourceFileName: null,
            generationError: null,
            generationErrorDetails: null,
            generationErrorRequestId: null,
            generationErrorStage: null,
            generationErrorSuggestedAction: null,
            generationErrorCode: null,
            generationErrorRetryable: null,
            generationRecoveryJobId: null,
            generationRecoveryTaskType: null,
          }),
        });
        toast.success("已重新获取上游视频结果");
      } catch (error) {
        const resolved = resolveErrorContent(error, "重新获取视频失败");
        const diagnostics = resolveGenerationErrorDiagnostics(error, resolved.details);
        updateNodeData(id, {
          ...buildGenerationTerminalPatch(),
          generationError: backendErrorToastMessage(error, t),
          generationErrorDetails: diagnostics.details ?? resolved.message,
          generationErrorRequestId: diagnostics.requestId,
          generationErrorStage: diagnostics.stage ?? "query",
          generationErrorSuggestedAction: diagnostics.suggestedAction,
          generationErrorCode: diagnostics.errorCode,
          generationErrorRetryable: diagnostics.retryable,
          generationRecoveryJobId: recoveryJobId,
          generationRecoveryTaskType: "freezone_video_gen",
        });
        toast.error("重新获取视频失败，可查看详情后再次尝试");
      } finally {
        setIsRecovering(false);
      }
    }, [id, isRecovering, recoveryIdentifier, t, updateNodeData]);

    const updateReferencePrompt = useCallback(
      (nextPrompt: string) => updateNodeData(id, { prompt: nextPrompt }),
      [id, updateNodeData],
    );
    const {
      isConnected,
      referenceMedia,
      referenceMediaCapInfo,
      mentionCandidates,
      upstreamTextContents,
      upstreamTextJoined,
      upstreamCounts,
      upstreamTypeCounts,
      handleDetachUpstream,
    } = useVideoReferences({
      nodeId: id,
      prompt,
      referenceOrder: data.referenceOrder,
      genMode,
      referenceLimits: capabilityVideoModel?.referenceLimits,
      capsByMode: REFERENCE_CAPS_BY_MODE,
      updatePrompt: updateReferencePrompt,
    });

    // The shipped MiniMax H3 AutoDL workflow exposes image references but no
    // video-reference slot.  A connected predecessor video therefore uses its
    // tail still as one image reference, while other models retain their
    // declared video-reference behavior.
    const autoTailFrameReference =
      genMode === "allReference" &&
      upstreamCounts.videos > 0 &&
      Number(capabilityVideoModel?.referenceLimits?.allReference?.video ?? 0) === 0 &&
      Number(capabilityVideoModel?.referenceLimits?.allReference?.image ?? 0) > 0 &&
      /h3|minimax/i.test(
        `${selectedVideoModelId} ${capabilityVideoModel?.workflowId ?? ""} ${capabilityVideoModel?.label ?? ""}`,
      );

    const isClipMode = Boolean(data.isClipMode);
    const clipStartMs =
      typeof data.clipStartMs === "number" ? data.clipStartMs : null;
    const clipEndMs =
      typeof data.clipEndMs === "number" ? data.clipEndMs : null;
    const durationMs =
      typeof data.durationMs === "number" ? data.durationMs : null;

    // 时长对账（只能真）：请求值只认提交时冻结的 `lastRequestedDurationSeconds`，
    // 实测值优先用后端 ffprobe 的 `resourceMeta.durationSec`，浏览器探到的
    // `durationMs` 兜底。任一侧缺失就不出角标——不拿当前配置冒充那次请求。
    const durationHonesty = useMemo(() => {
      const resourceMeta = parseResourceMeta(data.resourceMeta);
      const actualFromElement =
        typeof data.durationMs === "number" && data.durationMs > 0
          ? data.durationMs / 1000
          : null;
      const requested =
        typeof data.lastRequestedDurationSeconds === "number" &&
        data.lastRequestedDurationSeconds > 0
          ? data.lastRequestedDurationSeconds
          : null;
      return evaluateDurationHonesty({
        requestedSeconds: requested,
        actualSeconds: resourceMeta?.durationSec ?? actualFromElement,
      });
    }, [
      data.resourceMeta,
      data.durationMs,
      data.lastRequestedDurationSeconds,
    ]);

    const resolvedTitle = useMemo(
      () => resolveNodeDisplayName(CANVAS_NODE_TYPES.video, data),
      [data],
    );
    const layoutAspectRatio = normalizeAspectRatioForLayout(
      aspectRatio,
      "16:9",
    );
    const usesConfiguredAspectLayout = !data.videoUrl;
    const configuredAspectMin = usesConfiguredAspectLayout
      ? resolveResizeMinConstraintsByAspect(
          layoutAspectRatio,
          { minWidth: 300, minHeight: 300 },
        )
      : { minWidth: MIN_WIDTH, minHeight: MIN_HEIGHT };
    const resolvedWidth = Math.max(
      configuredAspectMin.minWidth,
      Math.round(width ?? DEFAULT_WIDTH),
    );
    const resolvedHeight = Math.max(
      configuredAspectMin.minHeight,
      Math.round(height ?? DEFAULT_HEIGHT),
    );
    // 收起态浮动面板固定基础尺寸；放大用居中弹窗（见下方 OperationPanelShell）。
    const [panelExpanded, setPanelExpanded] = useState(false);
    const panelHeight = OPERATIONS_PANEL_HEIGHT;
    const panelOverhang = OPERATIONS_PANEL_OVERHANG;

    // ── 叠卡画册（count > 1 的一组生成结果，与图片节点同构）──
    // 收拢时主视频后探出 N-1 张卡片边；hover 出现右上角数量徽标，点开展开成
    // 宫格画册。展开态点视频设为主视频、可单独「应用到画布」/ 下载。
    const albumRootRef = useRef<HTMLDivElement | null>(null);
    const albumPointerDownPosRef = useRef<{ x: number; y: number } | null>(null);
    const [albumExpanded, setAlbumExpanded] = useState(false);
    // 本次会话内"应到条数"——未完成的在画册里占位。存模块级登记表而非组件
    // state：onlyRenderVisibleElements 下平移出视口会卸载组件，state 会丢。
    const albumPendingTotal = useAlbumPendingTotal(id);
    const albumUrls = useMemo(() => {
      const raw = data.generationBatch;
      if (!Array.isArray(raw)) return [];
      return raw.filter((u): u is string => typeof u === 'string' && u.length > 0);
    }, [data.generationBatch]);
    const albumTotalSlots = Math.max(albumUrls.length, albumPendingTotal);
    const albumPendingCount = Math.max(0, albumPendingTotal - albumUrls.length);
    const hasAlbum = albumTotalSlots > 1;

    // 画册展开期间注册为本节点的 activeOverlay：外部 action 工具条 / 替换素材
    // 把手 / + 派生按钮都认它让位（拖动重新选中也压得住）。
    useEffect(() => {
      if (!albumExpanded) return;
      setActiveOverlayNodeId(id);
      return () => {
        if (useCanvasStore.getState().activeOverlayNodeId === id) {
          setActiveOverlayNodeId(null);
        }
      };
    }, [albumExpanded, id, setActiveOverlayNodeId]);

    useEffect(() => {
      if (!albumExpanded) return;
      const handlePointerDown = (event: PointerEvent) => {
        if (albumRootRef.current?.contains(event.target as Node)) return;
        setAlbumExpanded(false);
      };
      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key === 'Escape') setAlbumExpanded(false);
      };
      window.addEventListener('pointerdown', handlePointerDown);
      window.addEventListener('keydown', handleKeyDown);
      return () => {
        window.removeEventListener('pointerdown', handlePointerDown);
        window.removeEventListener('keydown', handleKeyDown);
      };
    }, [albumExpanded]);

    const handleSetAlbumMainVideo = useCallback(
      (url: string) => {
        updateNodeData(id, { videoUrl: url, sourceFileName: null,
          ...videoGenerationSourcePatch(url, { video_generation_source: data.generationBatchSources?.[url] }),
        });
        setAlbumExpanded(false);
      },
      [data.generationBatchSources, id, updateNodeData],
    );

    // 展开画册时取消节点激活态；必须经 onNodesChange 清 React Flow 自身的
    // selected 标志（只清 store 的 selectedNodeId 会被选中同步 effect 写回）。
    // 副作用放在 setState updater 外面：updater 必须纯（StrictMode 会双调用）。
    const handleToggleAlbumExpanded = useCallback(() => {
      if (!albumExpanded) {
        const store = useCanvasStore.getState();
        const selectionChanges = store.nodes
          .filter((node) => node.selected)
          .map((node) => ({ id: node.id, type: 'select' as const, selected: false }));
        if (selectionChanges.length > 0) {
          store.onNodesChange(selectionChanges);
        }
        setSelectedNode(null);
        // 每次展开重置「应用到画布」的落点游标。
        albumAppliedCountRef.current = 0;
      }
      setAlbumExpanded(!albumExpanded);
    }, [albumExpanded, setSelectedNode]);

    // 「应用到画布」：把这条视频作为独立视频节点放到展开宫格右侧。连续应用
    // 的落点逐次错开，避免精确叠在同一坐标上只看得见最后一个。
    const albumAppliedCountRef = useRef(0);
    const handleApplyAlbumVideoToCanvas = useCallback(
      (url: string) => {
        const self = useCanvasStore.getState().nodes.find((n) => n.id === id);
        if (!self) return;
        const applyIndex = albumAppliedCountRef.current;
        albumAppliedCountRef.current += 1;
        const position = {
          x: self.position.x + resolvedWidth * 2 + 12 + 48 + applyIndex * 36,
          y: self.position.y + applyIndex * 36,
        };
        const newNodeId = addNode(CANVAS_NODE_TYPES.video, position, {
          videoUrl: url,
          ...videoGenerationSourcePatch(url, { video_generation_source: data.generationBatchSources?.[url] }),
          aspectRatio: data.aspectRatio,
          user_spawned: true,
        } as Partial<VideoNodeData>);
        setSelectedNode(newNodeId);
      },
      [addNode, data.aspectRatio, data.generationBatchSources, id, resolvedWidth, setSelectedNode],
    );

    const handleDownloadAlbumVideo = useCallback(
      async (url: string, index: number) => {
        try {
          await downloadUrlAsFile(resolveImageDisplayUrl(url), `video-gen-${id}-${index + 1}.mp4`);
        } catch (error) {
          console.error('[video-node] album download failed', error);
        }
      },
      [id],
    );

    const clearTransientPreview = useCallback(() => {
      if (transientUrlRef.current) {
        URL.revokeObjectURL(transientUrlRef.current);
        transientUrlRef.current = null;
      }
      setTransientPreviewUrl(null);
    }, []);

    const processFile = useCallback(
      async (file: File) => {
        if (!isVideoFile(file)) return;
        const projectId = readUrl().project;
        if (!projectId) {
          console.error("[video-node] no project in URL");
          return;
        }
        clearTransientPreview();
        const previewUrl = URL.createObjectURL(file);
        transientUrlRef.current = previewUrl;
        setTransientPreviewUrl(previewUrl);
        updateNodeData(id, { sourceFileName: file.name, isUploading: true });
        try {
          // HEVC（飞书录屏/iPhone）等 Web 不兼容编码先在浏览器内转成 H.264 再上传，
          // 否则 Edge 等无对应解码器的浏览器只有声音没画面。见 videoTranscode.ts。
          // 转码期间 UI 统一走「上传中」loading，不单独显示转码进度。
          const prepared = await ensureWebSafeVideo(file);
          if (prepared.transcoded) {
            // 源编码在本浏览器可能根本解不了（Edge+HEVC），本地预览也换成转码产物。
            clearTransientPreview();
            const preparedUrl = URL.createObjectURL(prepared.file);
            transientUrlRef.current = preparedUrl;
            setTransientPreviewUrl(preparedUrl);
          }
          const uploaded = await uploadFreezoneVideo(
            projectId,
            prepared.file,
            prepared.file.name,
          );
          updateNodeData(id, {
            videoUrl: uploaded.url,
            previewImageUrl: null,
            sourceFileName: file.name,
            isUploading: false,
          });
        } catch (error) {
          console.error("[video-node] upload failed", error);
          updateNodeData(id, { isUploading: false });
          clearTransientPreview();
        }
      },
      [clearTransientPreview, id, updateNodeData],
    );

    const handleFileChange = useCallback(
      async (event: ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (file) await processFile(file);
        event.target.value = "";
      },
      [processFile],
    );

    const handleDrop = useCallback(
      async (event: DragEvent<HTMLElement>) => {
        event.preventDefault();
        event.stopPropagation();
        const file = resolveDroppedVideoFile(event);
        if (file) await processFile(file);
      },
      [processFile],
    );

    const handleDragOver = useCallback((event: DragEvent<HTMLElement>) => {
      event.preventDefault();
      event.stopPropagation();
    }, []);

    const handleUploadClick = useCallback(() => {
      inputRef.current?.click();
    }, []);

    // Spawn reference nodes from selected asset-library entries — one per
    // selection, stacked vertically to the left of this video node, then wired
    // as upstream references so they show up in the operations panel. The node
    // type depends on the media: images/videos become upload nodes carrying
    // imageUrl/videoUrl, audio becomes an audio node carrying audioUrl.
    const spawnCharacterLibraryReferences = useCallback(
      (selections: ReadonlyArray<AssetLibrarySelection>) => {
        if (selections.length === 0) return;
        const state = useCanvasStore.getState();
        const self = state.nodes.find((n) => n.id === id);
        if (!self) return;
        const UPLOAD_WIDTH = 320;
        const UPLOAD_HEIGHT = 240;
        const GAP_X = 40;
        const GAP_Y = 24;
        const baseX = self.position.x - UPLOAD_WIDTH - GAP_X;
        const totalH =
          UPLOAD_HEIGHT * selections.length + GAP_Y * (selections.length - 1);
        const startY =
          self.position.y + ((self.height ?? DEFAULT_HEIGHT) - totalH) / 2;
        const newIds: string[] = [];
        selections.forEach((sel, idx) => {
          const y = startY + idx * (UPLOAD_HEIGHT + GAP_Y);
          const displayName = sel.name || undefined;
          let newId: string;
          if (sel.media === "audio") {
            newId = addNode(
              CANVAS_NODE_TYPES.audio,
              { x: baseX, y },
              { audioUrl: sel.url, displayName },
            );
          } else if (sel.media === "video") {
            // 资产库视频作为「上游视频引用素材」：建 referenceOnly 的 video 节点，
            // 它能播放视频本体、被 isVideoNode 识别、下游自动切 videoEdit。之前建的是
            // 只渲染图片的 upload 节点——即便塞了 videoUrl 也不显示、也不被识别成视频。
            newId = addNode(
              CANVAS_NODE_TYPES.video,
              { x: baseX, y },
              {
                videoUrl: sel.url,
                aspectRatio: data.aspectRatio,
                displayName,
                referenceOnly: true,
              } as Partial<VideoNodeData>,
            );
          } else {
            newId = addNode(
              CANVAS_NODE_TYPES.upload,
              { x: baseX, y },
              {
                imageUrl: sel.url,
                previewImageUrl: sel.url,
                displayName,
              },
            );
          }
          addEdge(newId, id);
          newIds.push(newId);
        });
        state.autoGroupSpawn(id, newIds, { label: '资产参考组' });
      },
      [addEdge, addNode, data.aspectRatio, id],
    );

    const handleExternalAssetFiles = useCallback(
      (event: ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(event.target.files ?? []);
        // 允许连续两次选择同一个文件。
        event.target.value = "";
        if (files.length === 0) return;

        const state = useCanvasStore.getState();
        const self = state.nodes.find((node) => node.id === id);
        // 文件选择器打开期间节点可能已经被删除。
        if (!self) return;

        spawnExternalAssetNodes(
          {
            id,
            position: self.position,
            height: self.measured?.height ?? self.height ?? undefined,
          },
          files,
          {
            addNode,
            addEdge,
            // EventBus.publish 使用实例上下文，不能直接把方法引用裸传。
            publish: (type, payload) => canvasEventBus.publish(type, payload),
            autoGroupSpawn: (sourceId, spawnedIds, options) =>
              state.autoGroupSpawn(sourceId, spawnedIds, options),
          },
        );
      },
      [addEdge, addNode, id],
    );

    const promptOptimizationContextSignature = useMemo(
      () => JSON.stringify({
        prompt: prompt.trim(),
        model_id: modelId,
        api_model: selectedVideoModelId,
        mode: genMode,
        duration_seconds: durationSec,
        aspect_ratio: effectiveAspectRatio,
        quality,
        camera: cameraDirection ? { text: cameraDirection } : null,
        generate_audio: generateAudio,
        human_review: humanReview,
        scene_optimize: sceneOptimize ?? null,
        references: buildVideoPromptOptimizationReferences(
          genMode,
          referenceMediaCapInfo.map(({ item, withinCap }) => ({
            kind: item.kind,
            withinCap,
            url:
              item.kind === "image"
                ? item.imageUrl
                : item.kind === "video"
                  ? item.videoUrl
                  : item.audioUrl,
            label: item.displayName ?? null,
          })),
        ),
        upstream_text: upstreamTextJoined,
      }),
      [
        cameraDirection,
        durationSec,
        genMode,
        generateAudio,
        humanReview,
        isSeedance20Model,
        modelId,
        prompt,
        quality,
        referenceMediaCapInfo,
        sceneOptimize,
        selectedVideoModelId,
        submitAspectRatio,
        effectiveAspectRatio,
        upstreamTextJoined,
      ],
    );

    const handleOptimizePrompt = useCallback(
      async (
        researchMode: PromptOptimizationResearchMode = "standard",
        options: { keepPanel?: boolean } = {},
      ) => {
      if (isOptimizingPrompt || isGenerating) return;
      const originalPrompt = prompt.trim();
      if (!originalPrompt) return;
      const project = readUrl().project;
      if (!project) {
        toast.error("当前 URL 缺少项目，无法调用提示词优化模型。");
        return;
      }
      const references = buildVideoPromptOptimizationReferences(
        genMode,
        referenceMediaCapInfo.map(({ item, withinCap }) => ({
          kind: item.kind,
          withinCap,
          url:
            item.kind === "image"
              ? item.imageUrl
              : item.kind === "video"
                ? item.videoUrl
                : item.audioUrl,
          label: item.displayName ?? null,
        })),
      );
      setIsOptimizingPrompt(true);
      // 换联网深度重跑时保留面板，让按钮上的加载态可见。
      if (!options.keepPanel) setPromptOptimization(null);
      try {
        const request: FreezonePromptOptimizePayload = {
          text: originalPrompt,
          nodeType: "video",
          targetModelId: modelId,
          targetApiModel: selectedVideoModel?.apiModel ?? modelId,
          targetModelLabel: selectedVideoModel?.label ?? modelId,
          params: {
            mode: genMode,
            duration_seconds: durationSec,
            aspect_ratio: effectiveAspectRatio,
            quality,
            resolution: qualityToResolution(quality),
            camera_movement: cameraDirection ? { text: cameraDirection } : null,
            generate_audio: generateAudio,
            scene_optimize: sceneOptimize ?? null,
            upstream_text_is_injected_separately: upstreamTextJoined.length > 0,
          },
          references,
          researchMode,
          guidance:
            "只输出当前视频节点自有提示词。上游文本会在生成提交时自动拼接，不要重复抄入优化稿；运镜已经在提示词里，保持它、不要另起一段重复描述；只能使用 AVAILABLE REFERENCES 中列出的引用名。",
        };
        const ref = await submitFreezonePromptOptimize(project, request);
        await awaitTaskCompletion(ref.task_key, project);
        const result = await fetchFreezonePromptOptimizeResult(project, ref.job_id);
        setPromptOptimization({
          originalPrompt,
          request,
          result,
          jobId: ref.job_id,
          taskKey: ref.task_key,
          contextSignature: promptOptimizationContextSignature,
        });
      } catch (error) {
        console.error("[video-node] AI prompt optimization failed", error);
        toast.error(`提示词优化失败：${backendErrorToastMessage(error, t)}`);
      } finally {
        setIsOptimizingPrompt(false);
      }
    }, [
      cameraDirection,
      durationSec,
      genMode,
      generateAudio,
      humanReview,
      isGenerating,
      isOptimizingPrompt,
      isSeedance20Model,
      modelId,
      prompt,
      promptOptimizationContextSignature,
      quality,
      referenceMediaCapInfo,
      sceneOptimize,
      selectedVideoModel?.apiModel,
      selectedVideoModel?.label,
      submitAspectRatio,
      effectiveAspectRatio,
      t,
      upstreamTextJoined.length,
    ]);

    const handleReoptimizePrompt = useCallback(
      async (mode: PromptOptimizationResearchMode) => {
        if (promptOptimizationRerunningMode) return;
        setPromptOptimizationRerunningMode(mode);
        try {
          await handleOptimizePrompt(mode, { keepPanel: true });
        } finally {
          setPromptOptimizationRerunningMode(null);
        }
      },
      [handleOptimizePrompt, promptOptimizationRerunningMode],
    );

    const applyPromptOptimization = useCallback(() => {
      if (!promptOptimization) return;
      if (promptOptimization.contextSignature !== promptOptimizationContextSignature) {
        toast.warning("节点内容或执行配置已变化，请重新优化后再采用。");
        return;
      }
      const { originalPrompt, request, result } = promptOptimization;
      setPromptDraft(result.optimized_prompt);
      updateNodeData(id, {
        prompt: result.optimized_prompt,
        prompt_optimizer_original_prompt: originalPrompt,
        prompt_optimizer_model: result.optimizer_model,
        prompt_optimizer_profile: result.target_model_profile,
        prompt_optimizer_revision: result.revision,
        prompt_optimizer_changes: result.changes,
        prompt_optimizer_applied_rules: result.applied_rules,
        prompt_optimizer_warnings: result.warnings,
        prompt_optimizer_preserved_intent: result.preserved_intent,
        prompt_optimizer_output_language: result.output_language,
        prompt_optimizer_knowledge_profiles: result.knowledge_profiles,
        prompt_optimizer_knowledge_sources: result.knowledge_sources,
        prompt_optimizer_knowledge_live_sources: result.knowledge_live_sources,
        prompt_optimizer_knowledge_retrieval_mode: result.knowledge_retrieval_mode,
        prompt_optimizer_knowledge_profile_details: result.knowledge_profile_details,
        prompt_optimizer_strategy_contract: result.strategy_contract,
        prompt_optimizer_research_mode: result.research_mode,
        prompt_optimizer_research_status: result.research_status,
        prompt_optimizer_research_used: result.research_used,
        prompt_optimizer_research_sources: result.research_sources,
        prompt_optimizer_research_assessment: result.research_assessment,
        prompt_optimizer_critic_passed: result.critic_passed,
        prompt_optimizer_critic_issues: result.critic_issues,
        prompt_optimizer_critic_repairs: result.critic_repairs,
        prompt_optimizer_director_recipe_ids: result.director_recipe_ids,
        prompt_optimizer_director_recipe_receipt: result.director_recipe_receipt,
        prompt_optimizer_model_capability_snapshot: result.model_capability_snapshot,
        prompt_optimizer_reference_manifest: result.reference_manifest,
        prompt_optimizer_target_model_id: request.targetModelId,
        prompt_optimizer_target_api_model: request.targetApiModel ?? request.targetModelId,
        prompt_optimizer_target_model_label: request.targetModelLabel ?? request.targetModelId,
        prompt_optimizer_target_mode: request.params?.mode ?? null,
        prompt_optimizer_request_snapshot: request,
        prompt_optimizer_job_id: promptOptimization.jobId,
        prompt_optimizer_task_key: promptOptimization.taskKey,
        prompt_optimized_at: new Date().toISOString(),
      });
      setPromptOptimization(null);
    }, [id, promptOptimization, promptOptimizationContextSignature, updateNodeData]);

    const handleTranslatePrompt = useCallback(async () => {
      if (isTranslatingPrompt || isGenerating) return;
      const trimmed = prompt.trim();
      if (trimmed.length === 0) return;
      const project = readUrl().project;
      if (!project) {
        console.error("[video-node] translate: no project in URL");
        return;
      }
      setIsTranslatingPrompt(true);
      try {
        const ref = await submitFreezoneTextTranslate(project, {
          text: prompt,
          nodeType: "video",
          model: resolvedTextModel || undefined,
          canvasId: readUrl().canvas ?? "default",
          nodeId: id,
        });
        await awaitTaskCompletion(ref.task_key, project);
        const result = await fetchFreezoneTextTranslateResult(
          project,
          ref.job_id,
        );
        if (result.translated_text) {
          updateNodeData(id, { prompt: result.translated_text });
        }
      } catch (error) {
        console.error("[video-node] translate failed", error);
      } finally {
        setIsTranslatingPrompt(false);
      }
    }, [id, isGenerating, isTranslatingPrompt, prompt, resolvedTextModel, updateNodeData]);

    useEffect(() => {
      return canvasEventBus.subscribe("video-node/reupload", ({ nodeId }) => {
        if (nodeId !== id) return;
        inputRef.current?.click();
      });
    }, [id]);

    useEffect(() => {
      return canvasEventBus.subscribe(
        "video-node/external-file",
        ({ nodeId, file }) => {
          if (nodeId !== id || !isVideoFile(file)) return;
          void processFile(file);
        },
      );
    }, [id, processFile]);

    useVideoModeReconciliation({
      nodeId: id,
      storedMode: data.genMode,
      renderedMode: genMode,
      videoModelFamily,
      mediaCounts: upstreamCounts,
      typedCounts: upstreamTypeCounts,
      isHappyHorseModel,
      supportedModes: declaredVideoModes,
      referenceLimits: capabilityVideoModel?.referenceLimits,
      catalogAuthoritative,
      updateNodeData,
    });

    useEffect(
      () => () => {
        clearTransientPreview();
      },
      [clearTransientPreview],
    );

    const { videoSource, lowDetailZoom, lodStill, videoPosterSource } =
      useVideoNodePreview(data.videoUrl, transientPreviewUrl, upstreamPreviewUrl);

    useEffect(() => {
      updateNodeInternals(id);
    }, [id, resolvedHeight, resolvedWidth, updateNodeInternals]);

    const [hasMetadata, setHasMetadata] = useState(false);
    const [videoLoadError, setVideoLoadError] = useState(false);
    useEffect(() => {
      setHasMetadata(false);
      setVideoLoadError(false);
    }, [videoSource]);

    // ---- subtitle erase mode (libtv-style 智能去字幕) ------------------------
    const subtitleEraseMode = data.subtitleEraseMode ?? null;
    const subtitleEraseBox = data.subtitleEraseBox ?? null;
    const [isErasing, setIsErasing] = useState(false);
    // Transient drag state — null when not currently dragging.
    const [eraseDrag, setEraseDrag] = useState<{
      x0: number;
      y0: number;
      x1: number;
      y1: number;
    } | null>(null);

    /**
     * Compute the displayed video frame rect inside its container (object-contain).
     * Returns container-pixel coords. We use this to (a) size the box overlay so
     * it sits on top of the actual video pixels (not the letterbox bars) and (b)
     * convert pointer coords ↔ normalized 0..1 source coords.
     */
    const getDisplayedVideoRect = useCallback(
      (containerW: number, containerH: number) => {
        const vw = data.widthPx ?? 0;
        const vh = data.heightPx ?? 0;
        if (!vw || !vh || containerW <= 0 || containerH <= 0) {
          return { left: 0, top: 0, width: containerW, height: containerH };
        }
        const containerRatio = containerW / containerH;
        const videoRatio = vw / vh;
        if (videoRatio > containerRatio) {
          const w = containerW;
          const h = containerW / videoRatio;
          return { left: 0, top: (containerH - h) / 2, width: w, height: h };
        }
        const h = containerH;
        const w = containerH * videoRatio;
        return { left: (containerW - w) / 2, top: 0, width: w, height: h };
      },
      [data.heightPx, data.widthPx],
    );

    const handleEraseExit = useCallback(() => {
      updateNodeData(id, { subtitleEraseMode: null, subtitleEraseBox: null });
      setEraseDrag(null);
    }, [id, updateNodeData]);

    const handleClipSubmit = useCallback(
      async (startMs: number, endMs: number) => {
        if (isComposingClip) return;
        const sourceUrl = data.videoUrl;
        if (!sourceUrl) return;
        if (endMs <= startMs) return;
        const projectId = readUrl().project;
        if (!projectId) {
          console.error("[video-node] clip: no project in URL");
          return;
        }
        // Compose only supports 720p / 1080p — fall back to 720p for 480P sources.
        const composeResolution = quality === "1080P" ? "1080p" : "720p";
        setIsComposingClip(true);
        setClipError(null);
        try {
          const sourceStart = startMs / 1000;
          const sourceEnd = endMs / 1000;
          const ref = await submitFreezoneVideoCompose(projectId, {
            resolution: composeResolution,
            tracks: [
              {
                trackId: `track_${id}_video`,
                kind: "video",
                items: [
                  {
                    itemId: `item_${id}_${Date.now()}`,
                    sourceUrl,
                    timelineStart: 0,
                    sourceStart,
                    sourceEnd,
                  },
                ],
              },
            ],
          });
          await awaitTaskCompletion(ref.task_key, projectId);
          const result = await fetchFreezoneJobResult(
            projectId,
            "freezone_video_compose",
            ref.job_id,
          );
          if (result.url) {
            const state = useCanvasStore.getState();
            const position = state.findNodePosition(
              id,
              DEFAULT_WIDTH,
              DEFAULT_HEIGHT,
            );
            const newNodeId = addNode(CANVAS_NODE_TYPES.video, position, {
              videoUrl: result.url,
              durationMs: Math.round((sourceEnd - sourceStart) * 1000),
              displayName: "剪辑",
            });
            addEdge(id, newNodeId);
            updateNodeData(id, {
              isClipMode: false,
              clipStartMs: null,
              clipEndMs: null,
            });
          } else {
            console.warn("[video-node] compose completed without url", result);
            setClipError("剪辑完成但未返回视频地址");
          }
        } catch (error) {
          console.error("[video-node] clip compose failed", error);
          setClipError(error instanceof Error ? error.message : String(error));
        } finally {
          setIsComposingClip(false);
        }
      },
      [
        addEdge,
        addNode,
        data.videoUrl,
        id,
        isComposingClip,
        quality,
        updateNodeData,
      ],
    );

    const handleEraseSubmit = useCallback(async () => {
      if (isErasing) return;
      if (!data.videoUrl) return;
      if (subtitleEraseMode === "box" && !subtitleEraseBox) return;
      const projectId = readUrl().project;
      if (!projectId) {
        console.error("[video-node] no project in URL");
        return;
      }
      setIsErasing(true);
      try {
        const ref = await submitFreezoneVideoErase(projectId, {
          sourceUrl: data.videoUrl,
          mode: subtitleEraseMode === "box" ? "box" : "smart_subtitle",
          box: subtitleEraseMode === "box" ? subtitleEraseBox : null,
        });
        await awaitTaskCompletion(ref.task_key, projectId);
        const result = await fetchFreezoneJobResult(
          projectId,
          "freezone_video_erase",
          ref.job_id,
        );
        if (result.url) {
          updateNodeData(id, {
            videoUrl: result.url,
            subtitleEraseMode: null,
            subtitleEraseBox: null,
          });
        } else {
          console.warn("[video-node] erase completed without url", result);
        }
      } catch (error) {
        console.error("[video-node] subtitle erase failed", error);
      } finally {
        setIsErasing(false);
      }
    }, [
      data.videoUrl,
      id,
      isErasing,
      subtitleEraseBox,
      subtitleEraseMode,
      updateNodeData,
    ]);

    const selectedModelOffline =
      !selectedVideoModel ||
      selectedVideoModel?.disabled === true ||
      selectedVideoModel?.enabled === false ||
      videoChannelEnabled === false;
    const selectedModelOfflineReason =
      (persistedVideoModelId && !selectedVideoModel && boundVideoModel
        ? boundVideoModel.disabledReason?.trim() ||
          `节点绑定的视频模型当前不可用：${persistedVideoModelId}，请重新检测或选择模型`
        : persistedVideoModelId && !selectedVideoModel
          ? `节点绑定的视频模型已失效：${persistedVideoModelId}，请重新选择模型`
        : !selectedVideoModel
          ? "未配置可用的视频模型，请先在模型中心配置并验证"
        : selectedVideoModel?.disabledReason?.trim() ||
        videoChannelDisabledReason?.trim() ||
        "视频渠道未接通") as string;
    const hasPromptText =
      prompt.trim().length > 0 || upstreamTextJoined.length > 0;
    const mediaRejectionReason = videoSubmitMediaRejectionReason(
      genMode,
      videoModelFamily,
      // AutoDL H3's current contract accepts image references but not video
      // references.  Keep the canvas simple: a connected upstream video is
      // treated as one pending tail-frame image and converted at submit time.
      autoTailFrameReference
        ? {
            ...upstreamCounts,
            images: upstreamCounts.images + upstreamCounts.videos,
            videos: 0,
          }
        : upstreamCounts,
      capabilityVideoModel,
    );
    const promptContractReason = videoPromptContractIssue(
      `${prompt}\n${upstreamTextJoined}`,
      upstreamCounts,
    );
    const submitDisabledReason = videoSubmitDisabledReason({
      mode: genMode,
      hasPromptText,
      referenceCounts: upstreamCounts,
      selectedModelOffline,
      selectedModelOfflineReason,
      mediaRejectionReason,
      promptContractReason,
      isGenerating,
    });
    const submitDisabled = !canSubmitVideoGeneration({
      mode: genMode,
      hasPromptText,
      referenceCounts: upstreamCounts,
      selectedModelOffline,
      mediaRejectionReason,
      promptContractReason,
      isGenerating,
    });

    const handleSubmit = useVideoGenerationSubmission({
      id, data, t, updateNodeData, submittingRef, generationQueueAbortRef,
      setAutoSubmitRetryTick, autoSubmitRetryTick, isGenerating, submitDisabled,
      capabilityVideoModel,
      selectedVideoModel, videoChannelEnabled, videoChannelDisabledReason,
      quality, effectiveAspectRatio, durationParameterEnabled, durationSec,
      durationBounds, durationOptions, generateAudio, prompt, upstreamTextJoined,
      genMode, referenceMedia, isDirectVideoModel, modelId, selectedVideoModelId,
      isSeedance20Model, sceneOptimize, isHappyHorseModel,
      isKacangKlingV2vModel, autoTailFrameReference, videoModelFamily, count,
      refreshHistory,
    });

    const hasMainlineContext = hasMainlineContexts(
      (data as { mainline_context?: unknown }).mainline_context,
    );

    const cardToneClass = canvasNodeFrameClass({
      mainline: hasMainlineContext,
    });

    const isUploading = Boolean(data.isUploading);
    const isEmptyVideoBody = !videoSource && !isUploading && !isGenerating && !hasGenerationError;
    const bodySurfaceClass = isEmptyVideoBody
      ? CANVAS_NODE_INPUT_SURFACE_CLASS
      : CANVAS_NODE_PANEL_SURFACE_CLASS;
    const bodyFrameClass = isEmptyVideoBody
      ? CANVAS_NODE_INPUT_BODY_FRAME_CLASS
      : cardToneClass;
    const showVideoOpsPanel =
      selected &&
      !isBoxSelecting &&
      !albumExpanded &&
      !isClipMode &&
      !subtitleEraseMode &&
      !data.referenceOnly &&
      // 视频高清节点用自己的 VideoUpscaleEditorOverlay 配置面板，不走常规生成面板。
      !data.isUpscaleNode;

    const handleCaptureFrame = useCallback(
      async (mode: VideoFrameCaptureMode) => {
        if (isCapturingFrame) return { ok: false, error: "frame capture already running" };
        if (!videoSource) return { ok: false, error: "video output is required" };
        const projectId = readUrl().project;
        if (!projectId) {
          console.error("[video-node] no project in URL");
          return { ok: false, error: "project is required" };
        }
        const src = videoSource;
        const liveEl = videoRef.current;
        const liveDuration =
          liveEl && Number.isFinite(liveEl.duration) ? liveEl.duration : null;
        const fallbackDurationSec =
          typeof data.durationMs === "number" ? data.durationMs / 1000 : null;
        const knownDuration = liveDuration ?? fallbackDurationSec;
        let seekSec = 0;
        if (mode === "first") {
          seekSec = 0;
        } else if (mode === "last") {
          seekSec =
            knownDuration != null
              ? Math.max(0, knownDuration - 0.05)
              : Number.MAX_SAFE_INTEGER;
        } else {
          seekSec =
            liveEl && Number.isFinite(liveEl.currentTime)
              ? liveEl.currentTime
              : 0;
        }

        setIsCapturingFrame(true);
        try {
          const blob = await captureVideoFrameBlob(src, seekSec);
          const filename = `frame-${mode}-${Date.now()}.png`;
          const file = new File([blob], filename, { type: "image/png" });
          const uploaded = await uploadFreezoneImage(
            projectId,
            file,
            filename,
          );
          const widthPx = data.widthPx;
          const heightPx = data.heightPx;
          const aspectForNode =
            widthPx && heightPx && widthPx > 0 && heightPx > 0
              ? `${widthPx}:${heightPx}`
              : data.aspectRatio || "16:9";
          const createdNodeId = addDerivedUploadNode(
            id,
            uploaded.url,
            aspectForNode,
            uploaded.url,
          );
          if (createdNodeId) {
            const titleKey =
              mode === "first"
                ? "node.videoNode.frame.titleFirst"
                : mode === "last"
                  ? "node.videoNode.frame.titleLast"
                  : "node.videoNode.frame.titleCurrent";
            updateNodeData(createdNodeId, {
              displayName: t(titleKey),
              captureMetadata: videoFrameCaptureMetadata(id, typeof data.videoUrl === 'string' ? data.videoUrl : src, mode, seekSec),
            });
            addEdge(id, createdNodeId);
            return { ok: true, createdNodeId, outputUrl: uploaded.url };
          }
          return { ok: false, error: "could not create output image node", outputUrl: uploaded.url };
        } catch (error) {
          console.error("[video-node] frame capture failed", error);
          return {
            ok: false,
            error: error instanceof Error ? error.message : String(error),
          };
        } finally {
          setIsCapturingFrame(false);
        }
      },
      [
        addDerivedUploadNode,
        addEdge,
        data.aspectRatio,
        data.durationMs,
        data.heightPx,
        data.widthPx,
        data.videoUrl,
        id,
        isCapturingFrame,
        t,
        updateNodeData,
        videoSource,
      ],
    );

    useEffect(() => {
      return canvasEventBus.subscribe(
        "video-node/capture-frame",
        ({ nodeId, mode, onComplete }) => {
          if (nodeId !== id) return;
          void handleCaptureFrame(mode).then((receipt) => onComplete?.({
            ...receipt,
            ok: receipt.ok,
            nodeId: id,
            mode,
            outputUrl: receipt.outputUrl ?? null,
            createdNodeId: receipt.createdNodeId ?? null,
            error: receipt.error ?? null,
          }));
        },
      );
    }, [handleCaptureFrame, id]);

    useEffect(() => {
      // 「节点能力」菜单与工具栏「剪辑 / 去字幕」共用这条通道：只翻模式位，真正的提交
      // 仍落在节点下方展开的 VideoClipPanel / SubtitleErasePanel 上。因此这里回执的
      // ok 表示「面板已展开」，不代表任务已提交 —— 与 capture-frame 的回执语义不同。
      return canvasEventBus.subscribe(
        "video-node/set-operation",
        ({ nodeId, operation, onComplete }) => {
          if (nodeId !== id) return;
          if (!videoSource) {
            onComplete?.({
              ok: false,
              nodeId: id,
              operation,
              error: "video output is required",
            });
            return;
          }
          if (operation === "clip") {
            updateNodeData(id, {
              isClipMode: true,
              subtitleEraseMode: null,
              subtitleEraseBox: null,
            });
          } else if (operation === "subtitle-smart") {
            updateNodeData(id, {
              isClipMode: false,
              subtitleEraseMode: "smart",
              subtitleEraseBox: null,
            });
          } else {
            updateNodeData(id, {
              isClipMode: false,
              subtitleEraseMode: "box",
              subtitleEraseBox: null,
            });
          }
          onComplete?.({ ok: true, nodeId: id, operation });
        },
      );
    }, [id, updateNodeData, videoSource]);

    return (
      <div
        ref={albumRootRef}
        className="group village-video-media-card relative h-full w-full overflow-visible transition-[width,height] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none"
        data-village-video-card={data.referenceOnly ? "reference" : "standard"}
        style={{ width: resolvedWidth, height: resolvedHeight }}
        onClick={() => setSelectedNode(id)}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
      >
        {/* 叠卡画册的卡片边：从主视频右侧探出（与图片节点同款），点卡边也能展开画册。 */}
        {hasAlbum && !albumExpanded && videoSource && (
          <>
            {Array.from({ length: Math.min(albumTotalSlots - 1, 3) }, (_, index) => {
              const step = index + 1;
              return (
                <div
                  key={`album-deck-${index}`}
                  role="button"
                  tabIndex={-1}
                  title="展开画册"
                  onClick={(event) => {
                    event.stopPropagation();
                    handleToggleAlbumExpanded();
                  }}
                  className="absolute cursor-pointer rounded-[var(--node-radius)] border border-white/[0.18] bg-gradient-to-b from-[#48484d] to-[#2d2d31] shadow-[0_4px_14px_rgba(0,0,0,0.4)]"
                  style={{
                    top: step * 7,
                    bottom: step * 7,
                    left: step * 6,
                    right: -step * 7,
                    transform: `rotate(${step * 1.1}deg)`,
                    transformOrigin: 'center right',
                    opacity: 1 - step * 0.18,
                  }}
                />
              );
            })}
          </>
        )}
        <Handle
          type="target"
          position={Position.Left}
          id="target"
          className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
        />
        <Handle
          type="source"
          position={Position.Right}
          id="source"
          className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
        />

        {/* 画册展开时隐藏浮动标题和分辨率角标——画册容器自带头部（与图片节点一致）。 */}
        {!albumExpanded && (
          <>
            <NodeHeader
              className={`${NODE_HEADER_FLOATING_POSITION_CLASS} village-video-media-header`}
              icon={<VideoIcon className="h-4 w-4" />}
              titleText={resolvedTitle}
              editable
              onTitleChange={(nextTitle) =>
                updateNodeData(id, { displayName: nextTitle })
              }
            />
            {videoSource &&
            hasMetadata &&
            !videoLoadError &&
            typeof data.widthPx === "number" &&
            typeof data.heightPx === "number" &&
            data.widthPx > 0 &&
            data.heightPx > 0 ? (
              <ResolutionHonestyBadge
                media={data.isUpscaleNode ? "video-upscale" : "video"}
                requestedTier={
                  data.isUpscaleNode
                    ? data.upscaleResolution ?? data.lastRequestedResolution
                    : data.lastRequestedResolution ??
                      qualityToResolution(quality)
                }
                actualWidth={data.widthPx}
                actualHeight={data.heightPx}
              />
            ) : null}
            {videoSource &&
            hasMetadata &&
            !videoLoadError &&
            data.aspectRatioMismatch === true &&
            data.actualAspectRatio ? (
              <div
                className="absolute -top-14 right-1 z-20 flex max-w-[min(100%,260px)] items-center gap-1 rounded-md border border-amber-400/35 bg-amber-950/80 px-2 py-0.5 text-[11px] font-medium tabular-nums text-amber-100/90 backdrop-blur-sm"
                role="status"
                aria-label={`请求比例 ${data.lastRequestedAspectRatio ?? data.aspectRatio}，实际比例 ${data.actualAspectRatio}`}
                title={`请求比例：${data.lastRequestedAspectRatio ?? data.aspectRatio}；实际比例：${data.actualAspectRatio}`}
              >
                <AlertTriangle className="h-3 w-3 shrink-0 text-amber-200/80" />
                <span className="truncate">
                  比例偏离：{data.lastRequestedAspectRatio ?? data.aspectRatio} → {data.actualAspectRatio}
                </span>
              </div>
            ) : null}
            {videoSource && durationHonesty?.isMismatch ? (
              <div
                className="absolute -top-14 left-1 z-20 flex max-w-[min(60%,260px)] items-center gap-1 rounded-md border border-amber-400/35 bg-amber-950/80 px-2 py-0.5 text-[11px] font-medium tabular-nums text-amber-100/90 backdrop-blur-sm"
                role="status"
                aria-label={`时长偏离：${durationHonesty.badgeLabel}`}
                title={durationHonesty.tooltip}
              >
                <AlertTriangle className="h-3 w-3 shrink-0 text-amber-200/80" />
                <span className="truncate">
                  时长偏离：{durationHonesty.badgeLabel}
                </span>
              </div>
            ) : null}
          </>
        )}
        <NodeContextBadges
          contexts={(data as { mainline_context?: unknown }).mainline_context}
        />

        <NodeResizeHandle
          minWidth={configuredAspectMin.minWidth}
          minHeight={configuredAspectMin.minHeight}
          maxWidth={MAX_WIDTH}
          maxHeight={MAX_HEIGHT}
          keepAspectRatio
        />

        <NodeRightsBadge
          nodeId={id}
          data={data as Record<string, unknown>}
          placement="top-right"
        />

        <div
          className={`relative flex h-full w-full items-center justify-center ${videoSource ? "overflow-hidden" : "overflow-visible"} rounded-[var(--node-radius)] border ${bodySurfaceClass} transition-colors ${bodyFrameClass} ${
            // 画册展开时藏起节点本体——半透明的画册容器盖不严，底下的视频会透出来。
            albumExpanded && hasAlbum ? "invisible" : ""
          }`}
        >
          {/* 生成/上传中优先显示 loading：原地重新生成时 videoUrl 仍是上一条结果，
              若不加这层 guard，旧视频会一直占位、isGenerating 分支永远到不了。
              失败时 isGenerating 归 false，旧视频自动复现（videoUrl 未被清空）。 */}
          {!isGenerating && !isUploading && videoSource && lowDetailZoom && !isNodeMediaActive(id) ? (
            lodStill ? (
              <img
                src={lodStill}
                alt=""
                className="h-full w-full object-contain"
                draggable={false}
                onClick={() => setSelectedNode(id)}
              />
            ) : (
              <div
                className="flex h-full w-full items-center justify-center bg-black/40"
                onClick={() => setSelectedNode(id)}
              >
                <VideoIcon className="h-1/4 max-h-10 w-1/4 max-w-10 text-white/25" />
              </div>
            )
          ) : !isGenerating && !isUploading && videoSource ? (
            <video
              ref={setVideoRef}
              src={videoPosterSource ?? undefined}
              className="h-full w-full object-contain"
              playsInline
              disablePictureInPicture
              disableRemotePlayback
              controlsList="nodownload noplaybackrate noremoteplayback"
              // A canvas can contain many completed videos. Metadata loading on
              // every mounted node creates needless decoder/network work while
              // panning; the selected node still gets the old eager behavior,
              // and play() loads an unselected node on demand.
              preload={videoNodePreload(selected)}
              onClick={() => {
                // 点击视频本体只负责选中节点 —— 播放/暂停统一交给左下角按钮。
                setSelectedNode(id);
              }}
              onLoadedMetadata={(event) => {
                const el = event.currentTarget;
                setHasMetadata(true);
                setVideoLoadError(false);
                if (el.videoWidth && el.videoHeight) {
                  // 只把视频真实像素记到 widthPx/heightPx；不要写回 aspectRatio。
                  // aspectRatio 仅保存用户选的比例预设（16:9 / auto…），否则
                  // chip 会显示成像素串(1248:704)，且会作为非法 aspect_ratio 带进
                  // 下一次生成请求。
                  updateNodeData(id, buildVideoMetadataPatch({
                    width: el.videoWidth,
                    height: el.videoHeight,
                    durationSeconds: el.duration,
                    requestedAspectRatio: data.lastRequestedAspectRatio,
                  }) as Partial<VideoNodeData>);
                }
              }}
              onError={() => {
                setHasMetadata(true);
                setVideoLoadError(true);
              }}
            />
          ) : isUploading ? (
            <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/85">
              <Loader2 className="h-7 w-7 animate-spin opacity-70" />
              <span className="px-4 text-center text-[12px] leading-6">
                {t("node.videoNode.uploading")}
              </span>
            </div>
          ) : isGenerating && generationPreviewUrl ? (
            // 生成进行中，但用户点了历史记录预览：临时播放那条历史视频，新视频
            // 仍在后台生成。顶部 pill 提示「生成中」，右上「返回」回到 loading。
            <div className="relative h-full w-full">
              <video
                src={resolveImageDisplayUrl(generationPreviewUrl)}
                className="h-full w-full object-contain"
                controls
                playsInline
                disablePictureInPicture
                disableRemotePlayback
                controlsList="nodownload noplaybackrate noremoteplayback"
                preload="metadata"
                onClick={(event) => event.stopPropagation()}
              />
              <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between gap-2 p-2">
                  <span className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 text-[11px] text-white/90 backdrop-blur">
                   <Loader2 className="h-3 w-3 animate-spin" />
                   {upstreamPreviewUrl ? "上游已生成，正在缓存…" : "新视频生成中…"}
                 </span>
                <button
                  type="button"
                  className="nodrag pointer-events-auto inline-flex items-center gap-1 rounded-full bg-black/60 px-2.5 py-1 text-[11px] text-white/90 backdrop-blur transition-colors hover:bg-black/75"
                  onClick={(event) => {
                    event.stopPropagation();
                    setHistoryPreviewUrl(null);
                  }}
                >
                  <XIcon className="h-3 w-3" />
                  返回
                </button>
              </div>
            </div>
          ) : isGenerating ? (
            <div className="relative h-full w-full">
              {data.previewImageUrl ? (
                <img
                  src={resolveImageDisplayUrl(data.previewImageUrl)}
                  alt=""
                  className="h-full w-full object-contain"
                  draggable={false}
                />
              ) : null}
              <NodeGenerationOverlay
                startedAt={data.generationStartedAt ?? null}
                progress={generationTask?.progress ?? null}
                hasBackground={Boolean(data.previewImageUrl)}
                onCancel={() => void cancelNodeTask()}
                cancelPending={isCancellingNodeTask}
              />
            </div>
          ) : hasGenerationError ? (
            <NodeGenerationErrorCard
              title="视频生成失败"
              message={generationError}
              details={generationErrorDetails}
              requestId={generationErrorRequestId}
              stage={generationErrorStage}
              suggestedAction={generationErrorSuggestedAction}
              recoverBusy={isRecovering}
              onRecover={canRecoverVideoTask ? () => void handleRecoverVideoTask() : undefined}
              retryBusy={isGenerating}
              retryDisabled={submitDisabled}
              retryDisabledReason={submitDisabledReason}
              onRetry={() => void handleSubmit()}
              onChangeModel={() => {
                setSelectedNode(id);
                setPanelExpanded(true);
              }}
              onDismiss={() => updateNodeData(id, { ...CLEARED_GENERATION_ERROR_PATCH })}
            />
          ) : data.isUpscaleNode ? (
            <div className="flex h-full w-full items-center justify-center px-6">
              <span className="text-center text-sm font-medium text-text-dark/78">
                {t("node.videoUpscale.placeholder")}
              </span>
            </div>
          ) : isConnected ? (
            // 已连线：不再显示文字 CTA，只在节点中间放一个图标（对齐 libtv）。
            <div className="village-video-empty-state is-connected flex h-full w-full items-center justify-center">
              <Play className="h-8 w-8 text-text-muted/42" />
            </div>
          ) : (
            <div className="village-video-empty-state flex h-full w-full items-center justify-center px-8">
              <div className="village-video-empty-copy flex min-h-0 flex-col items-center justify-center gap-3 py-4 text-center">
                <div className="village-video-empty-title">在下方配置参数生成高清视频</div>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    handleUploadClick();
                  }}
                  onPointerDown={(event) => event.stopPropagation()}
                  className="nodrag village-video-empty-upload"
                  title={t("node.videoNode.clickToUpload")}
                >
                  <UploadIcon className="h-3.5 w-3.5" />
                  <span>{t("node.videoNode.upload")}</span>
                </button>
              </div>
            </div>
          )}

          {videoSource && videoLoadError && !isGenerating && !isUploading && (
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 bg-bg-dark/70 px-4 text-center text-red-200">
              <AlertTriangle className="h-6 w-6 text-red-300" />
              <span className="text-[12px] font-medium">视频加载失败</span>
            </div>
          )}

          {videoSource && !hasMetadata && !isUploading && !isGenerating && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-bg-dark/40">
              <Loader2 className="h-6 w-6 animate-spin text-text-muted/70" />
            </div>
          )}

          {videoSource &&
            hasMetadata &&
            !videoLoadError &&
            !isGenerating &&
            !isUploading &&
            !subtitleEraseMode && (
              <VideoPlayerControls
                videoEl={videoEl}
                isCapturingFrame={isCapturingFrame}
                onCapture={handleCaptureFrame}
                onPlaybackIntent={handlePlaybackIntent}
              />
            )}

          {/* 画册数量徽标：hover 节点出现，hover 徽标箭头下探，点击展开画册。 */}
          {hasAlbum && !isGenerating && videoSource && (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                handleToggleAlbumExpanded();
              }}
              onPointerDown={(event) => event.stopPropagation()}
              title={`展开 ${albumTotalSlots} 条生成结果`}
              className="nodrag group/albumpill absolute right-2 top-2 z-10 hidden items-center gap-1 rounded-full bg-black/65 px-2.5 py-1 text-[12px] font-medium tabular-nums text-white shadow-lg backdrop-blur-sm transition-colors hover:bg-black/85 group-hover:inline-flex"
            >
              {albumPendingCount > 0
                ? `${albumUrls.length}/${albumPendingTotal}`
                : albumUrls.length}
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform duration-200 ${
                  albumExpanded
                    ? 'rotate-180 group-hover/albumpill:-translate-y-[2px]'
                    : 'group-hover/albumpill:translate-y-[2px]'
                }`}
              />
            </button>
          )}

          {videoSource && subtitleEraseMode === "box" && (
            <SubtitleEraseBoxOverlay
              box={subtitleEraseBox}
              drag={eraseDrag}
              disabled={isErasing}
              getDisplayedRect={getDisplayedVideoRect}
              onDragStart={(start) => setEraseDrag(start)}
              onDragMove={(next) =>
                setEraseDrag((prev) =>
                  prev ? { ...prev, x1: next.x1, y1: next.y1 } : prev,
                )
              }
              onDragEnd={(final) => {
                setEraseDrag(null);
                if (!final) return;
                updateNodeData(id, { subtitleEraseBox: final });
              }}
            />
          )}
        </div>

        {/* 展开的画册宫格：与图片节点同构——「组」式轮廓 + 2 列宫格；点视频设为
            主视频并收拢；hover 出现「应用到画布」+ 下载；按住可拖动整个节点。 */}
        {albumExpanded && hasAlbum && (
          <div
            className="nowheel absolute -left-3 -top-3 z-[80] cursor-grab rounded-2xl border border-white/15 bg-white/[0.045] p-3 shadow-[0_16px_48px_rgba(0,0,0,0.4)] backdrop-blur-[2px] active:cursor-grabbing"
            style={{ width: resolvedWidth * 2 + 12 + 24 }}
            onClick={(event) => event.stopPropagation()}
            onPointerDownCapture={(event) => {
              albumPointerDownPosRef.current = { x: event.clientX, y: event.clientY };
            }}
          >
            <div className="mb-2 flex items-center gap-1.5 px-1 text-[12px] font-medium text-white/60">
              <VideoIcon className="h-3.5 w-3.5 text-white/45" />
              画册 · {albumTotalSlots} 条
            </div>
            <div className="grid grid-cols-2 gap-3">
              {albumUrls.map((url, index) => {
                const isMain = url === data.videoUrl;
                return (
                  <div
                    key={`album-cell-${index}`}
                    role="button"
                    tabIndex={-1}
                    title="点击设为主视频"
                    onClick={(event) => {
                      event.stopPropagation();
                      // 拖动画册（移动节点）后松手补发的 click 不算选主视频。
                      const start = albumPointerDownPosRef.current;
                      if (
                        start
                        && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5
                      ) {
                        return;
                      }
                      handleSetAlbumMainVideo(url);
                    }}
                    className={`group/albumcell relative cursor-pointer overflow-hidden rounded-[var(--node-radius)] border bg-[#1b1b1d] shadow-[0_12px_32px_rgba(0,0,0,0.45)] transition-colors ${
                      isMain
                        ? 'border-accent/80 ring-2 ring-accent/40'
                        : 'border-white/12 hover:border-white/35'
                    }`}
                    style={{ width: resolvedWidth, height: resolvedHeight }}
                  >
                    <video
                      src={resolveImageDisplayUrl(url)}
                      muted
                      playsInline
                      disablePictureInPicture
                      disableRemotePlayback
                      controlsList="nodownload noplaybackrate noremoteplayback"
                      preload="metadata"
                      className="h-full w-full object-cover"
                      onMouseEnter={(event) => {
                        void event.currentTarget.play().catch(() => undefined);
                      }}
                      onMouseLeave={(event) => {
                        event.currentTarget.pause();
                        event.currentTarget.currentTime = 0;
                      }}
                    />
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        handleApplyAlbumVideoToCanvas(url);
                      }}
                      title="把这条视频作为独立视频节点放到画布上"
                      className="nodrag absolute left-2 top-2 z-10 hidden h-7 items-center gap-1 rounded-md bg-black/70 px-2.5 text-[12px] font-medium text-white backdrop-blur-sm transition-colors hover:bg-black/90 group-hover/albumcell:inline-flex"
                    >
                      <UploadIcon className="h-3.5 w-3.5" />
                      应用到画布
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleDownloadAlbumVideo(url, index);
                      }}
                      title="下载这条视频"
                      className="nodrag absolute right-2 top-2 z-10 hidden h-7 w-7 items-center justify-center rounded-full bg-black/70 text-white backdrop-blur-sm transition-colors hover:bg-black/90 group-hover/albumcell:inline-flex"
                    >
                      <Download className="h-3.5 w-3.5" />
                    </button>
                    {isMain && (
                      <span className="absolute bottom-2 left-2 z-10 rounded-md bg-black/65 px-2 py-0.5 text-[11px] font-medium text-white backdrop-blur-sm">
                        主视频
                      </span>
                    )}
                  </div>
                );
              })}
              {/* 还在生成中的槽位：占位骨架，完成一条替换一条。 */}
              {Array.from({ length: albumPendingCount }, (_, index) => (
                <div
                  key={`album-pending-${index}`}
                  className="relative flex items-center justify-center overflow-hidden rounded-[var(--node-radius)] border border-white/10 bg-[#1b1b1d] shadow-[0_12px_32px_rgba(0,0,0,0.45)]"
                  style={{ width: resolvedWidth, height: resolvedHeight }}
                >
                  <div className="flex flex-col items-center gap-2 text-text-muted/70">
                    <Loader2 className="h-6 w-6 animate-spin" />
                    <span className="text-[12px]">生成中…</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {isClipMode && videoSource && (
          <div
            className="absolute left-0 right-0 z-10 flex flex-col gap-1"
            style={{ top: `calc(100% + ${OPERATIONS_PANEL_GAP}px)` }}
          >
            <VideoClipPanel
              videoUrl={videoSource}
              durationMs={durationMs}
              clipStartMs={clipStartMs}
              clipEndMs={clipEndMs}
              isSubmitting={isComposingClip}
              onChange={(patch) => updateNodeData(id, patch)}
              onExit={() => {
                if (isComposingClip) return;
                setClipError(null);
                updateNodeData(id, { isClipMode: false });
              }}
              onSubmit={(start, end) => {
                void handleClipSubmit(start, end);
              }}
            />
            {clipError && (
              <div className="rounded-md bg-red-500/15 px-3 py-1.5 text-[11px] text-red-300 break-words [overflow-wrap:anywhere]">
                剪辑失败：{clipError}
              </div>
            )}
          </div>
        )}

        {showVideoOpsPanel && (
            <OperationPanelShell
              expanded={panelExpanded}
              onCollapse={() => setPanelExpanded(false)}
              inlineClassName={`nodrag absolute z-30 flex flex-col rounded-[var(--node-radius)] ${CANVAS_NODE_OPS_PANEL_CLASS}`}
              inlineStyle={{
                top: `calc(100% + ${OPERATIONS_PANEL_GAP}px)`,
                left: -panelOverhang,
                right: -panelOverhang,
                height: panelHeight,
              }}
              modalStyle={{
                width: `min(${OPERATIONS_PANEL_EXPANDED_WIDTH}px, 92vw)`,
                height: `min(${OPERATIONS_PANEL_EXPANDED_HEIGHT}px, 86vh)`,
              }}
            >
              <PanelExpandButton
                expanded={panelExpanded}
                onToggle={() => setPanelExpanded((v) => !v)}
                className="absolute right-2 top-2 z-20"
              />
              <div className="shrink-0 border-b border-white/[0.08] px-3 pb-2 pr-10 pt-3">
                <div className="flex min-w-0 items-center justify-between gap-3 overflow-x-auto">
                <div className="flex shrink-0 items-center gap-2">
                  <CameraMovementChip
                    templates={cameraTemplates}
                    isLoading={cameraTemplatesLoading}
                    selectedId={cameraMovementId}
                    // 点卡片＝把该条正文写进提示词的运镜段；清除＝还原脚本原文。
                    // 运镜不再是节点上的 id 字段，所以这里改的是 prompt 本身。
                    onChange={(nextId) => {
                      const nextPreset = nextId
                        ? findCameraMovementPreset(cameraTemplates, nextId)
                        : null;
                      if (nextId && !nextPreset) return;
                      updateNodeData(id, {
                        prompt: nextPreset
                          ? setCameraDirection(
                              prompt,
                              nextPreset.promptFragment || nextPreset.label,
                            )
                          : resetCameraDirection(prompt, cameraOriginalPrompt),
                      });
                    }}
                  />
                  <button
                    type="button"
                    title={`调用文字模型并按 ${selectedVideoModel?.label ?? modelId} 的规则优化`}
                    disabled={isOptimizingPrompt || isGenerating || prompt.trim().length === 0}
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleOptimizePrompt();
                    }}
                    className={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} village-prompt-optimization-trigger`}
                  >
                    {isOptimizingPrompt ? (
                      <Loader2 className={`${NODE_TEXT_CONTROL_ICON_CLASS} shrink-0 animate-spin`} />
                    ) : (
                      <WandSparkles className={`${NODE_TEXT_CONTROL_ICON_CLASS} shrink-0`} />
                    )}
                    <span className="whitespace-nowrap">
                      {isOptimizingPrompt
                        ? promptOptimizationProgressLabel(promptOptimizationElapsed)
                        : "优化提示词"}
                    </span>
                  </button>
                  <CharacterLibraryChip
                    onOpen={() => setIsCharacterLibraryOpen(true)}
                  />
                  <CanvasReferencePickChip
                    nodeId={id}
                    nodeType={CANVAS_NODE_TYPES.video}
                  />
                  <ExternalAssetChip
                    onOpen={() => externalAssetInputRef.current?.click()}
                  />
                </div>
                <div className="ml-3 flex shrink-0 items-center gap-3">
                  <GenModeSelect
                    value={genMode}
                    modelId={capabilityVideoModel?.apiModel ?? capabilityVideoModel?.id ?? modelId}
                    modelFamily={videoModelFamily}
                    supportedModes={capabilityVideoModel?.supportedModes as VideoGenMode[] | undefined}
                    // HappyHorse 的可选模式由上游节点类型（含未填图的空节点）决定，
                    // 其余模型仍按已解析素材 URL 计数。
                    upstreamCounts={
                      isHappyHorseModel ? upstreamTypeCounts : upstreamCounts
                    }
                    onChange={(nextMode) => updateNodeData(id, { genMode: nextMode })}
                  />
                  <NodeContextPromptPaletteButton
                    nodeId={id}
                    onInsert={insertContextPaletteEntry}
                  />
                  {upstreamTextContents.map((content) => (
                    <ReferenceTextChip
                      key={`upstream-text-${content.nodeId}`}
                      nodeId={content.nodeId}
                      text={content.text ?? ""}
                      sourceLabel={content.displayName ?? content.nodeType}
                      onDetach={handleDetachUpstream}
                    />
                  ))}
                </div>
                </div>
                <div className="mt-2 flex min-h-9 flex-wrap items-center gap-x-2 gap-y-1.5">
                  <span className="shrink-0 text-[11px] font-medium text-text-muted/85">参考素材</span>
                {referenceMedia.length > 0 ? (
                  <ReferenceMediaRow
                    items={referenceMediaCapInfo}
                    limits={
                      capabilityVideoModel?.referenceLimits?.[genMode]
                      ?? REFERENCE_CAPS_BY_MODE[genMode]
                    }
                    genMode={genMode}
                    onFocus={(nodeId) => setSelectedNode(nodeId)}
                    onDetach={handleDetachUpstream}
                    onReorder={(ids) =>
                      updateNodeData(id, { referenceOrder: ids })
                    }
                    onInsert={(mentionName) => handleInsertReference(mentionName)}
                  />
                ) : (
                  <span className="text-[11px] text-text-muted/60">添加图片、视频或画布素材作为参考</span>
                )}
                </div>
              </div>

              <PromptMentionEditor
                ref={promptEditorRef}
                value={promptDraft}
                onChange={(next) => {
                  setPromptDraft(next);
                  if (!isComposingRef.current) {
                    updateNodeData(id, { prompt: next });
                  }
                }}
                onCompositionStart={() => {
                  isComposingRef.current = true;
                }}
                onCompositionEnd={(next) => {
                  isComposingRef.current = false;
                  setPromptDraft(next);
                  updateNodeData(id, { prompt: next });
                }}
                onKeyDown={(event) => event.stopPropagation()}
                candidates={mentionCandidates}
                placeholder={
                  upstreamTextJoined.length > 0
                    ? "上游内容已自动接入，可继续补充提示词…"
                    : t("node.videoNode.placeholder")
                }
                className={`nodrag nowheel min-h-0 w-full flex-1 overflow-y-auto whitespace-pre-wrap break-words border-none bg-transparent px-3 py-2 text-sm leading-6 text-text-dark outline-none ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS}`}
              />

              {dialogueTiming && (
                <p
                  role="status"
                  className={`shrink-0 px-3 pb-1 text-[11px] leading-4 ${
                    dialogueTiming.hardMinimumSeconds > durationSec
                      ? "text-amber-200/90"
                      : "text-text-muted/75"
                  }`}
                >
                  台词约 {dialogueTiming.speechUnitCount} 字词，当前时长 {durationSec} 秒；
                  按每秒 6 字词至少约 {dialogueTiming.hardMinimumSeconds} 秒，
                  按每秒 4 字词建议约 {dialogueTiming.comfortableSeconds} 秒。
                </p>
              )}

              {promptOptimization && (
                <PromptOptimizationPreview
                  originalPrompt={promptOptimization.originalPrompt}
                  result={promptOptimization.result}
                  targetModelLabel={promptOptimization.request.targetModelLabel ?? promptOptimization.request.targetModelId}
                  stale={promptOptimization.contextSignature !== promptOptimizationContextSignature}
                  rerunningMode={promptOptimizationRerunningMode}
                  onReoptimize={handleReoptimizePrompt}
                  onClose={() => setPromptOptimization(null)}
                  onApply={applyPromptOptimization}
                />
              )}

              <div className="flex shrink-0 items-center justify-between gap-2 border-t border-white/[0.08] px-3 py-2">
                <div className="flex min-w-0 items-center gap-2">
                  <ProviderModelPicker
                    selectedModelId={modelId}
                    onChange={(nextModelId) => {
                      updateNodeData(id, {
                        model: nextModelId,
                      });
                      // 记住这次选择，后续新建的视频节点将继承它。
                      writeLastVideoModel(nextModelId);
                    }}
                    domain="video"
                    popoverPlacement="top"
                    getOptionDisabledReason={(model) => {
                      if (model.disabled === true || model.enabled === false) {
                        return (
                          model.disabledReason?.trim() ||
                          videoChannelDisabledReason?.trim() ||
                          "视频渠道未接通"
                        );
                      }
                      return videoReferenceDisabledReason(
                        model.family ??
                          compileVideoModelFamily(model.apiModel ?? model.id),
                        upstreamCounts,
                      );
                    }}
                  />
                  <VideoConfigChip
                    aspectRatio={aspectRatio}
                    aspectRatioOptions={aspectRatioOptions}
                    resolutionOptions={resolutionOptions}
                    quality={quality}
                    advertisedQualityOptions={advertisedQualityOptions}
                    rejectedQualityOptions={rejectedQualityOptions}
                    runtimeCapabilityNote={capabilityVideoModel?.runtimeCapabilityNote}
                    durationSec={durationSec}
                    durationBounds={durationBounds}
                    durationOptions={durationOptions}
                    durationParameterEnabled={durationParameterEnabled}
                    sceneOptimize={sceneOptimize}
                    sceneOptimizeOptions={sceneOptimizeOptions}
                    generateAudio={generateAudio}
                    nativeAudio={capabilityVideoModel?.nativeAudio ?? "optional"}
                    onGenerateAudioChange={(next) =>
                      updateNodeData(id, {
                        generateAudio: next,
                        generateAudioUserSet: true,
                      })
                    }
                    onChange={(patch) => updateNodeData(id, patch)}
                  />
                  <VideoAdvancedParametersChip
                    parameters={capabilityVideoModel?.parameters ?? []}
                    opaque={capabilityVideoModel?.opaque ?? []}
                    values={data.parameters ?? {}}
                    onChange={(parameters) => updateNodeData(id, { parameters })}
                  />
                  <CountPicker
                    value={count}
                    onChange={(nextCount) =>
                      updateNodeData(id, { count: nextCount })
                    }
                  />
                  <button
                    type="button"
                    title="翻译提示词（中英文互译）"
                    disabled={
                      isTranslatingPrompt ||
                      isGenerating ||
                      prompt.trim().length === 0
                    }
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleTranslatePrompt();
                    }}
                    className={`${NODE_INLINE_ICON_BUTTON_CLASS} ${
                      isTranslatingPrompt
                        ? NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS
                        : ""
                    }`}
                  >
                    {isTranslatingPrompt ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Languages className="h-4 w-4" />
                    )}
                  </button>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <CreditCostPill
                    display={totalCreditCostDisplay}
                    disabled={submitDisabled}
                    className={NODE_CREDIT_PILL_FLAT_CLASS}
                  />
                  <button
                    type="button"
                    disabled={submitDisabled}
                    title={submitDisabledReason ?? t("node.videoNode.submit")}
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleSubmit();
                    }}
                    className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
                      submitDisabled
                        ? NODE_GENERATE_BUTTON_DISABLED_CLASS
                        : NODE_GENERATE_BUTTON_ENABLED_CLASS
                    }`}
                  >
                    <ArrowUp className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </OperationPanelShell>
          )}

        {selected &&
          !isBoxSelecting &&
          !albumExpanded &&
          !isClipMode &&
          !subtitleEraseMode &&
          !data.referenceOnly &&
          hasCompletedHistoryRecords(historyRecords) && (
            <NodePanelZoomAnchor>
              <div
                className={`nodrag pointer-events-auto absolute z-[300] rounded-[var(--node-radius)] ${CANVAS_NODE_OPS_PANEL_CLASS} ${NODE_OPS_PANEL_ENTER_CLASS} px-3 py-2`}
                style={{
                  top: `calc(100% + ${OPERATIONS_PANEL_GAP * 2 + panelHeight}px)`,
                  left: -panelOverhang,
                  right: -panelOverhang,
                }}
                onClick={(event) => event.stopPropagation()}
              >
              <NodeGenerationHistory
                records={historyRecords}
                isLoading={historyLoading}
                onRestore={handleRestoreHistory}
                onRefresh={() => void refreshHistory()}
                isActive={(record) => {
                  const url = historyRecordOutputUrl(record);
                  if (!url) return false;
                  // 预览态下高亮正在预览的历史条，否则高亮当前主视频。
                  if (isGenerating && historyPreviewUrl) {
                    return url === historyPreviewUrl;
                  }
                  return url === data.videoUrl;
                }}
              />
              </div>
            </NodePanelZoomAnchor>
          )}

        {subtitleEraseMode && (
          <div
            className="nodrag absolute left-0 right-0 z-10 flex justify-center"
            style={{ top: `calc(100% + ${OPERATIONS_PANEL_GAP}px)` }}
            onClick={(event) => event.stopPropagation()}
          >
            <SubtitleEraseOpsPanel
              mode={subtitleEraseMode}
              isErasing={isErasing}
              hasBox={!!subtitleEraseBox}
              onExit={handleEraseExit}
              onResetBox={() => updateNodeData(id, { subtitleEraseBox: null })}
              onSubmit={handleEraseSubmit}
            />
          </div>
        )}

        <input
          ref={inputRef}
          type="file"
          accept={VIDEO_FILE_ACCEPT}
          className="hidden"
          onChange={handleFileChange}
        />
        <input
          ref={externalAssetInputRef}
          type="file"
          multiple
          accept={`image/*,${VIDEO_FILE_ACCEPT},audio/*`}
          className="hidden"
          onChange={handleExternalAssetFiles}
        />

        <AssetLibraryModal
          open={isCharacterLibraryOpen}
          project={readUrl().project ?? null}
          onClose={() => setIsCharacterLibraryOpen(false)}
          onConfirm={(selections) =>
            spawnCharacterLibraryReferences(selections)
          }
        />
      </div>
    );
  },
  areVideoNodePropsEqual,
);

VideoNode.displayName = "VideoNode";
