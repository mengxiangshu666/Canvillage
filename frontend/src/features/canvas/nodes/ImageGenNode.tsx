// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import {
  ArrowUp,
  ChevronDown,
  Download,
  Image as ImageIcon,
  Images,
  Languages,
  Library,
  Loader2,
  Upload,
  WandSparkles,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  CANVAS_NODE_TYPES,
  type ImageGenCameraSelection,
  type ImageGenCount,
  type ImageGenNodeData,
  type ImageQuality,
  type ImageSize,
} from '@/features/canvas/domain/canvasNodes';
import {
  resolveImageDisplayUrl,
  snapToAllowedAspectRatio,
  withImageCacheBust,
} from '@/features/canvas/application/imageData';
import {
  aspectRatioFromImageDimensions,
  resolveResizeMinConstraintsByAspect,
  resolveMinEdgeFittedSize,
  shouldForceNaturalImageSize,
} from '@/features/canvas/application/imageNodeSizing';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import {
  isSystemManagedNodeData,
  mainlineNodeVisualState,
  nodeMainlineFlags,
} from '@/features/canvas/domain/mainlineNodeFlags';
import {
  NodeHeader,
  NODE_HEADER_FLOATING_POSITION_CLASS,
} from '@/features/canvas/ui/NodeHeader';
import { NodeResizeHandle } from '@/features/canvas/ui/NodeResizeHandle';
import { PanelExpandButton } from '@/features/canvas/ui/PanelExpandButton';
import {
  NODE_OPS_PANEL_ENTER_CLASS,
  NodePanelZoomAnchor,
  OperationPanelShell,
} from '@/features/canvas/ui/OperationPanelShell';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import { NodeGenerationErrorCard } from '@/features/canvas/ui/NodeGenerationErrorCard';
import { ResolutionHonestyBadge } from '@/features/canvas/ui/ResolutionHonestyBadge';
import { CanvasNodeImage } from '@/features/canvas/ui/CanvasNodeImage';
import {
  setAlbumPendingTotal,
  useAlbumPendingTotal,
} from '@/features/canvas/nodes/shared/albumPendingTotals';
import { resolveImageAutoSubmitDecision } from '@/features/canvas/nodes/image-auto-submit';
import { downloadUrlAsFile } from '@/lib/browserDownload';
import {
  CANVAS_NODE_INPUT_BODY_FRAME_CLASS,
  CANVAS_NODE_INPUT_PLACEHOLDER_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  CANVAS_NODE_OPS_PANEL_CLASS,
  CANVAS_NODE_PANEL_SURFACE_CLASS,
  canvasNodeFrameClass,
} from '@/features/canvas/ui/nodeFrameStyles';
import { useCanvasStore, useIsBoxSelecting } from '@/stores/canvasStore';
import { selectConnectedCanvasEdges } from '@/features/canvas/application/canvasEdgeIndex';
import { getFreezoneCanvasMetadata } from '@/features/freezone/canvasMetadataContext';
import { buildGeneratedRightsPatch } from '@/features/canvas/domain/nodeRights';
import {
  fetchFreezoneJobResultWithRetry,
  fetchFreezonePromptOptimizeResult,
  fetchFreezoneTextTranslateResult,
  submitFreezoneGen,
  submitFreezonePromptOptimize,
  submitFreezoneTextTranslate,
  uploadFreezoneImage,
  type FreezonePromptOptimizePayload,
  type FreezonePromptOptimizeResult,
} from '@/api/ops';
import {
  uploadAndAutoCommitSelectedBackgroundCandidate,
} from '@/features/canvas/application/selectedBackgroundSlot';
import { canvasEventBus } from '@/features/canvas/application/canvasServices';
import { getBeatDirectorStageManifest } from '@/api/viewerManifests';
import { areCanvasNodePropsEqual } from './videoNodeRenderProps';
import { BackgroundCropperDialog } from '@/features/canvas/ui/BackgroundCropperDialog';
import {
  ThreeDDirectorDialog,
  type ThreeDDirectorCaptureMeta,
} from '@/features/viewer-kit/three-d/ThreeDDirectorDialog';
import type { DirectorStageManifest } from '@/features/viewer-kit/three-d/directorManifest';
import { awaitTaskCompletion } from '@/api/tasks';
import { awaitFreezoneJobMediaResult } from '@/features/canvas/application/awaitFreezoneJobMediaResult';
import {
  cancelSubmittedTaskIfAborted,
  registerNodeGenerationTask,
  useCancelNodeGeneration,
} from '@/features/canvas/application/useCancelNodeGeneration';
import { backendErrorToastMessage } from '@/lib/api-errors';
import { readUrl } from '@/lib/url-params';
import {
  ProviderModelPicker,
} from '@/features/canvas/ui/ProviderModelPicker';
import {
  imageEditModelDisabledReason,
  isLiveModel,
  selectLiveModel,
} from '@/features/canvas/domain/videoModelSelection';
import {
  runtimeImageModelFromOption,
  UNCONFIGURED_IMAGE_MODEL,
} from '@/features/canvas/models/runtimeImageModels';
import { buildImageAdvancedSettings } from '@/features/canvas/domain/imageAdvancedSettings';
import {
  isValidImageAspectRatio,
  isValidImageSize,
} from '@/features/canvas/models/imageCapabilityValues';
import {
  extractRequestId,
  resolveGenerationErrorDiagnostics,
} from '@/features/canvas/application/generationErrorReport';
import { useFreezoneImageModels } from '@/features/canvas/hooks/useFreezoneImageModels';
import {
  resolveDirectCanvasModelId,
  useDirectModelCatalog,
} from '@/features/canvas/hooks/useDirectModelCatalog';
import { useNodeGenerationHistory } from '@/features/canvas/hooks/useNodeGenerationHistory';
import { ReferenceTextChip } from '@/features/canvas/nodes/shared/ReferenceTextChip';
import { CanvasReferencePickChip } from '@/features/canvas/nodes/shared/CanvasReferencePickChip';
import {
  AssetLibraryModal,
  type AssetLibrarySelection,
} from '@/features/canvas/ui/AssetLibraryModal';
import {
  NodeGenerationHistory,
  hasCompletedHistoryRecords,
  historyRecordOutputUrl,
} from '@/features/canvas/ui/NodeGenerationHistory';
import {
  describeCameraSelection,
} from '@/features/canvas/nodes/CameraPickerPopover';
import {
  buildImageGenerationSuccessPatch,
  CLEARED_GENERATION_TASK_PATCH,
  CLEARED_GENERATION_ERROR_PATCH,
  isTaskCancelledError,
  isStaleGenerationTask,
  shouldWriteGenerationError,
} from '@/features/canvas/application/generationTaskArbitration';
import {
  GENERATION_CONCURRENCY_DEFAULT,
  clampGenerationBatchCount,
  clearGenerationIntent,
  markGenerationIntent,
  runGenerationQueue,
  withGlobalGenerationSlot,
} from '@/features/canvas/application/generationConcurrency';
import {
  UPSTREAM_GATE_RETRY_MS,
  upstreamGenerationGate,
} from '@/features/canvas/application/generationDependencies';
import { useFreezoneCameraOptions } from '@/features/canvas/hooks/useFreezoneCameraOptions';
import { describeStyleSelection } from '@/features/canvas/nodes/StylePickerPopover';
import { useFreezoneStyleTemplates } from '@/features/canvas/hooks/useFreezoneStyleTemplates';
import { joinUpstreamText } from '@/features/canvas/application/graphContentResolver';
import { useUpstreamContents } from '@/features/canvas/application/useUpstreamGraph';
import { useNodeGenerationTaskState } from '@/features/canvas/application/useNodeGenerationTaskState';
import {
  PromptMentionEditor,
  type MentionCandidate,
  type PromptMentionEditorHandle,
} from '@/features/canvas/nodes/PromptMentionEditor';
import { CandidateBindingBadges } from '@/features/freezone/context/NodeContextBadges';
import {
  collectCandidateBindingsForNode,
} from '@/features/freezone/context/mainlineContext';
import {
  PromptOptimizationPreview,
  type PromptOptimizationResearchMode,
} from '@/features/freezone/PromptOptimizationPreview';
import {
  promptOptimizationProgressLabel,
  usePromptOptimizationElapsed,
} from '@/features/freezone/usePromptOptimizationElapsed';
import { toast } from 'sonner';
import { useGenerationCreditCost } from '@/lib/queries/generation-credit-cost';
import { CreditCostPill, formatCreditCost } from '@/components/credits/credit-visual';
import {
  NODE_CREDIT_PILL_FLAT_CLASS,
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
  NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS,
  NODE_INLINE_ICON_BUTTON_CLASS,
  NODE_REFERENCE_MEDIA_CHIP_CLASS,
  NODE_REFERENCE_MEDIA_DETACH_CLASS,
  NODE_TEXT_CONTROL_ICON_CLASS,
  NODE_TEXT_CONTROL_TRIGGER_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import {
  NODE_SIDE_ACTION_BUTTON_CLASS,
  NODE_SIDE_ACTION_ICON_CLASS,
  NodeSideActionRail,
} from '@/features/canvas/ui/NodeSideActionRail';
import { NodeContextPromptPaletteButton } from '@/features/canvas/nodes/ContextPromptPaletteButton';
import {
  contextPromptPaletteInsertionText,
  type ContextPromptPaletteEntry,
} from '@/features/canvas/nodes/contextPromptPalette';
import { hasImageGenPromptOverride } from '@/features/canvas/nodes/imageGenPrompt';
import {
  normalizeReferenceUrl,
  orderedReferenceUrlsWithOwnFirst,
} from '@/features/canvas/nodes/referenceOrdering';
import { useReferenceMentionSync } from '@/features/canvas/nodes/useReferenceMentionSync';
import {
  referenceInsertText,
} from '@/features/canvas/nodes/shared/referenceStripBadges';
import { ReferenceChipNumberBadge } from '@/features/canvas/nodes/shared/ReferenceStripBadge';
import { mergePromptOptimizationLockedConstraints } from '@/features/freezone/promptOptimizationPayload';
import {
  AdvancedParamsChip,
  AspectSizeChip,
  CameraChip,
  CountSelect,
  StyleChip,
} from './ImageGenParameterChips';

type ImageGenNodeProps = NodeProps & {
  id: string;
  data: ImageGenNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 580;
const DEFAULT_HEIGHT = 360;
const MIN_WIDTH = 480;
const MIN_HEIGHT = 260;
const MAX_WIDTH = 1100;
const MAX_HEIGHT = 1000;

const OPERATIONS_PANEL_HEIGHT = 286;
const OPERATIONS_PANEL_GAP = 12;
const OPERATIONS_PANEL_MIN_WIDTH = 720;
// 「放大」后的操作区尺寸：给提示词编辑区更舒适的高度与宽度。
const OPERATIONS_PANEL_EXPANDED_HEIGHT = 560;
const OPERATIONS_PANEL_EXPANDED_MIN_WIDTH = 960;

const SELECTED_BACKGROUND_CROP_ASPECT_OPTIONS = ['2:3', '16:9'] as const;

const DEFAULT_IMAGE_QUALITY: ImageQuality = 'medium';

export const ImageGenNode = memo(({ id, data, selected, width, height }: ImageGenNodeProps) => {
  const { t } = useTranslation();
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const isBoxSelecting = useIsBoxSelecting();
  // 顶部工具栏打开了二级功能浮层（全景 / 多角度 / 打光 等）时，浮层会在节点下方
  // 展开自己的操作区。此时隐藏本节点底部的生成/历史面板，让位给浮层，避免两块
  // 操作区重叠。
  const hasActiveOverlay = useCanvasStore((state) => state.activeOverlayNodeId === id);
  const setActiveOverlayNodeId = useCanvasStore((state) => state.setActiveOverlayNodeId);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const updateNodeSize = useCanvasStore((state) => state.updateNodeSize);
  const deleteEdge = useCanvasStore((state) => state.deleteEdge);
  const addNodeAction = useCanvasStore((state) => state.addNode);
  const addEdgeAction = useCanvasStore((state) => state.addEdge);

  // Local prompt buffer keeps the textarea's React `value` in lockstep with
  // user input even during IME composition (中文输入法). Committing to the
  // Zustand store on every keystroke triggers a global re-render that can
  // clobber the in-flight composition; the buffer absorbs that race.
  const externalPrompt = typeof data.prompt === 'string' ? data.prompt : '';
  const [promptDraft, setPromptDraft] = useState(externalPrompt);
  const isComposingRef = useRef(false);
  const hasUserEditedPromptRef = useRef(false);
  const submittingRef = useRef(false);
  const generationQueueAbortRef = useRef<AbortController | null>(null);
  const cancelLocalGeneration = useCallback((reason: Error) => {
    generationQueueAbortRef.current?.abort(reason);
  }, []);
  const {
    cancel: cancelNodeTask,
    isCancelling: isCancellingNodeTask,
  } = useCancelNodeGeneration(id, data, cancelLocalGeneration);
  // React Flow can temporarily unmount nodes outside the viewport.  An image
  // task is durable on the server, so that lifecycle event must not cancel a
  // paid/requested generation; explicit cancellation still goes through the
  // node's cancel action and aborts this controller.
  useEffect(() => {
    if (isComposingRef.current) return;
    setPromptDraft(externalPrompt);
  }, [externalPrompt]);
  const prompt = promptDraft;
  const promptEditorRef = useRef<PromptMentionEditorHandle>(null);
  const storedRequestAspectRatio = typeof data.requestAspectRatio === 'string'
    ? data.requestAspectRatio
    : '';
  const rawAspectRatio = storedRequestAspectRatio && storedRequestAspectRatio !== 'auto'
    ? storedRequestAspectRatio
    : typeof data.aspectRatio === 'string' && data.aspectRatio
      ? data.aspectRatio
      : '';
  const rawSize = typeof data.size === 'string' && data.size ? data.size : '';
  const count = (data.count ?? 1) as ImageGenCount;
  const autoCommitOnGenerate = data.autoCommitOnGenerate === true;
  const canAutoCommitOnGenerate =
    autoCommitOnGenerate &&
    isSystemManagedNodeData(data);
  const effectiveCount = canAutoCommitOnGenerate ? 1 : count;
  const { isGenerating, task: generationTask } = useNodeGenerationTaskState(data);
  const generationError =
    typeof data.generationError === 'string' && data.generationError.length > 0
      ? data.generationError
      : null;
  const generationErrorDetails =
    typeof data.generationErrorDetails === 'string' && data.generationErrorDetails.length > 0
      ? data.generationErrorDetails
      : null;
  const generationErrorRequestId =
    typeof data.generationErrorRequestId === 'string' && data.generationErrorRequestId.length > 0
      ? data.generationErrorRequestId
      : null;
  const generationErrorStage =
    typeof data.generationErrorStage === 'string' && data.generationErrorStage.trim()
      ? data.generationErrorStage.trim()
      : null;
  const generationErrorSuggestedAction =
    typeof data.generationErrorSuggestedAction === 'string' && data.generationErrorSuggestedAction.trim()
      ? data.generationErrorSuggestedAction.trim()
      : null;
  // A timed-out submit may still finish upstream. Keep the durable job id as a
  // recovery handle so the error card can query the existing result later.
  const generationRecoveryJobId =
    typeof data.generationRecoveryJobId === 'string' && data.generationRecoveryJobId.trim()
      ? data.generationRecoveryJobId.trim()
      : typeof data.generationTaskJobId === 'string' && data.generationTaskJobId.trim()
        ? data.generationTaskJobId.trim()
        : null;
  const generationRecoveryTaskType =
    typeof data.generationRecoveryTaskType === 'string' && data.generationRecoveryTaskType.trim()
      ? data.generationRecoveryTaskType.trim()
      : typeof data.generationTaskType === 'string' && data.generationTaskType.trim()
        ? data.generationTaskType.trim()
        : 'freezone_gen';
  const canRecoverImageTask = Boolean(
    generationRecoveryJobId
    && generationRecoveryTaskType === 'freezone_gen'
    && !data.imageUrl,
  );
  const cameraSelection = (data.cameraSelection ?? null) as ImageGenCameraSelection | null;
  const styleTemplateId =
    typeof data.styleTemplateId === 'string' && data.styleTemplateId.length > 0
      ? data.styleTemplateId
      : null;
  const referenceImageUrl =
    typeof data.referenceImageUrl === 'string' && data.referenceImageUrl.length > 0
      ? data.referenceImageUrl
      : null;
  /**
   * 节点**自带**的整组参考图。脚本节点「生成分镜」派生的图片节点会写这个数组
   * （LibTV 把该行每个角色的 `characterImageUrl` 都塞进 `params.imageList`，
   * 我们照它的语义补齐），多角色镜头因此不再只剩第一个角色。
   * 老画布 / 手动上传的节点只有 `referenceImageUrl` 一个字段，此时回落成单元素数组，
   * 与改造前的行为逐位相同。
   */
  const ownReferenceUrls = Array.isArray(data.referenceImageUrls)
    ? data.referenceImageUrls.filter(
      (url): url is string => typeof url === 'string' && url.length > 0,
    )
    : [];
  const expressionControlImageUrl =
    typeof data.expressionControlImageUrl === 'string' && data.expressionControlImageUrl.length > 0
      ? data.expressionControlImageUrl
      : null;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const autoSubmitClaimedRef = useRef(false);
  /** 上游还没出完时用它兜底重查闸门（正常路径靠上游出图后的重渲染）。 */
  const [autoSubmitRetryTick, setAutoSubmitRetryTick] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const [isTranslatingPrompt, setIsTranslatingPrompt] = useState(false);
  const [isOptimizingPrompt, setIsOptimizingPrompt] = useState(false);
  const [isRecovering, setIsRecovering] = useState(false);
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
  const {
    models: availableModels,
    isLoading: imageModelsLoading,
    isFallback: imageModelsFallback,
  } = useFreezoneImageModels();
  const { models: textModels } = useDirectModelCatalog('text');
  const resolvedTextModel = resolveDirectCanvasModelId(undefined, textModels);
  // Per-node generation history. Only fetch while the node is selected so an
  // unselected canvas full of nodes doesn't fan out a request each. `refresh`
  // is called after a generation settles to pull in the new record.
  const {
    records: historyRecords,
    isLoading: historyLoading,
    refresh: refreshHistory,
  } = useNodeGenerationHistory(id, { enabled: Boolean(selected) });

  // 生成进行中时，点击历史记录走「非破坏性预览」：不覆写 imageUrl、不打断在途
  // 任务，仅把这张历史图临时显示在主体上（见 isGenerating 渲染分支）。新图生成
  // 完成后由下方 effect 自动清空，回到最新结果。非生成态恢复历史时也清掉它。
  const [historyPreviewUrl, setHistoryPreviewUrl] = useState<string | null>(null);

  const handleRestoreHistory = useCallback(
    (record: Parameters<typeof historyRecordOutputUrl>[0]) => {
      const url = historyRecordOutputUrl(record);
      if (!url) return;
      // 生成进行中：仅做非破坏性预览，绝不动 imageUrl，也不打断在途任务。
      if (isGenerating) {
        setHistoryPreviewUrl(url);
        return;
      }
      setHistoryPreviewUrl(null);
      updateNodeData(id, {
        imageUrl: url,
        previewImageUrl: url,
        isGenerating: false,
        generationStartedAt: null,
        // 恢复的是单张历史结果，旧批次画册已与主图脱钩（没有任何一张会命中
        // 「主图」标记，点画册格还会静默丢掉刚恢复的图）——一并清掉。
        generationBatch: null,
      });
    },
    [id, isGenerating, updateNodeData],
  );

  // 生成结束（成功/失败）后清掉临时历史预览，让主体回到最新结果。
  useEffect(() => {
    if (!isGenerating) setHistoryPreviewUrl(null);
  }, [isGenerating]);
  // Resolve the model against the LIVE model list and derive BOTH the picker's
  // displayed id and the submit apiModel from this one object, so they can
  // never diverge.
  //
  // A persisted legacy id is reconciled against the authoritative runtime
  // catalog, so display, selected label and submitted model can never diverge.
  const selectedModel = useMemo(() => {
    const persisted =
      typeof data.model === 'string' && data.model.length > 0 ? data.model : null;
    const selected = selectLiveModel(availableModels, persisted);
    return selected && isLiveModel(selected) ? selected : undefined;
  }, [data.model, availableModels]);
  const modelId = selectedModel?.id ?? '';
  const runtimeImageModel = useMemo(
    () => (selectedModel ? runtimeImageModelFromOption(selectedModel) : UNCONFIGURED_IMAGE_MODEL),
    [selectedModel],
  );
  // An explicitly empty list is an authoritative "no preset" contract. It
  // must not turn into the synthetic `auto` option (which later becomes 1:1).
  const hasExplicitAspectContract = selectedModel?.aspectRatioOptions !== undefined;
  const allowAutoAspectRatio = Boolean(
    selectedModel
      && !hasExplicitAspectContract
      && selectedModel.capabilitySource !== 'unknown',
  );
  const imageAspectOptions = useMemo(
    () => {
      const options = allowAutoAspectRatio
        ? [{ value: 'auto', label: '自适应' }, ...runtimeImageModel.aspectRatios]
        : [...runtimeImageModel.aspectRatios];
      if (
        runtimeImageModel.supportsCustomAspectRatio &&
        rawAspectRatio &&
        isValidImageAspectRatio(rawAspectRatio, false) &&
        !options.some(({ value }) => value === rawAspectRatio)
      ) {
        options.push({ value: rawAspectRatio, label: rawAspectRatio });
      }
      return options;
    },
    [allowAutoAspectRatio, rawAspectRatio, runtimeImageModel],
  );
  const imageSizeOptions = useMemo(
    () => {
      const options = runtimeImageModel.resolutions.map((option) => option.value as ImageSize);
      if (
        runtimeImageModel.supportsCustomResolution &&
        rawSize &&
        isValidImageSize(rawSize) &&
        !options.includes(rawSize as ImageSize)
      ) {
        options.push(rawSize as ImageSize);
      }
      return options;
    },
    [rawSize, runtimeImageModel],
  );
  const defaultAspectRatio = runtimeImageModel.defaultAspectRatio;
  const defaultSize = runtimeImageModel.defaultResolution as ImageSize;
  const aspectRatio = imageAspectOptions.some(({ value }) => value === rawAspectRatio)
    ? rawAspectRatio
    : defaultAspectRatio;
  const size = imageSizeOptions.includes(rawSize as ImageSize)
    ? rawSize as ImageSize
    : defaultSize;
  const qualityOptions = runtimeImageModel.qualityOptions ?? [];
  const supportsQuality = qualityOptions.length > 0;
  const quality = qualityOptions.some(({ value }) => value === data.quality)
    ? data.quality as ImageQuality
    : (qualityOptions.find(({ value }) => value === DEFAULT_IMAGE_QUALITY)?.value
      ?? qualityOptions[0]?.value
      ?? DEFAULT_IMAGE_QUALITY) as ImageQuality;
  useEffect(() => {
    const patch: Partial<ImageGenNodeData> = {};
    if (selectedModel && data.model !== selectedModel.id) {
      patch.model = selectedModel.id;
    }
    if (data.aspectRatio !== aspectRatio) {
      patch.aspectRatio = aspectRatio;
    }
    if (data.size !== size) {
      patch.size = size;
    }
    if (Object.keys(patch).length > 0) {
      updateNodeData(id, patch);
    }
  }, [aspectRatio, data.aspectRatio, data.model, data.size, id, selectedModel, size, updateNodeData]);
  const imageSelectionForCost =
    imageModelsLoading || imageModelsFallback ? null : selectedModel?.apiModel ?? null;
  const imageCreditCost = useGenerationCreditCost('image_selection', imageSelectionForCost, {
    surface: 'canvas',
    params: supportsQuality ? { size, quality } : { size },
    quantity: clampGenerationBatchCount(effectiveCount),
  });
  const totalCreditCostDisplay = useMemo(() => {
    const total = imageCreditCost.data?.data.cost;
    if (typeof total !== 'number') return null;
    return formatCreditCost(total);
  }, [imageCreditCost.data?.data.cost]);
  const { options: cameraOptions } = useFreezoneCameraOptions();
  const cameraSummary = describeCameraSelection(cameraSelection, cameraOptions);
  const { templates: styleTemplates } = useFreezoneStyleTemplates();
  const selectedStyle = describeStyleSelection(styleTemplateId, styleTemplates);

  const upstreamContents = useUpstreamContents(id);
  // ImageGen 上游只消费「文本 + 图片」，视频/音频内容被丢弃 ——
  // 即便 upload 节点带了视频 URL，也不进 OpsPanel 也不进 reference_urls。
  const upstreamImageContents = useMemo(() => {
    const seen = new Set<string>();
    const out: typeof upstreamContents = [];
    for (const content of upstreamContents) {
      const url = typeof content.imageUrl === 'string' ? content.imageUrl : '';
      if (!url || seen.has(url)) continue;
      seen.add(url);
      out.push(content);
    }
    return out;
  }, [upstreamContents]);
  const upstreamTextContents = useMemo(
    () =>
      upstreamContents.filter(
        (content) => typeof content.text === 'string' && content.text.trim().length > 0,
      ),
    [upstreamContents],
  );
  const upstreamTextJoined = useMemo(
    () => joinUpstreamText(upstreamContents),
    [upstreamContents],
  );
  const freezoneSource = (data.__freezone_source as
    | { role?: string; meta?: Record<string, unknown> }
    | undefined) ?? undefined;
  const sourceRole = typeof freezoneSource?.role === "string"
    ? freezoneSource.role
    : "";
  // 分镜图已经把本镜构图、资产锚定（图片N↔角色/场景/道具）写进自己的 prompt。
  // 再把整份脚本从上游拼进来会让 relay 压缩掉锚定段，模型只能看到附件而不知道各图用途。
  const isScriptStoryboardNode = Boolean(data.scriptRowKey || data.scriptShotId);
  const shouldInlineUpstreamTextAsPrompt =
    sourceRole === "scene_master" || sourceRole === "scene_reverse_master";
  const upstreamReferenceUrls = useMemo(
    () =>
      Array.from(
        new Set(
          upstreamImageContents
            .map((c) => (typeof c.imageUrl === 'string' ? c.imageUrl : ''))
            .filter((url) => url.length > 0),
        ),
      ),
    [upstreamImageContents],
  );
  // 提交给后端的参考图有序列表：节点自带参考图（整组，按序）排前面、上游图接在
  // 后面（URL 去重）。@图片N 编号、mention 重排基线、提交三处共用这一份 —— 后端
  // 按位置解释 图片N，曾经编号只数上游图、提交却把自身参考图前置，节点自带参考图
  // 时所有 @图片N 到后端整体偏移 1（@图片1 实际指向自身参考图）。
  // 多元素时整组都进列表：`referenceImageUrl` 仍是第 1 张（`@图片1` 的编号基线没变），
  // 第 2 张起此前会被静默丢弃 —— 分镜图节点上就是多角色镜头丢人。
  const normalizedReferenceImageUrl = normalizeReferenceUrl(referenceImageUrl);
  const normalizedOwnReferenceUrls = useMemo(() => {
    // 顺序：`referenceImageUrl` 打头（它就是 `@图片1` 的编号基线，正常情况下也正是
    // 组里第 1 张），组内其余接在后面。这样用户手动传图覆盖第 1 张时，上传的那张
    // 不会被组里原来的角色卡顶掉，而原角色卡仍留在列表里参与提交。
    const merged = [
      ...(normalizedReferenceImageUrl ? [normalizedReferenceImageUrl] : []),
      ...ownReferenceUrls.map(normalizeReferenceUrl),
    ].filter((url): url is string => url !== null);
    return Array.from(new Set(merged));
  }, [normalizedReferenceImageUrl, ownReferenceUrls]);
  const normalizedExpressionControlImageUrl = normalizeReferenceUrl(expressionControlImageUrl);
  const orderedReferenceUrls = useMemo(() => {
    const base = orderedReferenceUrlsWithOwnFirst(normalizedOwnReferenceUrls, upstreamReferenceUrls);
    if (!normalizedExpressionControlImageUrl) return base;
    // 表情控制图固定占第 2 张（老契约），所以插在自带参考图第一张之后。
    const insertionIndex = normalizedOwnReferenceUrls.length > 0 ? 1 : 0;
    return Array.from(new Set([
      ...base.slice(0, insertionIndex),
      normalizedExpressionControlImageUrl,
      ...base.slice(insertionIndex),
    ]));
  }, [normalizedExpressionControlImageUrl, normalizedOwnReferenceUrls, upstreamReferenceUrls]);
  const connectedEdges = useCanvasStore((state) =>
    selectConnectedCanvasEdges(state.edges, id),
  );
  const candidateBindingRoles = useMemo(
    () => collectCandidateBindingsForNode(connectedEdges, id).map((binding) => binding.role),
    [connectedEdges, id],
  );
  // 节点被连线（存在入边）后：隐藏「试试」CTA，只在节点中间显示一个图标（对齐 libtv）。
  const isConnected = useMemo(
    () => connectedEdges.some((edge) => edge.target === id),
    [connectedEdges, id],
  );

  // 候选按 orderedReferenceUrls 编号（自身参考图在场时就是图片1），保证 @ 出来的
  // 缩略图与后端解析到的 图片N 是同一张。key 优先用上游 nodeId；自身参考图没有
  // 上游节点，用 URL 兜底（key 只需在候选内稳定唯一）。
  const mentionCandidates = useMemo<MentionCandidate[]>(
    () =>
      orderedReferenceUrls.map((url, index) => ({
        key:
          upstreamImageContents.find((content) => content.imageUrl === url)
            ?.nodeId ?? `self:${url}`,
        name: `图片${index + 1}`,
        imageUrl: resolveImageDisplayUrl(url),
        index: index + 1,
      })),
    [orderedReferenceUrls, upstreamImageContents],
  );

  const promptOptimizationContextSignature = useMemo(
    () => JSON.stringify({
      prompt: prompt.trim(),
      model_id: modelId,
      api_model: selectedModel?.apiModel ?? modelId,
      aspect_ratio: aspectRatio,
      size,
      quality: supportsQuality ? quality : null,
      camera: cameraSummary || null,
      style_id: styleTemplateId,
      references: orderedReferenceUrls,
      upstream_text: upstreamTextJoined,
    }),
    [
      aspectRatio,
      cameraSummary,
      supportsQuality,
      modelId,
      orderedReferenceUrls,
      prompt,
      quality,
      selectedModel?.apiModel,
      size,
      styleTemplateId,
      upstreamTextJoined,
    ],
  );

  const handleOptimizePrompt = useCallback(
    async (
      researchMode: PromptOptimizationResearchMode = 'standard',
      options: { keepPanel?: boolean } = {},
    ) => {
      if (isOptimizingPrompt || isGenerating) return;
      const originalPrompt = prompt.trim();
      if (!originalPrompt) return;
      const projectId = readUrl().project;
      if (!projectId) {
        toast.error('当前 URL 缺少项目，无法调用提示词优化模型。');
        return;
      }
      setIsOptimizingPrompt(true);
      // 换联网深度重跑时保留面板，让按钮上的加载态可见。
      if (!options.keepPanel) setPromptOptimization(null);
    try {
      const request: FreezonePromptOptimizePayload = {
        text: originalPrompt,
        nodeType: 'image',
        targetModelId: modelId,
        targetApiModel: selectedModel?.apiModel ?? modelId,
        targetModelLabel: selectedModel?.label ?? modelId,
        params: {
          mode: orderedReferenceUrls.length > 0 ? 'image_to_image' : 'text_to_image',
          aspect_ratio: aspectRatio,
          size,
          quality: supportsQuality ? quality : null,
          camera: cameraSummary || null,
          style: selectedStyle
            ? { id: styleTemplateId, label: selectedStyle.label, prompt: selectedStyle.style_prompt }
            : null,
          upstream_text_context: upstreamTextJoined || null,
          upstream_text_is_injected_separately: upstreamTextJoined.length > 0,
        },
        references: orderedReferenceUrls.map((url, index) => ({
          name: `@图片${index + 1}`,
          kind: 'image',
          order: index + 1,
          url,
          role: url === normalizedExpressionControlImageUrl
            ? 'expression_geometry_control'
            : index === 0 && normalizedOwnReferenceUrls.length > 0
              ? 'node_primary_reference'
              : 'upstream_reference',
        })),
        researchMode,
        guidance:
          '只输出当前图片节点自有提示词。上游文本会在生成提交时自动拼接，不要重复抄入优化稿；只能使用 AVAILABLE REFERENCES 中列出的引用名。',
      };
      const ref = await submitFreezonePromptOptimize(projectId, request);
      await awaitTaskCompletion(ref.task_key, projectId);
      const result = await fetchFreezonePromptOptimizeResult(projectId, ref.job_id);
      setPromptOptimization({
        originalPrompt,
        request,
        result,
        jobId: ref.job_id,
        taskKey: ref.task_key,
        contextSignature: promptOptimizationContextSignature,
      });
    } catch (error) {
      console.error('[image-gen] AI prompt optimization failed', error);
      toast.error(`提示词优化失败：${backendErrorToastMessage(error, t)}`);
    } finally {
      setIsOptimizingPrompt(false);
    }
  }, [
    aspectRatio,
    cameraSummary,
    isGenerating,
    supportsQuality,
    isOptimizingPrompt,
    modelId,
    orderedReferenceUrls,
    prompt,
    promptOptimizationContextSignature,
    quality,
    normalizedExpressionControlImageUrl,
    normalizedReferenceImageUrl,
    selectedModel?.apiModel,
    selectedModel?.label,
    selectedStyle,
    size,
    styleTemplateId,
    t,
    upstreamTextJoined,
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
      toast.warning('节点内容或执行配置已变化，请重新优化后再采用。');
      return;
    }
    const { originalPrompt, request, result } = promptOptimization;
    const next = mergePromptOptimizationLockedConstraints(
      originalPrompt,
      result.optimized_prompt,
    );
    setPromptDraft(next);
    hasUserEditedPromptRef.current = true;
    updateNodeData(id, {
      prompt: next,
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
      prompt_optimizer_request_snapshot: request,
      prompt_optimizer_job_id: promptOptimization.jobId,
      prompt_optimizer_task_key: promptOptimization.taskKey,
      prompt_optimized_at: new Date().toISOString(),
    });
    setPromptOptimization(null);
  }, [id, promptOptimization, promptOptimizationContextSignature, updateNodeData]);

  // 让 prompt 里的 @图片N 始终跟随参考图引用编号：删除 / 重排 / 新增引用连线、
  // 上传或移除自身参考图后，mentionCandidates 会重新编号，这里把 prompt 里的数字
  // 一并重写、被删引用的 mention 移除。有序基线 = orderedReferenceUrls（自身参考图
  // 在前、去重 URL、连接顺序，与编号和提交口径一致；用 URL 而非 nodeId 作身份，
  // 避免「两个上游节点图同一 URL」时删其一被误判为引用消失）。
  const applyPromptRemap = useCallback(
    (next: string) => {
      setPromptDraft(next);
      updateNodeData(id, { prompt: next });
    },
    [id, updateNodeData],
  );
  useReferenceMentionSync(
    prompt,
    [{ prefix: "图片", ids: orderedReferenceUrls }],
    applyPromptRemap,
  );

  // 弹层与编辑器同在面板里、编辑器恒已挂载，故插入直接走命令式 API，回调保持稳定引用
  // （无需依赖 prompt，避免每次按键重建回调、连带调色盘按钮重渲染）。
  const insertContextPaletteEntry = useCallback(
    (entry: ContextPromptPaletteEntry) => {
      promptEditorRef.current?.insertTextAtCursor(
        contextPromptPaletteInsertionText(entry),
      );
    },
    [],
  );

  // 取消关联某个上游素材：直接删掉「该上游节点 → 本节点」的连线，无需用户
  // 去画布上找那根线。collectInputContents 只走一跳，所以 content.nodeId 就是
  // 直接相连的上游节点，可精确定位到要删的边。
  const handleDetachUpstream = useCallback(
    (sourceNodeId: string) => {
      useCanvasStore
        .getState()
        .edges.filter((edge) => edge.source === sourceNodeId && edge.target === id)
        .forEach((edge) => deleteEdge(edge.id));
    },
    [id, deleteEdge],
  );

  // 点引用缩略图 → 把它的 `@图片N` 写进提示词。
  //
  // 与视频节点同一口径：只写不重排（序号 = 在当前有序引用列表里的位置，点一下就
  // 悄悄改别人的号会让提示词里已有的 `@图片N` 全部错位）。同一张引用点几次写几次，
  // 插入位置由编辑器光标决定 —— 不拦重复引用。提示词完全由命令行式 API 改，
  // `useReferenceMentionSync` 会在引用顺序变化时接管重编号。
  const handleInsertReference = useCallback(
    (mentionName: string) => {
      if (!mentionName) return;
      promptEditorRef.current?.insertTextAtCursor(
        referenceInsertText(mentionName),
      );
    },
    [],
  );

  const [isAssetLibraryOpen, setIsAssetLibraryOpen] = useState(false);

  // Spawn upload reference nodes from selected asset-library images — one per
  // selection, stacked to the left of this node, then wired as upstream refs so
  // they feed the multi-reference generation. Image-only here (the modal is
  // opened with allowedMedia=['image']), but we still guard on media.
  const spawnAssetLibraryReferences = useCallback(
    (selections: ReadonlyArray<AssetLibrarySelection>) => {
      const imageSelections = selections.filter((sel) => sel.media === 'image');
      if (imageSelections.length === 0) return;
      const state = useCanvasStore.getState();
      const self = state.nodes.find((n) => n.id === id);
      if (!self) return;
      const UPLOAD_WIDTH = 320;
      const UPLOAD_HEIGHT = 240;
      const GAP_X = 40;
      const GAP_Y = 24;
      const baseX = self.position.x - UPLOAD_WIDTH - GAP_X;
      const totalH =
        UPLOAD_HEIGHT * imageSelections.length + GAP_Y * (imageSelections.length - 1);
      const startY =
        self.position.y + ((self.height ?? DEFAULT_HEIGHT) - totalH) / 2;
      const newIds: string[] = [];
      imageSelections.forEach((sel, idx) => {
        const y = startY + idx * (UPLOAD_HEIGHT + GAP_Y);
        const newId = addNodeAction(
          CANVAS_NODE_TYPES.upload,
          { x: baseX, y },
          {
            imageUrl: sel.url,
            previewImageUrl: sel.url,
            displayName: sel.name || undefined,
          },
        );
        addEdgeAction(newId, id);
        newIds.push(newId);
      });
      state.autoGroupSpawn(id, newIds, { label: '资产参考组' });
    },
    [addEdgeAction, addNodeAction, id],
  );

  // Hover preview state for the upstream image thumbnails in the OpsPanel
  // reference row. Mirrors the @-mention chip preview UX so users can peek
  // a full-size image without leaving the prompt editor.
  const [refHover, setRefHover] = useState<{ imageUrl: string; rect: DOMRect } | null>(null);
  const refPreviewStyle = useMemo(() => {
    if (!refHover) return null;
    const SIZE = 220;
    const left = Math.min(
      Math.max(8, refHover.rect.left),
      window.innerWidth - SIZE - 8,
    );
    const top = refHover.rect.top - SIZE - 8;
    return { left, top: Math.max(8, top), size: SIZE };
  }, [refHover]);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.imageGen, data),
    [data],
  );
  const usesConfiguredAspectLayout = !data.imageUrl && !referenceImageUrl;
  const configuredAspectMin = usesConfiguredAspectLayout
    ? resolveResizeMinConstraintsByAspect(
        aspectRatio === 'auto' ? '1:1' : aspectRatio,
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
  const [stylePickerOpen, setStylePickerOpen] = useState(false);
  const panelHeight = OPERATIONS_PANEL_HEIGHT;
  const panelWidth = Math.max(resolvedWidth, OPERATIONS_PANEL_MIN_WIDTH);

  const previewUrl = useMemo(() => {
    if (data.previewImageUrl) return resolveImageDisplayUrl(data.previewImageUrl);
    if (data.imageUrl) return resolveImageDisplayUrl(data.imageUrl);
    if (referenceImageUrl && !data.scriptShotKeyframeSourceNodeId) return resolveImageDisplayUrl(referenceImageUrl);
    return null;
  }, [data.imageUrl, data.previewImageUrl, referenceImageUrl, data.scriptShotKeyframeSourceNodeId]);
  const visiblePreviewUrl = isGenerating ? null : previewUrl;

  const hasGeneratedResult = Boolean(data.imageUrl);
  // Natural pixel size of the displayed image, mirrored from data when present
  // (persisted by the onLoad handler below) and refreshed on every <img> load so
  // the resolution badge shows even for nodes whose size already matched (those
  // skip the persist branch). Lets us render a top-right resolution chip like the
  // video node.
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(() => {
    const w = (data as { imageNaturalWidth?: unknown }).imageNaturalWidth;
    const h = (data as { imageNaturalHeight?: unknown }).imageNaturalHeight;
    return typeof w === 'number' && typeof h === 'number' && w > 0 && h > 0
      ? { width: w, height: h }
      : null;
  });
  // ── 叠卡画册（count > 1 的一组生成结果）──
  // 收拢时主图后探出 N-1 张卡片边缘；hover 出现右上角数量徽标，点开展开成
  // 宫格画册（同一节点内，天然不可解组）。展开态可对任意一张「设为主图」
  // （回填 imageUrl 并收拢）或单独下载。
  const albumRootRef = useRef<HTMLDivElement | null>(null);
  // 画册容器 pointerdown 起点，用于区分点击与拖动（拖动节点后松手会补发 click）。
  const albumPointerDownPosRef = useRef<{ x: number; y: number } | null>(null);
  const [albumExpanded, setAlbumExpanded] = useState(false);
  // 本次会话内"应到张数"：N 个接口并发、完成有先后，先完成的立即入册，
  // 未完成的在画册里占位（骨架 + spinner）。存模块级登记表而非组件 state——
  // onlyRenderVisibleElements 下平移出视口会卸载组件，state 会丢；见模块注释。
  const albumPendingTotal = useAlbumPendingTotal(id);
  const albumUrls = useMemo(() => {
    const raw = data.generationBatch;
    if (!Array.isArray(raw)) return [];
    return raw.filter((u): u is string => typeof u === 'string' && u.length > 0);
  }, [data.generationBatch]);
  const albumTotalSlots = Math.max(albumUrls.length, albumPendingTotal);
  const albumPendingCount = Math.max(0, albumPendingTotal - albumUrls.length);
  const hasAlbum = albumTotalSlots > 1;

  // 画册展开期间注册为本节点的 activeOverlay：拖动画册会让 React Flow 重新
  // 选中节点（selectNodesOnDrag），单靠展开瞬间的取消选中压不住——action
  // 工具条 / OpsPanel / 历史条 / 替换素材把手都认 activeOverlayNodeId 让位，
  // 注册后无论选中与否都不会再叠出来。
  useEffect(() => {
    if (!albumExpanded) return;
    setActiveOverlayNodeId(id);
    return () => {
      // 只清自己注册的，避免误清其它浮层（多角度/打光等）的注册。
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

  const handleSetAlbumMainImage = useCallback(
    (url: string) => {
      updateNodeData(id, { imageUrl: url, previewImageUrl: url });
      setAlbumExpanded(false);
    },
    [id, updateNodeData],
  );

  // 展开画册时取消节点激活态：上方 action 工具条、下方 OpsPanel、历史记录条
  // 都跟着 selected 走，叠在宫格上很乱——画册期间只看图。
  // 注意必须经 onNodesChange 派发 select=false 清掉 React Flow 自身的选中
  // 标志——只清 store 的 selectedNodeId 会被 Canvas 的选中同步 effect
  // （RF selectedNodeIds → setSelectedNode）立刻写回来。
  // 副作用放在 setState updater 外面：updater 必须纯（StrictMode 会双调用，
  // 副作用入内会把 onNodesChange 派发两遍）。
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

  // 「应用到画布」：把这张图作为独立图片节点放到展开宫格右侧（同构 imageGen
  // 节点，可直接被下游引用/二次生成）。画册保持展开，方便连续应用多张——
  // 连续应用的落点逐次向下错开，避免精确叠在同一坐标上只看得见最后一个。
  const albumAppliedCountRef = useRef(0);
  const handleApplyAlbumImageToCanvas = useCallback(
    (url: string) => {
      const self = useCanvasStore.getState().nodes.find((n) => n.id === id);
      if (!self) return;
      const applyIndex = albumAppliedCountRef.current;
      albumAppliedCountRef.current += 1;
      const position = {
        x: self.position.x + resolvedWidth * 2 + 12 + 48 + applyIndex * 36,
        y: self.position.y + applyIndex * 36,
      };
      const newNodeId = addNodeAction(CANVAS_NODE_TYPES.imageGen, position, {
        imageUrl: url,
        previewImageUrl: url,
        aspectRatio,
        user_spawned: true,
      } as Partial<ImageGenNodeData>);
      setSelectedNode(newNodeId);
    },
    [addNodeAction, aspectRatio, id, resolvedWidth, setSelectedNode],
  );

  const handleDownloadAlbumImage = useCallback(
    async (url: string, index: number) => {
      try {
        await downloadUrlAsFile(resolveImageDisplayUrl(url), `image-gen-${id}-${index + 1}.png`);
      } catch (error) {
        console.error('[image-gen] album download failed', error);
      }
    },
    [id],
  );

  const handlePickFile = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleUploadFile = useCallback(
    async (file: File) => {
      const projectId = readUrl().project;
      if (!projectId) {
        console.error('[image-gen] no project in URL');
        return;
      }
      setIsUploading(true);
      try {
        const result = await uploadFreezoneImage(projectId, file, file.name);
        // 用户主动上传是**显式覆盖**：节点自带参考图收拢成这一张，不再把分镜派生的
        // 角色卡留在组里偷偷参与提交（那些卡在节点上看不见，静默多送参考图属于
        // 隐藏状态）。撤销/换掉都在这里重新写整组。
        updateNodeData(id, { referenceImageUrl: result.url, referenceImageUrls: [result.url] });
      } catch (error) {
        console.error('[image-gen] upload failed', error);
      } finally {
        setIsUploading(false);
      }
    },
    [id, updateNodeData],
  );

  const handleClearReference = useCallback(() => {
    // 整组一起清：只清 `referenceImageUrl` 会让组里的角色卡继续参与提交。
    updateNodeData(id, { referenceImageUrl: null, referenceImageUrls: null });
  }, [id, updateNodeData]);

  const handleRemoveOwnReference = useCallback(
    (url: string) => {
      const normalized = normalizeReferenceUrl(url);
      if (!normalized) return;
      const remaining = Array.from(
        new Set(
          [referenceImageUrl, ...ownReferenceUrls]
            .map(normalizeReferenceUrl)
            .filter((item): item is string => item !== null),
        ),
      ).filter((item) => item !== normalized);
      updateNodeData(id, {
        referenceImageUrl: remaining[0] ?? null,
        referenceImageUrls: remaining.length > 0 ? remaining : null,
      });
    },
    [id, ownReferenceUrls, referenceImageUrl, updateNodeData],
  );

  const handleRemoveExpressionReference = useCallback(() => {
    updateNodeData(id, { expressionControlImageUrl: null });
  }, [id, updateNodeData]);

  const handleSpawnUpstreamImage = useCallback(() => {
    const self = useCanvasStore.getState().nodes.find((n) => n.id === id);
    if (!self) return;
    // 上游图片节点本身也是 imageGen —— 用户可以直接在它里面写 prompt /
    // 选模型 / 生成图，下游再拿它的结果当参考图。与 upload 相比好处是
    // 自带 OpsPanel，整链路同构。
    const UPSTREAM_WIDTH = DEFAULT_WIDTH;
    const position = {
      x: self.position.x - UPSTREAM_WIDTH - 28,
      y: self.position.y,
    };
    const newNodeId = addNodeAction(CANVAS_NODE_TYPES.imageGen, position);
    addEdgeAction(newNodeId, id);
    setSelectedNode(newNodeId);
  }, [addEdgeAction, addNodeAction, id, setSelectedNode]);

  const handleTranslatePrompt = useCallback(async () => {
    if (isTranslatingPrompt || isGenerating) return;
    const trimmed = prompt.trim();
    if (trimmed.length === 0) return;
    const projectId = readUrl().project;
    if (!projectId) {
      console.error('[image-gen] translate: no project in URL');
      return;
    }
    setIsTranslatingPrompt(true);
    try {
      const ref = await submitFreezoneTextTranslate(projectId, {
        text: prompt,
        nodeType: 'image',
        model: resolvedTextModel || undefined,
        canvasId: readUrl().canvas ?? 'default',
        nodeId: id,
      });
      await awaitTaskCompletion(ref.task_key, projectId);
      const result = await fetchFreezoneTextTranslateResult(projectId, ref.job_id);
      if (result.translated_text) {
        setPromptDraft(result.translated_text);
        updateNodeData(id, { prompt: result.translated_text });
      }
    } catch (error) {
      console.error('[image-gen] translate failed', error);
    } finally {
      setIsTranslatingPrompt(false);
    }
  }, [id, isGenerating, isTranslatingPrompt, prompt, resolvedTextModel, updateNodeData]);

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, resolvedHeight, resolvedWidth, updateNodeInternals]);

  // 「实时读取上游」：用户可以不填 prompt，只要上游连了带 text 的节点
  // (文本/脚本/图片生成 prompt 等) 就能 submit；submit 时拼接上游 text。
  const hasEffectivePrompt =
    prompt.trim().length > 0 ||
    (
      upstreamTextJoined.length > 0 &&
      (!shouldInlineUpstreamTextAsPrompt || !hasUserEditedPromptRef.current)
    );
  const hasAuthoritativeImageModel = Boolean(
    selectedModel
      && selectedModel.enabled !== false
      && selectedModel.disabled !== true,
  ) && !imageModelsLoading && !imageModelsFallback;
  const supportsImageToImage =
    !selectedModel?.supportedModes ||
    selectedModel.supportedModes.includes('imageToImage') ||
    selectedModel.supportedModes.includes('image_to_image');
  const imageModeBlockedReason =
    orderedReferenceUrls.length > 0 && !supportsImageToImage
      ? `${selectedModel?.label ?? '当前模型'} 已识别为仅支持文生图，移除参考图或切换支持图生图的模型。`
      : null;
  const submitDisabled =
    isGenerating || !hasEffectivePrompt || !hasAuthoritativeImageModel || Boolean(imageModeBlockedReason);
  const submitDisabledReason = isGenerating
    ? '当前任务仍在生成中'
    : imageModeBlockedReason
      ?? (!hasEffectivePrompt
        ? '请先填写提示词或连接带文本的上游节点'
        : !hasAuthoritativeImageModel
          ? '请先配置并检测可用的生图模型'
          : null);

  const handleRecoverImageTask = useCallback(async () => {
    if (isRecovering) return;
    const projectId = readUrl().project;
    const recoveryJobId = generationRecoveryJobId;
    if (!projectId || !recoveryJobId) {
      toast.error('没有可重新获取的上游图片任务');
      return;
    }

    setIsRecovering(true);
    try {
      // This path only reads the existing freezone job result. It never calls
      // submitFreezoneGen, so a late upstream result cannot be double-charged.
      const result = await fetchFreezoneJobResultWithRetry(
        projectId,
        'freezone_gen',
        recoveryJobId,
        { attempts: 30, delayMs: 2_000 },
      );
      const url = typeof result.url === 'string' ? result.url.trim() : '';
      if (!url) throw new Error('重新获取完成但没有返回图片地址');

      updateNodeData(id, {
        ...buildImageGenerationSuccessPatch(url),
        ...buildGeneratedRightsPatch(data),
        generationRecoveryJobId: null,
        generationRecoveryTaskType: null,
      });
      void refreshHistory();
      toast.success('已重新获取上游图片结果');
    } catch (error) {
      const rawErrorMessage =
        error instanceof Error && error.message
          ? error.message
          : String(error || '重新获取图片失败');
      updateNodeData(id, {
        isGenerating: false,
        generationStartedAt: null,
        generationError: backendErrorToastMessage(error, t),
        generationErrorDetails: rawErrorMessage,
        generationErrorRequestId: extractRequestId(rawErrorMessage),
        generationErrorStage: 'query',
        generationErrorSuggestedAction: '上游结果尚未落盘，可稍后再次点击重新获取。',
        generationErrorCode: null,
        generationErrorRetryable: true,
        generationRecoveryJobId: recoveryJobId,
        generationRecoveryTaskType: 'freezone_gen',
      });
      toast.error('重新获取图片失败，可稍后再次尝试');
    } finally {
      setIsRecovering(false);
    }
  }, [
    generationRecoveryJobId,
    id,
    isRecovering,
    refreshHistory,
    t,
    updateNodeData,
  ]);

  const handleSubmit = useCallback(async () => {
    if (submitDisabled || submittingRef.current) return;
    submittingRef.current = true;
    // 从这一刻起本节点就算「在出图」：下游节点靠它按顺序排在后面（准备期也算）。
    markGenerationIntent(id);
    const abortController = new AbortController();
    generationQueueAbortRef.current?.abort();
    generationQueueAbortRef.current = abortController;
    try {
    const projectId = readUrl().project;
    if (!projectId) {
      console.error('[image-gen] no project in URL');
      return;
    }

    // apiModel comes from the SAME reconciled model the picker displays, so the
    // backend always receives the model the user actually sees.
    const apiModel = selectedModel?.apiModel;
    if (!apiModel) return;
    // 自身参考图（用户手动上传） + 所有上游图片/视频 URL，去重 —— 与 @图片N
    // 编号共用同一份有序列表（orderedReferenceUrls），后端按位置解释 图片N。
    // 后端 reference_urls 接受 image / video 混合数组。
    const referenceUrls = orderedReferenceUrls;
    const hasCamera = Boolean(
      cameraSelection
      && (cameraSelection.cameraBodyId
        || cameraSelection.lensId
        || cameraSelection.focalLengthMm
        || cameraSelection.aperture),
    );
    const ownPrompt = prompt.trim();
    const effectivePrompt = isScriptStoryboardNode
      ? ownPrompt
      : shouldInlineUpstreamTextAsPrompt
      ? (ownPrompt || (hasUserEditedPromptRef.current ? "" : upstreamTextJoined.trim()))
      : [upstreamTextJoined, ownPrompt]
        .filter((s) => s.length > 0)
        .join('\n\n');
    const genPayload = {
      prompt: effectivePrompt,
      // 节点上的比例可能是图片自然尺寸约分出的非标准值或 "auto"；提交前
      // 吸附到当前模型合同声明的合法比例，不能落回另一套全局比例列表。
      aspectRatio: imageAspectOptions.length > 0
        ? snapToAllowedAspectRatio(
          aspectRatio,
          imageAspectOptions
            .map((option) => option.value)
            .filter((value) => value !== 'auto'),
          imageAspectOptions.find((option) => option.value !== 'auto')?.value ?? '1:1',
        ) as typeof aspectRatio
        : '',
      imageSize: size,
      // 画质仅在模型合同声明 qualityOptions 时下发。
      quality: supportsQuality ? quality : null,
      // 模型合同声明的高级参数（MJ 系开关等）。后端按合同过滤，未知键不透传。
      advancedSettings: buildImageAdvancedSettings(data.extraParams),
      referenceUrls,
      model: apiModel,
      modelId,
      camera: hasCamera
        ? {
            cameraBodyId: cameraSelection?.cameraBodyId ?? null,
            lensId: cameraSelection?.lensId ?? null,
            focalLengthMm: cameraSelection?.focalLengthMm ?? null,
            aperture: cameraSelection?.aperture ?? null,
          }
        : null,
      style: styleTemplateId ? { templateId: styleTemplateId } : null,
    };

    // 后端不再支持一次出多张，改为按「生成数量」并发调用 N 次接口，每次出
    // 1 张。N > 1 时不再复制兄弟节点，而是全部回填到当前节点的
    // generationBatch（叠卡画册）：第 1 张完成的设为主图（imageUrl），其余
    // 逐张追加进画册，收拢态渲染成叠起的卡片。
    const total = clampGenerationBatchCount(effectiveCount);
    // Clear any prior failure / album on resubmit — the on-node error banner
    // should only reflect the most recent attempt.
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
      generationRecoveryJobId: null,
      generationRecoveryTaskType: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
      generationTaskRefs: null,
      generationBatch: null,
      generationQueueTotal: total,
      generationQueueCompleted: 0,
      generationQueueConcurrency: Math.min(GENERATION_CONCURRENCY_DEFAULT, total),
      // Freeze request tier for the honesty badge (requested vs actual pixels).
      lastRequestedImageSize: size,
      lastRequestedImageQuality: supportsQuality ? quality : null,
      lastRequestedAspectRatio: genPayload.aspectRatio,
    });
    // 先完成的图立即入册展示，未完成的在画册里渲染占位骨架。
    setAlbumPendingTotal(id, total > 1 ? total : 0);

    const canvasId = readUrl().canvas ?? 'default';
    // 各并发任务完成顺序不定，本地累积已完成的 URL，整组写回（避免读改写竞态）。
    const completedUrls: string[] = [];
    const runOne = async (runIndex: number) => {
      let taskKey: string | null = null;
      let taskJobId: string | null = null;
      try {
        const ref = await submitFreezoneGen(projectId, {
          ...genPayload,
          canvasId,
          nodeId: id,
        });
        taskKey = ref.task_key;
        taskJobId = ref.job_id;
        // Persist the task handle so a page refresh can resume polling this
        // job. With N concurrent runs on one node only one handle can persist —
        // keep the first (main-image) run's.
        registerNodeGenerationTask(id, ref, { primary: runIndex === 0 });
        await cancelSubmittedTaskIfAborted(projectId, ref, abortController.signal);
        const mediaResult = await awaitFreezoneJobMediaResult(projectId, ref, {
          probeDelayMs: 800,
          maxProbeMs: 90_000,
          signal: abortController.signal,
          onTaskCompletedWithoutUrl: (completed) => {
            console.warn('[image-gen] generation task completed without inline url', completed);
          },
        });
        const url = mediaResult.url;
        if (url) {
          if (abortController.signal.aborted) return;
          if (runIndex === 0 && taskKey) {
            const latestNodeData = (useCanvasStore
              .getState()
              .nodes
              .find((node) => node.id === id)?.data ?? {}) as Record<string, unknown>;
            if (isStaleGenerationTask({ nodeData: latestNodeData, taskKey })) {
              abortController.abort();
              return;
            }
          }
          completedUrls.push(url);
          const isFirstCompleted = completedUrls.length === 1;
          updateNodeData(id, {
            // 第 1 张完成的设为主图并结束 loading；后续只扩充画册。
            ...(isFirstCompleted
              ? {
                  ...buildImageGenerationSuccessPatch(url),
                  ...buildGeneratedRightsPatch(data),
                  ...(data.expression_set_id ? { expression_status: "generated" } : {}),
                }
              : {}),
            ...(total > 1 ? { generationBatch: [...completedUrls] } : {}),
          });
          if (canAutoCommitOnGenerate && isFirstCompleted) {
            canvasEventBus.publish('freezone/commit-node', {
              nodeId: id,
              auto: true,
            });
          }
        } else {
          console.warn('[image-gen] generation completed without output url', mediaResult.task);
          // 只有 run 0（任务句柄的归属者）且尚无任何成功时才终结 loading——
          // 非首个任务先「无 URL 完成」不能把还在跑的整体 loading 提前掐掉。
          if (runIndex === 0 && completedUrls.length === 0) {
            updateNodeData(id, {
              isGenerating: false,
              generationStartedAt: null,
              generationError: '上游任务已完成，但没有返回图片结果',
              generationErrorDetails: '结果接口暂未返回可用图片地址。',
              generationErrorStage: 'query',
              generationErrorSuggestedAction: '上游结果可能仍在落盘，可稍后点击重新获取。',
              generationErrorRetryable: true,
              generationRecoveryJobId: taskJobId,
              generationRecoveryTaskType: 'freezone_gen',
            });
          }
        }
      } catch (error) {
        console.error('[image-gen] generation failed', error);
        const taskWasCancelled = isTaskCancelledError(error);
        if (taskWasCancelled) abortController.abort(error);
        if (abortController.signal.aborted && !taskWasCancelled) return;
        // 已有同批其它图完成（主图已落）时不覆盖成功态为错误——部分失败只
        // 影响画册张数。
        if (completedUrls.length > 0) return;
        // 任务仲裁（stale / shouldWrite）只对 run 0 有意义：节点上只持久化了
        // run 0 的任务句柄，其余 run 的 taskKey 必然对不上，套用仲裁会把
        // 它们的失败全部误判为「过期任务」而静默吞掉。
        if (runIndex === 0) {
          const latestNodeData = (useCanvasStore
            .getState()
            .nodes
            .find((node) => node.id === id)?.data ?? {}) as Record<string, unknown>;
          if (
            taskKey
            && isStaleGenerationTask({ nodeData: latestNodeData, taskKey })
          ) return;
          if (
            taskKey
            && !shouldWriteGenerationError({ nodeData: latestNodeData, taskKey, error })
          ) {
            updateNodeData(id, { isGenerating: false, generationStartedAt: null });
            return;
          }
        }
        // Persist the failure on the node so it stays visible until the next
        // submit — the request id is the handle support uses to trace it.
        // 只有 run 0 失败才终结 loading：非首 run 失败时 run 0 可能还在跑，
        // 它的成功补丁会清掉这里写的错误横幅。
        const rawErrorMessage =
          error instanceof Error && error.message
            ? error.message
            : String(error || t('common.error'));
        const displayErrorMessage = backendErrorToastMessage(error, t);
        // The task API already carries the provider's structured diagnosis
        // (stage / retryable / suggested action). Persist it so the failure
        // card can explain the block instead of dumping the relay's JSON.
        const diagnostics = resolveGenerationErrorDiagnostics(error, rawErrorMessage);
        updateNodeData(id, {
          ...(runIndex === 0
            ? { isGenerating: false, generationStartedAt: null }
            : {}),
          generationError: displayErrorMessage,
          // Keep the complete task/provider error for support copy. Only the
          // concise provider `message` is rendered on the node.
          generationErrorDetails: rawErrorMessage,
          generationErrorRequestId: diagnostics.requestId,
          generationErrorStage: diagnostics.stage,
          generationErrorSuggestedAction: diagnostics.suggestedAction,
          generationErrorCode: diagnostics.errorCode,
          generationErrorRetryable: diagnostics.retryable,
          ...(runIndex === 0 && taskJobId
            ? {
                generationRecoveryJobId: taskJobId,
                generationRecoveryTaskType: 'freezone_gen',
              }
            : {}),
          ...(data.expression_set_id ? { expression_status: "failed" } : {}),
        });
        // Re-throw so the caller can surface a single error dialog after all
        // concurrent attempts settle (rather than one dialog per failed image).
        throw error;
      }
    };

    const settledResults = await runGenerationQueue(
      Array.from({ length: total }, (_, runIndex) => runIndex),
      async (runIndex) => withGlobalGenerationSlot(
        () => runOne(runIndex),
        abortController.signal,
        // 上游还在排队 / 正在出图就先别提交：否则会拿它还没出好的图当参考图白跑一次。
        { nodeId: id, gate: () => upstreamGenerationGate(id) },
      ),
      GENERATION_CONCURRENCY_DEFAULT,
      (completed) => {
        if (!abortController.signal.aborted) {
          updateNodeData(id, { generationQueueCompleted: completed });
        }
      },
      abortController.signal,
    );
    // 全部尘埃落定后撤掉占位（失败的任务不留空槽，画册按实际完成数收口）。
    setAlbumPendingTotal(id, 0);
    // Leaving the canvas abandons local callbacks, not the recoverable job.
    if (abortController.signal.aborted) return;
    const latestNodeData = (useCanvasStore
      .getState()
      .nodes
      .find((node) => node.id === id)?.data ?? {}) as Record<string, unknown>;
    const recoveryJobId =
      typeof latestNodeData.generationRecoveryJobId === 'string'
        ? latestNodeData.generationRecoveryJobId.trim()
        : typeof latestNodeData.generationTaskJobId === 'string'
          ? latestNodeData.generationTaskJobId.trim()
          : '';
    const hasRejectedRun = settledResults.some((result) => result.status === 'rejected');
    if (completedUrls.length === 0 && recoveryJobId) {
      // A failed/late upstream job remains queryable. Clear only the local
      // loading bookkeeping; retain task handles for the recovery button.
      updateNodeData(id, {
        isGenerating: false,
        generationStartedAt: null,
        generationQueueTotal: null,
        generationQueueCompleted: null,
        generationQueueConcurrency: null,
        generationRecoveryJobId: recoveryJobId,
        generationRecoveryTaskType: 'freezone_gen',
        ...(latestNodeData.generationError
          ? {}
          : {
              generationError: hasRejectedRun
                ? '上游图片任务未能在等待窗口内返回结果'
                : '上游图片任务尚未返回结果',
              generationErrorDetails: '可以继续查询已有任务结果，不会重新生成。',
              generationErrorStage: 'query',
              generationErrorRetryable: true,
            }),
      });
    } else {
      updateNodeData(id, {
        ...CLEARED_GENERATION_TASK_PATCH,
      });
    }
    // Backend records each attempt (success or failure); pull the new entries.
    // Failures are surfaced directly on the failing node (request-id banner),
    // set per-target inside runOne's catch — no global modal.
    void refreshHistory();
    } finally {
      if (generationQueueAbortRef.current === abortController) {
        generationQueueAbortRef.current = null;
      }
      submittingRef.current = false;
      clearGenerationIntent(id);
    }
  }, [
    aspectRatio,
    canAutoCommitOnGenerate,
    selectedModel,
    cameraSelection,
    count,
    data.expression_set_id,
    data.extraParams,
    effectiveCount,
    id,
    supportsQuality,
    modelId,
    orderedReferenceUrls,
    prompt,
    quality,
    size,
    styleTemplateId,
    submitDisabled,
    isScriptStoryboardNode,
    shouldInlineUpstreamTextAsPrompt,
    updateNodeData,
    upstreamTextJoined,
    refreshHistory,
  ]);

  // Compatibility path for legacy client-owned automatic generation. Keep the
  // durable flag until the authoritative model catalog and submit contract are
  // ready; server-owned WorkflowRun tasks always win and never double-submit.
  useEffect(() => {
    const queued = data.canvas_auto_generate_once === true || data.expression_auto_generate === true;
    const decision = resolveImageAutoSubmitDecision({
      queued,
      isGenerating,
      imageModelsLoading,
      hasAuthoritativeImageModel,
      submitDisabled,
      hasServerTask: Boolean(data.generationTaskKey || data.generationTaskRefs),
    });
    if (decision === 'idle') {
      autoSubmitClaimedRef.current = false;
      return;
    }
    if (decision === 'wait' || autoSubmitClaimedRef.current) return;
    // 「有顺序的按顺序来」在这层就拦住：上游还在出图时先别消费排队标记。
    // 上游的图一落地，本节点因为订阅了上游内容会重新渲染，effect 带着新闭包再跑一次——
    // 提交时 payload 里的参考图才是**上游刚出的那张**（若在这里先提交再等，参考图会是旧的空快照）。
    // 30s 兜底重试：上游万一永远不出（没挂载上），本节点也不会把自己卡死。
    if (upstreamGenerationGate(id) !== 'go') {
      const retryTimer = setTimeout(
        () => setAutoSubmitRetryTick((tick) => tick + 1),
        UPSTREAM_GATE_RETRY_MS,
      );
      return () => clearTimeout(retryTimer);
    }
    autoSubmitClaimedRef.current = true;
    updateNodeData(id, {
      canvas_auto_generate_once: false,
      expression_auto_generate: false,
      ...(
        decision === 'submit' && data.expression_set_id
          ? { expression_status: "submitting" }
          : {}
      ),
    });
    if (decision === 'submit') void handleSubmit();
  }, [
    autoSubmitRetryTick,
    data.canvas_auto_generate_once,
    data.expression_auto_generate,
    data.expression_set_id,
    data.generationTaskKey,
    data.generationTaskRefs,
    handleSubmit,
    hasAuthoritativeImageModel,
    id,
    imageModelsLoading,
    isGenerating,
    submitDisabled,
    updateNodeData,
  ]);

  // ===== Step B: 场景资产节点的 "用作背景源" 操作 =====
  // scene_master / scene_reverse_master 节点上的按钮 → 打开 BackgroundCropperDialog
  // → 用户选择截图比例和区域 → 生成当前背景候选节点 → 自动 commit 主线。
  // 用户明确要求 \"不全用 master/reverse,要截图\" — 所以走 cropper 路径,不是
  // 直接 PATCH anchor (旧实现已替换)。
  // Step C: director_combined 节点上的「打开导演世界」按钮使用
  // the canvas frontend 内置同源 viewer,不跳旧外部导演台。
  const sourceMeta = (freezoneSource?.meta ?? {}) as Record<string, unknown>;
  const sourceEpisode = typeof sourceMeta.episode === "number"
    ? sourceMeta.episode
    : null;
  const sourceBeat = typeof sourceMeta.beat === "number"
    ? sourceMeta.beat
    : null;
  // 平面 source: master / reverse 走 BackgroundCropperDialog (用户选择截图比例和区域)。
  // 360 / 3GS 不走这条 — 它们统一进入 Director World，capture 入口在那里。
  const cropperSourceRoles = new Set(['scene_master', 'scene_reverse_master']);
  const canUseAsBackground = cropperSourceRoles.has(sourceRole);
  const canOpenDirectorStage = sourceRole === "director_combined"
    && sourceEpisode !== null
    && sourceBeat !== null;
  const [bgCropperOpen, setBgCropperOpen] = useState(false);
  const [directorStageBusy, setDirectorStageBusy] = useState(false);
  const [directorStageOpen, setDirectorStageOpen] = useState(false);
  const [directorStageManifest, setDirectorStageManifest] = useState<DirectorStageManifest | null>(null);
  // 从 canvas metadata 拿到当前镜头的 episode/beat 定位信息 (selectedBackground 在
  // beat preset 里 emit 时跟 beat-scope 节点同步,但本节点 (scene_master 等) 来自
  // _add_scene_refs 没带 episode/beat meta — 从 canvas metadata.preset 兜底)。
  const canvasMetaForBeat = getFreezoneCanvasMetadata();
  const canvasPresetMeta = (canvasMetaForBeat?.preset as
    | { episode?: number; beat?: number }
    | undefined) ?? undefined;
  const effectiveEpisode = sourceEpisode ?? canvasPresetMeta?.episode ?? null;
  const effectiveBeat = sourceBeat ?? canvasPresetMeta?.beat ?? null;

  useEffect(() => {
    if (!shouldInlineUpstreamTextAsPrompt) return;
    if (isComposingRef.current) return;
    if (hasUserEditedPromptRef.current) return;
    if (externalPrompt.trim().length > 0) return;
    const nextPrompt = upstreamTextJoined.trim();
    if (!nextPrompt) return;
    setPromptDraft(nextPrompt);
  }, [
    externalPrompt,
    shouldInlineUpstreamTextAsPrompt,
    upstreamTextJoined,
  ]);

  const handleOpenDirectorStageInline = useCallback(async () => {
    if (!canOpenDirectorStage) return;
    const projectId = readUrl().project;
    if (!projectId || effectiveEpisode === null || effectiveBeat === null) return;
    setDirectorStageBusy(true);
    try {
      const manifest = await getBeatDirectorStageManifest(projectId, effectiveEpisode, effectiveBeat);
      setDirectorStageManifest(manifest);
      setDirectorStageOpen(true);
    } catch (err) {
      console.error('[director-stage] manifest fetch failed', err);
    } finally {
      setDirectorStageBusy(false);
    }
  }, [canOpenDirectorStage, effectiveEpisode, effectiveBeat]);

  const handleDirectorCaptureCombined = useCallback(
    async (blob: Blob, meta: ThreeDDirectorCaptureMeta) => {
      const projectId = readUrl().project;
      if (!projectId || effectiveEpisode === null || effectiveBeat === null) {
        throw new Error('缺少项目或镜头上下文');
      }

      let imageUrl = meta.controlFrameUrl
        ?? meta.controlFrameBundle?.urls?.combined
        ?? '';
      if (!imageUrl) {
        const uploaded = await uploadFreezoneImage(
          projectId,
          blob,
          `director_combined_${Date.now()}.png`,
          { timeoutMs: false },
        );
        imageUrl = uploaded.url;
      }

      const nextBundle = meta.controlFrameBundle ?? data.director_control_bundle;

      updateNodeData(id, {
        imageUrl,
        previewImageUrl: withImageCacheBust(imageUrl, Date.now()),
        ...(nextBundle ? { director_control_bundle: nextBundle } : {}),
        committed_at: new Date().toISOString(),
        committed_slot_url: imageUrl,
        slot_target: {
          kind: 'director_render',
          episode: effectiveEpisode,
          beat: effectiveBeat,
        },
      });
      canvasEventBus.publish('freezone/assets-updated', undefined);
    },
    [data.director_control_bundle, effectiveEpisode, effectiveBeat, id, updateNodeData],
  );

  // 视觉态从 4 个 derived flag 派生(see mainlineNodeFlags):
  //   preset_locked      — preset_managed === true:amber 实线 + lock badge
  //   candidate_pushable — user_spawned + slot_target:amber 虚线 + push badge
  //   context_only       — 有 mainline_context 但无 slot_target:cyan 细线 + context chip
  //   ordinary           — 都没有:默认白色 border
  //
  const mainlineFlags = useMemo(
    () => nodeMainlineFlags({ data, id, type: 'imageGenNode', position: { x: 0, y: 0 } } as never),
    [data, id],
  );
  const visualState = mainlineNodeVisualState(mainlineFlags);
  const mainlineCanvasReadonly = mainlineFlags.isPresetManaged && !canAutoCommitOnGenerate;
  const cardToneClass = (() => {
    switch (visualState) {
      case 'preset_locked':
        return canvasNodeFrameClass({ mainline: true });
      case 'candidate_pushable':
        return canvasNodeFrameClass({ mainline: true, dashed: true });
      case 'context_only':
        return canvasNodeFrameClass({ mainline: true });
      case 'ordinary':
      default:
        return canvasNodeFrameClass();
    }
  })();
  // 画册展开时一并隐藏 OpsPanel——展开瞬间已 setSelectedNode(null)，这里再兜
  // 一道，防止展开后用户点节点重新选中时面板叠到宫格上。
  const showImageOpsPanel =
    selected && !isBoxSelecting && !hasActiveOverlay && !mainlineCanvasReadonly && !albumExpanded;

  return (
    <div
      ref={albumRootRef}
      className="group relative h-full w-full overflow-visible transition-[width,height] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none"
      style={{ width: resolvedWidth, height: resolvedHeight }}
      onClick={() => setSelectedNode(id)}
    >
      {/* 叠卡画册的卡片边缘：从主图右下方探出，张数与画册一致（最多露 3 张）。
          先渲染、被后面的主卡覆盖，只露出错位的边。 */}
      {hasAlbum && !albumExpanded && previewUrl && (
        <>
          {Array.from({ length: Math.min(albumTotalSlots - 1, 3) }, (_, index) => {
            const step = index + 1;
            return (
              // 点探出的卡片边也能展开画册（和点数量徽标等效）。
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
                  // 仿 TapNow：后面的卡依次上下内缩、向右探出、微旋转——
                  // 露出的是一条条「卡片边」，而不是整块色板。
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

      {/* 画册展开时隐藏浮动标题和分辨率角标——画册容器自带「画册 · N 张」头部，
          两者都浮在节点上沿同一位置，叠在一起显示错乱。 */}
      {!albumExpanded && (
        <>
          <NodeHeader
            className={NODE_HEADER_FLOATING_POSITION_CLASS}
            icon={<ImageIcon className="h-4 w-4" />}
            titleText={resolvedTitle}
            editable
            onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
          />
          {visiblePreviewUrl && naturalSize ? (
            <ResolutionHonestyBadge
              media="image"
              requestedTier={
                (data as ImageGenNodeData).lastRequestedImageSize ?? size
              }
              actualWidth={naturalSize.width}
              actualHeight={naturalSize.height}
            />
          ) : null}
        </>
      )}
      <CandidateBindingBadges roles={candidateBindingRoles} />

      <NodeResizeHandle
        minWidth={configuredAspectMin.minWidth}
        minHeight={configuredAspectMin.minHeight}
        maxWidth={MAX_WIDTH}
        maxHeight={MAX_HEIGHT}
        keepAspectRatio
      />

      {!hasGeneratedResult && !referenceImageUrl && !isGenerating && !generationError && (
        <NodeSideActionRail nodeId={id} autoHide selected={Boolean(selected)}>
          <button
            type="button"
            disabled={isUploading}
            onClick={(event) => {
              event.stopPropagation();
              handlePickFile();
            }}
            onPointerDown={(event) => event.stopPropagation()}
            title="上传图片"
            className={NODE_SIDE_ACTION_BUTTON_CLASS}
          >
            {isUploading ? (
              <Loader2 className={`${NODE_SIDE_ACTION_ICON_CLASS} animate-spin`} />
            ) : (
              <Upload className={NODE_SIDE_ACTION_ICON_CLASS} />
            )}
            <span>{isUploading ? '上传中' : '上传图片'}</span>
          </button>
        </NodeSideActionRail>
      )}

      <div
        className={`relative flex h-full w-full items-center justify-center ${visiblePreviewUrl ? 'overflow-hidden' : 'overflow-visible'} rounded-[var(--node-radius)] border transition-colors ${visiblePreviewUrl ? CANVAS_NODE_PANEL_SURFACE_CLASS : CANVAS_NODE_INPUT_SURFACE_CLASS} ${cardToneClass} ${visiblePreviewUrl ? '' : CANVAS_NODE_INPUT_BODY_FRAME_CLASS} ${
          // 画册展开时藏起节点本体的图片卡——半透明的画册容器盖不严，
          // 底下的主图会透出来叠在宫格头部。
          albumExpanded && hasAlbum ? 'invisible' : ''
        }`}
      >
        {visiblePreviewUrl ? (
          <>
            <CanvasNodeImage
              src={visiblePreviewUrl}
              alt={resolvedTitle}
              viewerSourceUrl={visiblePreviewUrl}
              onLoad={(event) => {
                const naturalW = event.currentTarget.naturalWidth;
                const naturalH = event.currentTarget.naturalHeight;
                if (naturalW > 0 && naturalH > 0) {
                  setNaturalSize((prev) =>
                    prev && prev.width === naturalW && prev.height === naturalH
                      ? prev
                      : { width: naturalW, height: naturalH },
                  );
                }
                const forceNaturalSize = shouldForceNaturalImageSize(data as Record<string, unknown>);
                if (data.isSizeManuallyAdjusted === true && !forceNaturalSize) {
                  return;
                }
                const nextAspectRatio = aspectRatioFromImageDimensions(
                  event.currentTarget.naturalWidth,
                  event.currentTarget.naturalHeight,
                );
                if (!nextAspectRatio) {
                  return;
                }
                const nextSize = resolveMinEdgeFittedSize(nextAspectRatio, {
                  minWidth: MIN_WIDTH,
                  minHeight: MIN_HEIGHT,
                });
                const displaySizeMismatch =
                  Math.abs(resolvedWidth - nextSize.width) > 1 ||
                  Math.abs(resolvedHeight - nextSize.height) > 1;
                if (nextAspectRatio !== data.aspectRatio || displaySizeMismatch) {
                  updateNodeSize(id, nextSize, {
                    lockManualSize: forceNaturalSize ? false : undefined,
                    data: {
                      aspectRatio: nextAspectRatio,
                      imageNaturalWidth: event.currentTarget.naturalWidth,
                      imageNaturalHeight: event.currentTarget.naturalHeight,
                      imageAspectRatioUpdatedAt: Date.now(),
                    },
                  });
                }
              }}
              className="h-full w-full object-contain"
            />
            {!hasGeneratedResult && referenceImageUrl && !isGenerating && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  handleClearReference();
                }}
                title="移除参考图"
                className="nodrag absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded-full bg-black/55 text-white transition-colors hover:bg-black/75"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
            {/* 多张自带参考图只画第 1 张（节点预览就一张），不标出来的话其余几张
                在界面上完全不可见 —— 用户以为只有一张，实际提交了整组。
                分镜图节点上就是「双人镜头第二个人物」被带出去却看不见。 */}
            {!hasGeneratedResult && !isGenerating && ownReferenceUrls.length > 1 && (
              <span
                className="nodrag pointer-events-none absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-black/55 px-2 py-0.5 text-[11px] font-medium tabular-nums text-white"
                title={`本节点自带 ${ownReferenceUrls.length} 张参考图，提交时整组一起送出（@图片1 起按序编号）`}
              >
                <Images className="h-3 w-3" />
                {ownReferenceUrls.length}
              </span>
            )}
            {/* 画册数量徽标：hover 节点时出现，hover 徽标时箭头下探，点击展开画册。 */}
            {hasAlbum && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  handleToggleAlbumExpanded();
                }}
                onPointerDown={(event) => event.stopPropagation()}
                title={`展开 ${albumTotalSlots} 张生成结果`}
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
          </>
        ) : isGenerating && historyPreviewUrl ? (
          // 生成进行中，但用户点了历史记录预览：临时显示那张历史图，新图仍在
          // 后台生成。顶部 pill 提示「生成中」，右上「返回」回到 loading 遮罩。
          // 用原生 <img>（非 CanvasNodeImage）避免 onLoad 按预览图改节点尺寸。
          <div className="relative h-full w-full">
            <img
              src={resolveImageDisplayUrl(historyPreviewUrl)}
              alt=""
              className="h-full w-full object-contain"
              draggable={false}
              onClick={(event) => event.stopPropagation()}
            />
            <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between gap-2 p-2">
              <span className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 text-[11px] text-white/90 backdrop-blur">
                <Loader2 className="h-3 w-3 animate-spin" />
                新图片生成中…
              </span>
              <button
                type="button"
                className="nodrag pointer-events-auto inline-flex items-center gap-1 rounded-full bg-black/60 px-2.5 py-1 text-[11px] text-white/90 backdrop-blur transition-colors hover:bg-black/75"
                onClick={(event) => {
                  event.stopPropagation();
                  setHistoryPreviewUrl(null);
                }}
              >
                <X className="h-3 w-3" />
                返回
              </button>
            </div>
          </div>
        ) : isGenerating ? (
          <div className="h-full w-full" />
        ) : generationError ? (
          // Failed with no result yet: keep the card empty so only the centered
          // error banner shows — placeholder + upload affordances would clutter it.
          <div className="h-full w-full" />
        ) : (
          <div className="flex h-full w-full items-center px-8 text-text-muted/55">
            {isUploading ? (
              <div className="flex w-full flex-col items-center justify-center gap-2">
                <Loader2 className="h-7 w-7 animate-spin opacity-70" />
                <span className="text-[12px] leading-6">上传中…</span>
              </div>
            ) : data.scriptShotKeyframeSourceNodeId ? (
              <div className="flex w-full flex-col items-center justify-center gap-2">
                <ImageIcon className="h-9 w-9 text-text-muted/46" aria-hidden />
                <span className="text-[12px] leading-6">状态画面待生成</span>
              </div>
            ) : isConnected ? (
              // 已连线：不再显示文字 CTA，只在节点中间放一个图标（对齐 libtv）。
              <div className="flex w-full items-center justify-center">
                <ImageIcon className="h-9 w-9 text-text-muted/46" aria-hidden />
              </div>
            ) : (
              <>
                <div className="flex min-h-0 flex-col justify-center gap-2 py-4">
                  <div className="text-xs text-[var(--canvas-node-input-helper)]">试试：</div>
                  <div className="flex flex-col gap-0.5">
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      handleSpawnUpstreamImage();
                    }}
                    onPointerDown={(event) => event.stopPropagation()}
                    title="新建一个上游图片节点用作参考"
                    className="nodrag -mx-2 inline-flex items-center gap-3 rounded-lg px-2 py-2 text-sm text-text-dark transition-colors hover:bg-white/[0.08]"
                  >
                    <Upload className="h-4 w-4 text-text-muted/90" />
                    <span>图生图</span>
                  </button>
                  </div>
                </div>
                <ImageIcon className="ml-auto mr-20 h-9 w-9 text-text-muted/46" aria-hidden />
              </>
            )}
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void handleUploadFile(file);
          }}
        />

        {isGenerating && !historyPreviewUrl && (
          <NodeGenerationOverlay
            startedAt={data.generationStartedAt ?? null}
            progress={generationTask?.progress ?? null}
            hasBackground={Boolean(visiblePreviewUrl)}
            onCancel={() => void cancelNodeTask()}
            cancelPending={isCancellingNodeTask}
          />
        )}

        {!isGenerating && generationError && (
          <div className="absolute inset-0 z-10">
            <NodeGenerationErrorCard
              title={t('node.imageNode.generationFailed')}
              message={generationError}
              details={generationErrorDetails}
              requestId={generationErrorRequestId}
              stage={generationErrorStage}
              suggestedAction={generationErrorSuggestedAction}
              recoverBusy={isRecovering}
              onRecover={canRecoverImageTask ? () => void handleRecoverImageTask() : undefined}
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
          </div>
        )}
      </div>

      {/* 展开的画册宫格：覆盖在节点位置向右下铺开，每格与节点等尺寸。
          外层一圈「组」式轮廓（边框 + 弱底色 + 左上角标签），强调这组图是
          一个组合。hover 单格出现「应用到画布」+ 下载；点击图片设为主图。 */}
      {albumExpanded && hasAlbum && (
        // 容器不带 nodrag、也不拦 pointerdown——按住画册任意处即可拖动整个节点
        // （组合一起走）。按下时记录起点，cell 的 onClick 据此区分「点击选主图」
        // 和「拖动后松手」（React Flow 拖完浏览器仍会补发 click）。
        <div
          className="nowheel absolute -left-3 -top-3 z-[80] cursor-grab rounded-2xl border border-white/15 bg-white/[0.045] p-3 shadow-[0_16px_48px_rgba(0,0,0,0.4)] backdrop-blur-[2px] active:cursor-grabbing"
          style={{ width: resolvedWidth * 2 + 12 + 24 }}
          onClick={(event) => event.stopPropagation()}
          onPointerDownCapture={(event) => {
            albumPointerDownPosRef.current = { x: event.clientX, y: event.clientY };
          }}
        >
          <div className="mb-2 flex items-center gap-1.5 px-1 text-[12px] font-medium text-white/60">
            <ImageIcon className="h-3.5 w-3.5 text-white/45" />
            画册 · {albumTotalSlots} 张
          </div>
          <div className="grid grid-cols-2 gap-3">
          {albumUrls.map((url, index) => {
            const isMain = url === data.imageUrl;
            return (
              // 直接点击图片即设为主图并收拢画册（不再需要单独的「设为主图」按钮）。
              <div
                key={`album-cell-${index}`}
                role="button"
                tabIndex={-1}
                title="点击设为主图"
                onClick={(event) => {
                  event.stopPropagation();
                  // 拖动画册（移动节点）后松手补发的 click 不算选主图。
                  const start = albumPointerDownPosRef.current;
                  if (
                    start
                    && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5
                  ) {
                    return;
                  }
                  handleSetAlbumMainImage(url);
                }}
                className={`group/albumcell relative cursor-pointer overflow-hidden rounded-[var(--node-radius)] border bg-[#1b1b1d] shadow-[0_12px_32px_rgba(0,0,0,0.45)] transition-colors ${
                  isMain
                    ? 'border-accent/80 ring-2 ring-accent/40'
                    : 'border-white/12 hover:border-white/35'
                }`}
                style={{ width: resolvedWidth, height: resolvedHeight }}
              >
                <img
                  src={resolveImageDisplayUrl(url)}
                  alt=""
                  className="h-full w-full object-cover"
                  draggable={false}
                />
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    handleApplyAlbumImageToCanvas(url);
                  }}
                  title="把这张图作为独立图片节点放到画布上"
                  className="nodrag absolute left-2 top-2 z-10 hidden h-7 items-center gap-1 rounded-md bg-black/70 px-2.5 text-[12px] font-medium text-white backdrop-blur-sm transition-colors hover:bg-black/90 group-hover/albumcell:inline-flex"
                >
                  <Upload className="h-3.5 w-3.5" />
                  应用到画布
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleDownloadAlbumImage(url, index);
                  }}
                  title="下载这张图片"
                  className="nodrag absolute right-2 top-2 z-10 hidden h-7 w-7 items-center justify-center rounded-full bg-black/70 text-white backdrop-blur-sm transition-colors hover:bg-black/90 group-hover/albumcell:inline-flex"
                >
                  <Download className="h-3.5 w-3.5" />
                </button>
                {isMain && (
                  <span className="absolute bottom-2 left-2 z-10 rounded-md bg-black/65 px-2 py-0.5 text-[11px] font-medium text-white backdrop-blur-sm">
                    主图
                  </span>
                )}
              </div>
            );
          })}
          {/* 还在生成中的槽位：占位骨架，完成一张替换一张。 */}
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

      {/*
        Step B + C: 场景资产 / 导演中间产物节点的内联 action 按钮
        (scene_master / scene_reverse_master 加 "用作背景源" → 打开 cropper
         dialog 选 16:9 区域 → 生成当前背景候选并自动 commit;
         director_combined 加 "打开导演世界" → 同源 viewer dialog)。
        button 浮在节点右下角,selected 时可见,避免占用节点 body 空间。
      */}
      {selected && (canUseAsBackground || canOpenDirectorStage) && (
        <div className="nodrag absolute bottom-2 right-2 z-[6] flex gap-1">
          {canUseAsBackground && (
            <button
              type="button"
              disabled={effectiveEpisode === null || effectiveBeat === null}
              onClick={(event) => {
                event.stopPropagation();
                setBgCropperOpen(true);
              }}
              className="inline-flex h-6 items-center gap-1 rounded-md border border-amber-300/55 bg-[rgba(120,77,19,0.78)] px-2 text-[10px] font-medium text-amber-100 shadow-[0_0_0_1px_rgba(0,0,0,0.45)] hover:bg-[rgba(140,90,22,0.88)] disabled:cursor-not-allowed disabled:opacity-50"
              title={`从 ${sourceRole === 'scene_master' ? 'scene_master' : 'scene_reverse_master'} 选一个 16:9 区域写入本 beat 的 selected_background.png — beat 工作台后续 sketch/render 会用这张做背景锚点`}
            >
              📐 截取背景
            </button>
          )}
          {canOpenDirectorStage && (
            <button
              type="button"
              disabled={directorStageBusy}
              onClick={(event) => {
                event.stopPropagation();
                void handleOpenDirectorStageInline();
              }}
              className={`inline-flex h-6 items-center gap-1 rounded-md border border-sky-300/55 px-2 text-[10px] font-medium shadow-[0_0_0_1px_rgba(0,0,0,0.45)] ${
                directorStageBusy
                  ? 'cursor-not-allowed bg-sky-400/10 text-sky-100/60'
                  : 'bg-[rgba(15,67,107,0.78)] text-sky-100 hover:bg-[rgba(22,90,140,0.88)]'
              }`}
              title={t("viewer.threeD.openDirectorWorldTitle")}
            >
              {directorStageBusy
                ? t("viewer.threeD.openingDirectorWorld")
                : `🎬 ${t("viewer.threeD.directorWorld")}`}
            </button>
          )}
        </div>
      )}

      {/*
        自由 canvas 上 ImageGenNode 的全功能 ops panel (camera / model picker /
        free reference upload / generation count / style picker / submit ...).
        Preset-managed source nodes hide this panel; user-spawned nodes keep it.
      */}
      {showImageOpsPanel && (
        <OperationPanelShell
          expanded={panelExpanded}
          onCollapse={() => setPanelExpanded(false)}
          inlineClassName={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col rounded-[var(--node-radius)] ${CANVAS_NODE_OPS_PANEL_CLASS}`}
          inlineStyle={{
            top: `calc(100% + ${OPERATIONS_PANEL_GAP}px)`,
            height: panelHeight,
            width: panelWidth,
          }}
          modalStyle={{
            width: `min(${OPERATIONS_PANEL_EXPANDED_MIN_WIDTH}px, 92vw)`,
            height: `min(${OPERATIONS_PANEL_EXPANDED_HEIGHT}px, 86vh)`,
          }}
        >
          <PanelExpandButton
            expanded={panelExpanded}
            onToggle={() => setPanelExpanded((v) => !v)}
            className="absolute right-2 top-2 z-20"
          />
          <div className="shrink-0 border-b border-white/[0.08] px-3 pb-2 pr-10 pt-3">
            <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
            <StyleChip
              selectedId={styleTemplateId}
              selectedLabel={selectedStyle?.label ?? null}
              onChange={(nextId) => updateNodeData(id, { styleTemplateId: nextId })}
              onOpenChange={setStylePickerOpen}
            />
            <CameraChip
              selection={cameraSelection}
              summary={cameraSummary}
              onChange={(next) => updateNodeData(id, { cameraSelection: next })}
            />
            <NodeContextPromptPaletteButton
              nodeId={id}
              onInsert={insertContextPaletteEntry}
            />
            <CanvasReferencePickChip
              nodeId={id}
              nodeType={CANVAS_NODE_TYPES.imageGen}
            />
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setIsAssetLibraryOpen(true);
              }}
              className={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} group/asset px-1.5`}
              title="从资产库选择参考图（人物 / 场景 / 道具）"
            >
              <Library className={`${NODE_TEXT_CONTROL_ICON_CLASS} group-hover/asset:text-text-dark`} />
              <span>资产库</span>
            </button>
            <button
              type="button"
              title={`根据当前模型 ${selectedModel?.label ?? modelId} 优化提示词`}
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
                  : '优化提示词'}
              </span>
            </button>
            {upstreamTextContents.map((content) => (
              <ReferenceTextChip
                key={content.nodeId}
                nodeId={content.nodeId}
                text={content.text ?? ''}
                sourceLabel={content.displayName ?? content.nodeType}
                onDetach={handleDetachUpstream}
              />
            ))}
            </div>
            <div className="mt-2 flex min-h-9 flex-wrap items-center gap-x-2 gap-y-1.5">
              <span className="shrink-0 text-[11px] font-medium text-text-muted/85">参考素材</span>
            {orderedReferenceUrls.length > 0 ? (
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                {orderedReferenceUrls.map((referenceUrl, referenceIndex) => {
                  const upstreamContent = upstreamImageContents.find(
                    (content) => normalizeReferenceUrl(content.imageUrl) === referenceUrl,
                  );
                  const isOwnReference = normalizedOwnReferenceUrls.includes(referenceUrl);
                  const isExpressionReference = referenceUrl === normalizedExpressionControlImageUrl;
                  const url = resolveImageDisplayUrl(referenceUrl);
                  const sourceLabel = isOwnReference
                    ? '本节点参考素材'
                    : isExpressionReference
                      ? '表情控制素材'
                      : `来自上游 · ${upstreamContent?.displayName ?? upstreamContent?.nodeType ?? '图片节点'}`;
                  return (
                    <div
                      key={`reference-image-${referenceIndex}-${referenceUrl}`}
                      className={NODE_REFERENCE_MEDIA_CHIP_CLASS}
                      title={`${sourceLabel}｜点一下写入提示词`}
                      onMouseEnter={(event) => {
                        setRefHover({
                          imageUrl: url,
                          rect: event.currentTarget.getBoundingClientRect(),
                        });
                      }}
                      onMouseLeave={() => setRefHover(null)}
                      onClick={(event) => {
                        // 与视频节点同口径：点缩略图 = 把 `@图片N` 写进提示词。
                        // 跳上游节点用双击，免得两个手势抢同一个点击。
                        event.stopPropagation();
                        handleInsertReference(`图片${referenceIndex + 1}`);
                      }}
                      onDoubleClick={(event) => {
                        event.stopPropagation();
                        setRefHover(null);
                        if (upstreamContent) setSelectedNode(upstreamContent.nodeId);
                      }}
                      role="button"
                      tabIndex={-1}
                    >
                      <img
                        src={url}
                        alt=""
                        className="h-full w-full object-cover"
                        draggable={false}
                      />
                      <ReferenceChipNumberBadge typeIndex={referenceIndex + 1} />
                      <button
                        type="button"
                        title="取消引用此素材"
                        className={NODE_REFERENCE_MEDIA_DETACH_CLASS}
                        onMouseDown={(event) => event.stopPropagation()}
                        onClick={(event) => {
                          event.stopPropagation();
                          setRefHover(null);
                          if (isOwnReference) handleRemoveOwnReference(referenceUrl);
                          else if (isExpressionReference) handleRemoveExpressionReference();
                          else if (upstreamContent) handleDetachUpstream(upstreamContent.nodeId);
                        }}
                      >
                        <X className="h-3 w-3" strokeWidth={2.5} />
                      </button>
                    </div>
                  );
                })}
              </div>
            ) : (
              <span className="text-[11px] text-text-muted/60">添加人物、场景或道具图片作为参考</span>
            )}
            </div>
          </div>

          <PromptMentionEditor
            ref={promptEditorRef}
            value={prompt}
            onChange={(next) => {
              hasUserEditedPromptRef.current = hasImageGenPromptOverride(next);
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
              hasUserEditedPromptRef.current = hasImageGenPromptOverride(next);
              setPromptDraft(next);
              updateNodeData(id, { prompt: next });
            }}
            candidates={mentionCandidates}
            placeholder={
              upstreamTextJoined.length > 0
                ? '上游内容已自动接入，可继续补充提示词…'
                : '描述你想要生成的画面内容，@引用素材'
            }
            className={`nodrag nowheel min-h-0 w-full flex-1 overflow-y-auto whitespace-pre-wrap break-words border-none bg-transparent px-3 py-2 text-sm leading-6 text-text-dark outline-none ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS}`}
          />

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
                onChange={(nextModelId) => updateNodeData(id, { model: nextModelId })}
                popoverPlacement="top"
                getOptionDisabledReason={(model) => (
                  orderedReferenceUrls.length > 0
                    ? imageEditModelDisabledReason(model)
                    : null
                )}
              />
              <AspectSizeChip
                aspectRatio={aspectRatio}
                aspectOptions={imageAspectOptions}
                quality={quality}
                qualityOptions={qualityOptions.map((option) => ({
                  value: option.value as ImageQuality,
                  label: option.label,
                }))}
                supportsCustomAspectRatio={runtimeImageModel.supportsCustomAspectRatio}
                showQuality={supportsQuality}
                onChange={(patch) => updateNodeData(id, patch)}
              />
              <AdvancedParamsChip
                schema={runtimeImageModel.extraParamsSchema ?? []}
                extraParams={data.extraParams}
                defaultExtraParams={runtimeImageModel.defaultExtraParams}
                onChange={(key, value) =>
                  updateNodeData(id, {
                    extraParams: {
                      ...(data.extraParams ?? {}),
                      [key]: value,
                    },
                  })
                }
              />
              {!canAutoCommitOnGenerate && (
                <CountSelect
                  value={count}
                  onChange={(nextCount) => updateNodeData(id, { count: nextCount })}
                />
              )}
              <button
                type="button"
                title="翻译提示词（中英文互译）"
                disabled={isTranslatingPrompt || isGenerating || prompt.trim().length === 0}
                onClick={(event) => {
                  event.stopPropagation();
                  void handleTranslatePrompt();
                }}
                className={`${NODE_INLINE_ICON_BUTTON_CLASS} ${
                  isTranslatingPrompt
                    ? NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS
                    : ''
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
                title={
                  imageModeBlockedReason
                  ?? (!hasAuthoritativeImageModel ? '正在载入已配置的生图模型' : '生成')
                }
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
      {selected && !isBoxSelecting && !hasActiveOverlay && !panelExpanded && !stylePickerOpen && hasCompletedHistoryRecords(historyRecords) && (
        <NodePanelZoomAnchor>
          <div
            className={`nodrag pointer-events-auto absolute left-1/2 z-[300] -translate-x-1/2 rounded-[var(--node-radius)] ${CANVAS_NODE_OPS_PANEL_CLASS} ${NODE_OPS_PANEL_ENTER_CLASS} px-3 py-2`}
            style={{
              top: `calc(100% + ${OPERATIONS_PANEL_GAP * 2 + panelHeight}px)`,
              width: panelWidth,
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
                // 预览态下高亮正在预览的历史条，否则高亮当前主图。
                if (isGenerating && historyPreviewUrl) {
                  return url === historyPreviewUrl;
                }
                return url === data.imageUrl;
              }}
            />
          </div>
        </NodePanelZoomAnchor>
      )}
      {refHover && refPreviewStyle
        && createPortal(
          <div
            className="pointer-events-none fixed z-[10001] overflow-hidden rounded-lg border border-white/15 bg-surface-dark/95 shadow-xl"
            style={{
              left: refPreviewStyle.left,
              top: refPreviewStyle.top,
              width: refPreviewStyle.size,
              height: refPreviewStyle.size,
            }}
          >
            <img
              src={refHover.imageUrl}
              alt=""
              className="h-full w-full object-cover"
              draggable={false}
            />
          </div>,
          document.body,
        )}

      {/* Step B: 平面 source (master/reverse) 的截取背景 dialog。
          Pano360 / 3GS 不走这条 — 它们用各自 viewer 上的 capture 按钮。 */}
      {canUseAsBackground && effectiveEpisode !== null && effectiveBeat !== null && (
        <BackgroundCropperDialog
          isOpen={bgCropperOpen}
          onClose={() => setBgCropperOpen(false)}
          sourceUrl={typeof data.imageUrl === 'string' ? data.imageUrl : ''}
          sourceLabel={sourceRole === 'scene_master' ? 'master' : 'reverse'}
          aspectOptions={SELECTED_BACKGROUND_CROP_ASPECT_OPTIONS}
          onConfirmBlob={async (blob, filename) => {
            await uploadAndAutoCommitSelectedBackgroundCandidate(
              { episode: effectiveEpisode, beat: effectiveBeat },
              blob,
              filename,
              {
                sourceNodeId: id,
                label: t("viewer.threeD.selectedBackgroundOutputLabel"),
                successMessage: t("viewer.threeD.selectedBackgroundCommitSuccess", {
                  episode: effectiveEpisode,
                  beat: effectiveBeat,
                }),
              },
            );
          }}
          onCandidateSuccess={() => setBgCropperOpen(false)}
          onError={(msg) => console.warn('[bg-cropper]', msg)}
        />
      )}
      {canOpenDirectorStage && (
        <ThreeDDirectorDialog
          open={directorStageOpen}
          onOpenChange={setDirectorStageOpen}
          manifest={directorStageManifest}
          title={t("viewer.threeD.beatDirectorWorld")}
          description={t("viewer.threeD.beatDirectorWorldDescription")}
          viewerPurpose="beat"
          autoCommitDirectorCombined
          onSubmitDirectorCombined={handleDirectorCaptureCombined}
        />
      )}
      <AssetLibraryModal
        open={isAssetLibraryOpen}
        project={readUrl().project ?? null}
        allowedMedia={['image']}
        onClose={() => setIsAssetLibraryOpen(false)}
        onConfirm={(selections) => spawnAssetLibraryReferences(selections)}
      />
    </div>
  );
}, areCanvasNodePropsEqual);

ImageGenNode.displayName = 'ImageGenNode';
