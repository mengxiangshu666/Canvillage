// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { createPortal } from 'react-dom';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import {
  AlignJustify,
  ArrowDown,
  ArrowUp,
  AlertCircle,
  Clapperboard,
  Copy,
  Download,
  Expand,
  FileText,
  Film,
  ImageIcon,
  ImagePlus,
  Languages,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  User,
  Video,
  Wand2,
  X,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  SCRIPT_NODE_SIZE,
  isAudioNode,
  isExportImageNode,
  isImageEditNode,
  isImageGenNode,
  isTextAnnotationNode,
  isUploadNode,
  isVideoNode,
  type CanvasNode,
  type CanvasNodeType,
  type ScriptAssetViewMode,
  type ScriptAssetGenConfig,
  type ScriptGenAction,
  type ScriptImageGenConfig,
  type ScriptNodeData,
  type StoryVideoGenConfig,
} from '@/features/canvas/domain/canvasNodes';
import { isRenderableImageSrc, resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { focusDerivedNodes } from '@/features/canvas/application/focusDerivedNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import {
  deleteScriptShotRowAt,
  duplicateScriptShotRowAt,
  ensureScriptShotIdentities,
  insertScriptShotRowAfter,
  moveScriptShotRow,
  normalizeScriptResultIdentities,
} from '@/features/canvas/domain/scriptShotIdentity';
import {
  NodeHeader,
  NODE_HEADER_FLOATING_POSITION_CLASS,
} from '@/features/canvas/ui/NodeHeader';
import { NodeResizeHandle } from '@/features/canvas/ui/NodeResizeHandle';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import { NodeGenerationErrorCard, humanizeGenerationError } from '@/features/canvas/ui/NodeGenerationErrorCard';
import { RegenerateButton } from '@/features/canvas/ui/RegenerateButton';
import { EditableTableCell } from '@/features/canvas/ui/EditableTableCell';
import { resolveGenerationErrorDiagnostics } from '@/features/canvas/application/generationErrorReport';
import { CLEARED_GENERATION_ERROR_PATCH, CLEARED_GENERATION_TASK_PATCH } from '@/features/canvas/application/generationTaskArbitration';
import {
  CANVAS_NODE_INPUT_FRAME_CLASS,
  CANVAS_NODE_INPUT_PLACEHOLDER_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  CANVAS_NODE_PANEL_SURFACE_CLASS,
  canvasNodeFrameClass,
} from '@/features/canvas/ui/nodeFrameStyles';
import { OperationPanelShell } from '@/features/canvas/ui/OperationPanelShell';
import { PanelExpandButton } from '@/features/canvas/ui/PanelExpandButton';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  fetchFreezoneStoryScriptResult,
  fetchFreezoneTextTranslateResult,
  submitFreezoneStoryScript,
  submitFreezoneTextTranslate,
  type FreezoneGenerationHistoryRecord,
  type FreezoneStoryScriptResult,
  type FreezoneStoryScriptRow,
} from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import { generationTaskDescriptor } from '@/features/canvas/application/resumeGeneration';
import { submitScriptLocalRewrite } from '@/features/canvas/application/submitScriptLocalRewrite';
import { useUpstreamNodes } from '@/features/canvas/application/useUpstreamGraph';
import { useNodeGenerationTaskState } from '@/features/canvas/application/useNodeGenerationTaskState';
import { useCancelNodeGeneration } from '@/features/canvas/application/useCancelNodeGeneration';
import { useNodeGenerationHistory } from '@/features/canvas/hooks/useNodeGenerationHistory';
import {
  NodeGenerationHistory,
  hasCompletedHistoryRecords,
} from '@/features/canvas/ui/NodeGenerationHistory';
import { DirectModelPicker } from '@/features/canvas/ui/DirectModelPicker';
import { readUrl } from '@/lib/url-params';
import {
  resolveDirectCanvasModelId,
  useDirectModelCatalog,
} from '@/features/canvas/hooks/useDirectModelCatalog';
import { CreditCostPill, formatCreditCost } from '@/components/credits/credit-visual';
import { useGenerationCreditCost } from '@/lib/queries/generation-credit-cost';
import { toast } from 'sonner';
import {
  NODE_CREDIT_PILL_FLAT_CLASS,
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
  NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS,
  NODE_INLINE_ICON_BUTTON_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import type { ScriptFieldDef } from './script/scriptFields';
import {
  buildScriptRowKeys,
  resolveScriptViewId,
  type ScriptViewId,
} from './script/scriptViews';
import { ScriptViewSwitcher } from './script/ScriptViewSwitcher';
import { ScriptColumnMenu } from './script/ScriptColumnMenu';
import { ScriptStatsBar } from './script/ScriptStatsBar';
import { computeScriptStats } from './script/scriptStats';
import {
  resolveScriptColumnMode,
  resolveVisibleScriptColumns,
  scriptFieldSequenceForToggle,
  toggleScriptColumnPatch,
  type ScriptColumnMode,
} from './script/scriptColumns';
import {
  SCRIPT_TABLE_FULLSCREEN_CHROME_PX,
  SCRIPT_TABLE_NODE_CHROME_PX,
  useViewportWidth,
} from './script/useScriptTableWidth';
import { areCanvasNodePropsEqual } from './videoNodeRenderProps';
import { ScriptCreativeView } from './script/ScriptCreativeView';
import { ScriptVideoReview } from './script/ScriptVideoReview';
import { ScriptDirectorPlan } from './script/ScriptDirectorPlan';
import { directorPlanGenerationInstruction } from './script/directorSequenceCoverage';
import { ScriptAssetView } from './script/ScriptAssetView';
import { ScriptStoryboardDialog } from './script/ScriptStoryboardDialog';
import { downloadScriptCsv } from './script/scriptCsv';
import {
  DEFAULT_SCRIPT_IMAGE_GEN_CONFIG,
  generateScriptStoryboard,
  storyboardMemberIdsToRearm,
  storyboardMembersCanRearm,
} from './script/generateScriptStoryboard';
import { storyboardImageNodesForScript } from './script/scriptStoryboardMembers';
import { storyboardGroupLabel } from './script/scriptStoryboard';
import {
  pickScriptAssets,
  useScriptAssetLedger,
  type ScriptAssetLedger,
} from './script/scriptAssets';
import {
  DEFAULT_SCRIPT_ASSET_VIEW_MODE,
  DEFAULT_SCRIPT_ASSET_GEN_ASPECT,
  collectScriptVisualStyle,
  generateScriptAssetImages,
  resolveScriptAssetViewMode,
} from './script/scriptAssetGen';
import { ScriptAssetGenDialog } from './script/ScriptAssetGenDialog';
import { ScriptContractBanner } from './script/ScriptContractBanner';
import { ScriptReadinessBanner } from './script/ScriptReadinessBanner';
import { ScriptShotRewriteDialog } from './script/ScriptShotRewriteDialog';
import { ScriptShotVideoDialog } from './script/ScriptShotVideoDialog';
import {
  scriptRewriteFrozenFacts,
} from './script/scriptPromptSegments';
import {
  graphSliceOf,
  planScriptShotVideos,
  resolveScriptDeliverySpec,
  scatterScriptShotVideos,
  scriptShotVideoChain,
  scriptShotVideoNodesInRowOrder,
  shotVideoAcceptsChainReference,
} from './script/scriptShotVideos';
import {
  assembleScriptFilm,
  scriptFilmStatus,
} from './script/scriptShotCompose';
import { useFreezoneVideoModels } from '@/features/canvas/hooks/useFreezoneVideoModels';
import { selectVideoModel } from '@/features/canvas/domain/videoModelSelection';
import { normalizeVideoQualityValue } from '@/features/canvas/models/imageCapabilityValues';
import {
  describeStaleReasons,
  storyboardStalenessForScript,
  type StoryboardStaleReason,
} from './script/scriptStaleness';
import {
  describeShotDefects,
  describeShotState,
  formatShotNumbers,
  scriptAssetPreflightForScript,
  scriptPreflightForScript,
} from './script/scriptPreflight';
import {
  SCRIPT_NODE_Z,
  resolveScriptNodeBox,
  resolveScriptPanelOverhang,
} from './script/scriptNodeLayout';
import { computeScriptReadiness } from './script/scriptReadiness';
import { computeScriptPaidActionGate } from './script/scriptPaidActionGate';
import { describeScriptRepairOutcome, optimizableScriptIssues } from './script/scriptRepair';

type ScriptNodeProps = NodeProps & {
  id: string;
  data: ScriptNodeData;
  selected?: boolean;
};

// 尺寸口径只有一处：`SCRIPT_NODE_SIZE`（LOD 外壳与 canvasStore 的
// FALLBACK_NODE_SIZES 也读同一份）。渲染尺寸由 resolveScriptNodeBox 统一算，
// 这里只留 resize 把手要用的上下限。
const MIN_WIDTH = SCRIPT_NODE_SIZE.min.width;
const MIN_HEIGHT = SCRIPT_NODE_SIZE.min.height;
const MAX_WIDTH = SCRIPT_NODE_SIZE.max.width;
const MAX_HEIGHT = SCRIPT_NODE_SIZE.max.height;
const PANEL_GAP_PX = 12;
// 「放大」后的输入面板尺寸：给提示词编辑区更舒适的高度与宽度（与 ImageGenNode 同款体验）。
const OPS_PANEL_EXPANDED_WIDTH = 880;
const OPS_PANEL_EXPANDED_HEIGHT = 560;

// 上游节点的预估尺寸（与各自节点 DEFAULT_WIDTH / DEFAULT_HEIGHT 对齐），
// 用于把生成的 text / video / upload 节点放到脚本节点左侧时计算坐标。
// 注：addNode 实际尺寸由 canvasNodeFactory 决定，这里只是布局用近似值。
const SPAWN_TEXT_WIDTH = 440;
const SPAWN_TEXT_HEIGHT = 320;
const SPAWN_VIDEO_WIDTH = 580;
const SPAWN_VIDEO_HEIGHT = 380;
const SPAWN_UPLOAD_WIDTH = 320;
const SPAWN_UPLOAD_HEIGHT = 350;
const SPAWN_GAP_X = 40;
const SPAWN_GAP_Y = 24;

// 后端 freezone/text/story-script 接口未来会调整，模型参数暂不前端控制，
// 也不在 UI 里暴露选择器；提交时不传 model，由后端默认行为决定。

interface ScriptActionDef {
  key: ScriptGenAction;
  label: string;
  Icon: typeof AlignJustify;
}

const SCRIPT_ACTIONS: ScriptActionDef[] = [
  {
    key: 'fromScript',
    label: '剧本生成分镜脚本',
    Icon: AlignJustify,
  },
  {
    key: 'fromVideoRef',
    label: '视频参考生成分镜脚本',
    Icon: Video,
  },
  {
    key: 'fromCharacter',
    label: '角色生成分镜脚本',
    Icon: User,
  },
];

// 字段表挪到 ./script/scriptFields.ts：与后端 FreezoneStoryScriptRow 一一对应，
// 并与 CSV 导出共用一份定义（导出时角色列按各行实际占用槽位动态展开）。
// 角色图与关键帧参考由后端回填已验证的项目资产 URL，不能再让模型或旧字段名把表格渲染成空列。
type ScriptColumnDef = ScriptFieldDef;

function isScriptResult(value: unknown): value is FreezoneStoryScriptResult {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as { rows?: unknown };
  return Array.isArray(candidate.rows);
}

type ScriptReferenceKind = 'text' | 'image' | 'video' | 'audio';

interface ScriptReference {
  nodeId: string;
  kind: ScriptReferenceKind;
  /** 用作 chip / 预览的图片（image / video 首帧）；text 节点不需要。 */
  thumbUrl?: string | null;
  /** text 节点的内容（提交时拼接到 source_text）；其它节点不需要。 */
  text?: string | null;
  /** video 节点：视频源 URL，用于 hover 预览的 <video>。 */
  videoUrl?: string | null;
  /** video 节点：视频总时长（秒），提交时作为 duration_sec 提升时间戳精度。 */
  durationSec?: number | null;
  /** 节点显示名（chip tooltip 用 / 角色名）。 */
  displayName?: string | null;
}

function classifyUpstreamNode(node: CanvasNode): ScriptReference | null {
  if (isTextAnnotationNode(node)) {
    return {
      nodeId: node.id,
      kind: 'text',
      text: typeof node.data.content === 'string' ? node.data.content : '',
      displayName: node.data.displayName ?? null,
    };
  }
  if (isVideoNode(node)) {
    const videoUrl =
      typeof node.data.videoUrl === 'string' && node.data.videoUrl.length > 0
        ? node.data.videoUrl
        : null;
    const thumbUrl =
      (typeof node.data.previewImageUrl === 'string' && node.data.previewImageUrl) ||
      null;
    const durationSec =
      typeof node.data.durationMs === 'number' && node.data.durationMs > 0
        ? node.data.durationMs / 1000
        : null;
    return {
      nodeId: node.id,
      kind: 'video',
      thumbUrl,
      videoUrl,
      durationSec,
      displayName: node.data.displayName ?? null,
    };
  }
  if (isAudioNode(node)) {
    return {
      nodeId: node.id,
      kind: 'audio',
      displayName: node.data.displayName ?? null,
    };
  }
  if (isImageGenNode(node)) {
    const data = node.data;
    const ref =
      typeof data.referenceImageUrl === 'string' && data.referenceImageUrl.length > 0
        ? data.referenceImageUrl
        : null;
    return {
      nodeId: node.id,
      kind: 'image',
      thumbUrl: data.previewImageUrl || data.imageUrl || ref,
      displayName: data.displayName ?? null,
    };
  }
  if (isUploadNode(node) || isImageEditNode(node) || isExportImageNode(node)) {
    const data = node.data;
    return {
      nodeId: node.id,
      kind: 'image',
      thumbUrl: data.previewImageUrl || data.imageUrl || null,
      displayName: data.displayName ?? null,
    };
  }
  return null;
}

// 提交逻辑抽成共享 hook：节点本体的「重试」按钮与底部操作面板的「生成」按钮共用同一条
// 提交路径。错误统一写进 data.generationError（渲染在节点本体上），不再用面板本地 state。
function useScriptStorySubmit(
  nodeId: string,
  references: ScriptReference[],
  prompt: string,
  data: ScriptNodeData,
  onSettled?: () => void,
): { submit: () => Promise<void>; isGenerating: boolean } {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const { models: textModels } = useDirectModelCatalog('text');
  const resolvedTextModel = resolveDirectCanvasModelId(data.model, textModels);
  const { models: visionModels } = useDirectModelCatalog('vision');
  const resolvedVisionModel = resolveDirectCanvasModelId(data.model, visionModels);
  const { isGenerating } = useNodeGenerationTaskState(data);

  const submit = useCallback(async () => {
    if (isGenerating) return;
    const project = readUrl().project;
    if (!project) {
      console.error('[script-node] submit: no project in URL');
      updateNodeData(nodeId, { generationError: '缺少 project 参数' });
      return;
    }

    // 同一个 story-script 接口支持三种输入，按上游连线类型分流：
    //  - 文本节点  → source_text
    //  - 视频节点  → video_url (+ duration_sec)
    //  - 角色图节点 → character_refs[]（image_url + 角色名）
    // 文本框内容：有任一素材时作为 steering prompt；否则作为 source_text 主输入。
    const upstreamText = references
      .filter((ref) => ref.kind === 'text')
      .map((ref) => (ref.text ?? '').trim())
      .filter((text) => text.length > 0)
      .join('\n\n');
    const trimmedPrompt = prompt.trim();

    const videoRef = references.find((ref) => ref.kind === 'video' && ref.videoUrl);
    const characterRefs = references
      .filter((ref) => ref.kind === 'image' && ref.thumbUrl)
      .map((ref) => ({
        imageUrl: ref.thumbUrl as string,
        name: ref.displayName?.trim() || undefined,
      }));

    // 视频和角色图现在是后端的一等输入：视频会抽帧交给视觉路由，角色图会以视觉附件
    // 参与生成并在结果中绑定真实资产 URL。仅在没有素材时，文本框才作为主剧本输入。
    const hasMedia = Boolean(videoRef) || characterRefs.length > 0;
    const sourceText = upstreamText.length > 0 ? upstreamText : hasMedia ? '' : trimmedPrompt;
    const steeringPrompt = [upstreamText.length > 0 || hasMedia ? trimmedPrompt : '', data.scriptDirectorPlanNeedsSync && isScriptResult(data.scriptResult) ? directorPlanGenerationInstruction(data.scriptResult.director_plan) : ''].filter(Boolean).join('\n\n') || undefined;
    const executionModel = hasMedia ? resolvedVisionModel : resolvedTextModel;

    if (!executionModel) {
      updateNodeData(nodeId, {
        generationError: hasMedia
          ? '当前节点绑定的视觉模型已失效，请重新选择模型。'
          : '当前节点绑定的文字模型已失效，请重新选择模型。',
      });
      return;
    }

    if (!sourceText && !hasMedia) {
      updateNodeData(nodeId, {
        generationError: '请输入剧情文本，或连接有效的视频/角色参考素材',
      });
      return;
    }

    updateNodeData(nodeId, {
      isGenerating: true,
      generationStartedAt: Date.now(),
      generationError: null,
      generationModel: executionModel || null,
      generationModelId: executionModel || null,
      generationProviderId: 'direct',
    });
    try {
      const ref = await submitFreezoneStoryScript(project, {
        videoModel: data.videoGenConfig?.model ?? '',
        sourceText,
        videoUrl: videoRef?.videoUrl ?? undefined,
        durationSec: videoRef?.durationSec ?? undefined,
        characterRefs: characterRefs.length > 0 ? characterRefs : undefined,
        prompt: steeringPrompt,
        model: executionModel || undefined,
        canvasId: readUrl().canvas ?? 'default',
        nodeId,
      });
      // Persist the task handle so a page refresh can resume this job.
      updateNodeData(nodeId, generationTaskDescriptor(ref, 'full'));
      await awaitTaskCompletion(ref.task_key, project);
      const result = await fetchFreezoneStoryScriptResult(project, ref.job_id);
      updateNodeData(nodeId, {
        ...CLEARED_GENERATION_TASK_PATCH,
        scriptGenerationMode: null,
        scriptResult: normalizeScriptResultIdentities(result), scriptDirectorPlanNeedsSync: false,
        scriptTitle: result.title ?? null,
        scriptContractReport: result.contract_report ?? null,
        ...CLEARED_GENERATION_ERROR_PATCH,
      });
    } catch (error) {
      console.error('[script-node] submit failed', error);
      // 把后端随错误一起送来的诊断（request_id / stage / suggested_action）
      // 落到节点字段上，而不是只留一句 error.message —— 卡片靠这些字段才能
      // 说清「哪一步失败、要不要重试、拿什么去查」。
      const diagnostics = resolveGenerationErrorDiagnostics(error);
      updateNodeData(nodeId, {
        ...CLEARED_GENERATION_TASK_PATCH,
        scriptGenerationMode: null,
        generationError: error instanceof Error ? error.message : '生成失败',
        generationErrorDetails: diagnostics.details,
        generationErrorRequestId: diagnostics.requestId,
        generationErrorStage: diagnostics.stage,
        generationErrorSuggestedAction: diagnostics.suggestedAction,
        generationErrorCode: diagnostics.errorCode,
        generationErrorRetryable: diagnostics.retryable,
      });
    } finally {
      onSettled?.();
    }
  }, [
    isGenerating,
    nodeId,
    references,
    prompt,
    data,
    resolvedTextModel,
    resolvedVisionModel,
    updateNodeData,
    onSettled,
  ]);

  return { submit, isGenerating };
}

export const ScriptNode = memo(({ id, data, selected, width, height }: ScriptNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const selectedNodeId = useCanvasStore((state) => state.selectedNodeId);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  // Subscribe to ONLY one-hop upstream (not the whole nodes array) so unrelated
  // node drags don't re-render this node. See useUpstreamGraph.
  const upstreamNodes = useUpstreamNodes(id);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isStoryboardDialogOpen, setIsStoryboardDialogOpen] = useState(false);
  const [isAssetDialogOpen, setIsAssetDialogOpen] = useState(false);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.script, data),
    [data],
  );

  const scriptResult = isScriptResult(data.scriptResult) ? data.scriptResult : null;
  const rows = scriptResult?.rows ?? [];
  const hasResult = rows.length > 0;

  const activeView = resolveScriptViewId(data.activeViewId ?? data.viewMode);
  const handleViewChange = useCallback(
    (next: ScriptViewId) => {
      updateNodeData(id, { viewMode: next, activeViewId: next });
    },
    [id, updateNodeData],
  );

  // 生成分镜的图片模型 / 比例：写在节点上（对齐 LibTV imageGenConfig），下次沿用。
  const imageGenConfig: ScriptImageGenConfig = data.imageGenConfig ?? {};
  const storyboardModel = imageGenConfig.model ?? '';
  const storyboardAspect =
    imageGenConfig.aspectRatio ?? DEFAULT_SCRIPT_IMAGE_GEN_CONFIG.aspectRatio ?? '16:9';
  const handleStoryboardModelChange = useCallback(
    (next: string) => {
      updateNodeData(id, { imageGenConfig: { ...imageGenConfig, model: next } });
    },
    [id, imageGenConfig, updateNodeData],
  );
  const handleStoryboardAspectChange = useCallback(
    (next: string) => {
      const deliverySpec = resolveScriptDeliverySpec(next);
      updateNodeData(id, {
        imageGenConfig: {
          ...imageGenConfig,
          aspectRatio: deliverySpec.aspectRatio,
          deliverySpec,
        },
      });
    },
    [id, imageGenConfig, updateNodeData],
  );

  // 资产图的模型 / 比例 / 视图规格：与分镜图分开存 —— 比例默认「自动」，
  // 三族各取所需（角色 4:3 四视图、场景 16:9、道具 1:1），用户也可以一键锁成同一个。
  const assetGenConfig: ScriptAssetGenConfig =
    data.assetGenConfig ?? {
      aspectRatio: DEFAULT_SCRIPT_ASSET_GEN_ASPECT,
      viewMode: DEFAULT_SCRIPT_ASSET_VIEW_MODE,
    };
  const assetModel = assetGenConfig.model ?? storyboardModel;
  const assetAspect = assetGenConfig.aspectRatio ?? DEFAULT_SCRIPT_ASSET_GEN_ASPECT;
  const assetViewMode = resolveScriptAssetViewMode(assetGenConfig);
  // 全片视觉风格：从脚本自己产出的 `shot_prompt` 第 7 段白拿（它是全片唯一且逐行相同的那段）。
  // 资产图带上它，才不会出现「资产是写实、分镜是卡通」这种一眼假的成片。
  const assetStyle = useMemo(() => collectScriptVisualStyle(rows), [rows]);
  const handleAssetModelChange = useCallback(
    (next: string) => {
      updateNodeData(id, { assetGenConfig: { ...assetGenConfig, model: next } });
    },
    [assetGenConfig, id, updateNodeData],
  );
  const handleAssetAspectChange = useCallback(
    (next: string) => {
      updateNodeData(id, { assetGenConfig: { ...assetGenConfig, aspectRatio: next } });
    },
    [assetGenConfig, id, updateNodeData],
  );
  const handleAssetViewModeChange = useCallback(
    (next: ScriptAssetViewMode) => {
      updateNodeData(id, {
        assetGenConfig: { ...assetGenConfig, viewMode: next },
      });
    },
    [assetGenConfig, id, updateNodeData],
  );


  // 表格单元格编辑：把编辑过的值写回 data.scriptResult.rows[idx][colKey]。
  // 整个 scriptResult 是 store 持有的 single source of truth，节点表格 +
  // 全屏表格都共享同一个 onCommit，确保两处编辑都能落盘。
  const handleCellCommit = useCallback(
    (rowIndex: number, colKey: string, nextValue: string) => {
      if (!scriptResult) return;
      const existingRows = ensureScriptShotIdentities(scriptResult.rows ?? []).rows;
      const existing = existingRows[rowIndex];
      if (!existing) return;
      const prevRaw = existing[colKey];
      const prev = typeof prevRaw === 'string' ? prevRaw : prevRaw == null ? '' : String(prevRaw);
      if (prev === nextValue) return;
      const nextRows = existingRows.map((row, index) =>
        index === rowIndex
          ? {
              ...row,
              [colKey]: nextValue,
              ...(colKey === 'shot_no'
                ? { display_shot_no: nextValue.trim() || String(index + 1) }
                : {}),
            }
          : row,
      );
      updateNodeData(id, {
        scriptResult: { ...scriptResult, rows: nextRows },
        // 行一改，旧报告就不再是这张表的读数。立即清掉，避免雷达把过期合同当成当前结论；
        // 保存画布时后端会按行指纹补回一份新鲜报告。
        scriptContractReport: null,
      });
    },
    [id, scriptResult, updateNodeData],
  );

  // 行操作（T-047）：复制 / 插行 / 删行 / 重排都只改 `data.scriptResult.rows`。
  // 身份合同收在 `scriptShotIdentity` 的纯函数里（既有行 `shot_id` 一律不动；新行
  // 拿新身份；删行不动其它行身份；纯重排只改 `shot_order`），节点只负责把结果写回
  // store。此前只有 helper、没有入口，这里把它接进真实表格按钮。
  const commitScriptRows = useCallback(
    (nextRows: FreezoneStoryScriptRow[]) => {
      if (!scriptResult) return;
      updateNodeData(id, {
        scriptResult: { ...scriptResult, rows: nextRows },
        scriptContractReport: null,
      });
    },
    [id, scriptResult, updateNodeData],
  );

  const handleInsertRowAfter = useCallback(
    (rowIndex: number) => {
      if (!scriptResult) return;
      commitScriptRows(insertScriptShotRowAfter(scriptResult.rows ?? [], rowIndex));
    },
    [commitScriptRows, scriptResult],
  );

  const handleDuplicateRow = useCallback(
    (rowIndex: number) => {
      if (!scriptResult) return;
      commitScriptRows(duplicateScriptShotRowAt(scriptResult.rows ?? [], rowIndex));
    },
    [commitScriptRows, scriptResult],
  );

  const handleMoveRow = useCallback(
    (rowIndex: number, delta: -1 | 1) => {
      if (!scriptResult) return;
      commitScriptRows(moveScriptShotRow(scriptResult.rows ?? [], rowIndex, delta));
    },
    [commitScriptRows, scriptResult],
  );

  const handleDeleteRow = useCallback(
    (rowIndex: number) => {
      if (!scriptResult) return;
      commitScriptRows(deleteScriptShotRowAt(scriptResult.rows ?? [], rowIndex));
    },
    [commitScriptRows, scriptResult],
  );

  // 「只改这一镜」的弹层状态与提交。走的是同一个 story-script 接口的**重写模式**：
  // 请求里带上当前整表与目标镜身份，服务端只换那一行，拼接后再过一遍合同，
  // 所以角色卡与第 7/8 段永远由表决定，不由这一次生成决定。
  const { models: rewriteTextModels } = useDirectModelCatalog('text');
  const [rewriteTarget, setRewriteTarget] = useState<number | null>(null);
  const [rewriteSequenceId, setRewriteSequenceId] = useState<string | null>(null);
  const [rewriteBusy, setRewriteBusy] = useState(false);
  const [rewriteError, setRewriteError] = useState<string | null>(null);
  const [optimizeBusy, setOptimizeBusy] = useState(false);
  const [optimizeError, setOptimizeError] = useState<string | null>(null);
  const { cancel: cancelNodeTask, isCancelling: isCancellingNodeTask, canCancel: canCancelNodeTask } = useCancelNodeGeneration(id, data, undefined, { clearPatch: { scriptGenerationMode: null } });

  const handleRewriteShot = useCallback((rowIndex: number) => {
    setRewriteError(null);
    setRewriteSequenceId(null);
    setRewriteTarget(rowIndex);
  }, []);

  const handleRewriteSequence = useCallback((sequenceId: string) => {
    setRewriteError(null);
    setRewriteSequenceId(sequenceId);
    setRewriteTarget(null);
  }, []);
  const rewriteSequence = scriptResult?.director_plan?.sequences?.find(sequence => sequence.sequence_id === rewriteSequenceId);
  const rewriteSequenceRows = rewriteSequence ? rows.filter(row => rewriteSequence.shot_nos?.includes(Number(row.shot_no))) : [];

  // 合同报告是「这一次生成」的读数，用户看过就可以收起；下一次生成会带来新的报告，
  // 所以收起不需要持久化到节点数据里。
  const handleDismissContractReport = useCallback(() => {
    updateNodeData(id, { scriptContractReport: null });
  }, [id, updateNodeData]);

  // 弹层要如实说清三件事：改的是哪一镜、它现在是什么样、哪几段改不动。
  // 人物身份与全片风格保留；技术参数允许随本镜创作目的变化。
  const rewriteShotNo = useMemo(() => {
    if (rewriteTarget === null) return '';
    const row = rows[rewriteTarget];
    const display = typeof row?.display_shot_no === 'string' ? row.display_shot_no.trim() : '';
    return display || String(row?.shot_no ?? rewriteTarget + 1);
  }, [rewriteTarget, rows]);

  const rewriteShotSummary = useMemo(() => {
    if (rewriteTarget === null) return '';
    const row = rows[rewriteTarget];
    if (!row) return '';
    return [
      typeof row.visual_description === 'string' ? row.visual_description.trim() : '',
      typeof row.shot === 'string' && row.shot.trim() ? `景别 ${row.shot.trim()}` : '',
      typeof row.duration === 'string' || typeof row.duration === 'number'
        ? `时长 ${String(row.duration)}s`
        : '',
      typeof row.dialogue === 'string' && row.dialogue.trim() ? `对白「${row.dialogue.trim()}」` : '',
    ]
      .filter(Boolean)
      .join('｜');
  }, [rewriteTarget, rows]);

  const rewriteFrozenFacts = useMemo(() => {
    return scriptRewriteFrozenFacts(rows, rewriteTarget);
  }, [rows, rewriteTarget]);

  const handleRewriteCancel = useCallback(() => {
    if (rewriteBusy) { if (canCancelNodeTask) void cancelNodeTask(); return; }
    setRewriteTarget(null);
    setRewriteSequenceId(null);
    setRewriteError(null);
  }, [rewriteBusy, canCancelNodeTask, cancelNodeTask]);

  const submitRewrite = useCallback(
    async (instruction: string) => {
      if ((rewriteTarget === null && !rewriteSequenceId) || !scriptResult) return;
      const project = readUrl().project;
      if (!project) {
        setRewriteError('缺少 project 参数');
        return;
      }
      const model = resolveDirectCanvasModelId(data.model, rewriteTextModels);
      if (!model) {
        setRewriteError('当前节点绑定的文字模型已失效，请重新选择模型。');
        return;
      }
      const targetRow = rewriteTarget === null ? undefined : (scriptResult.rows ?? [])[rewriteTarget];
      setRewriteBusy(true);
      setRewriteError(null);
      try {
        await submitScriptLocalRewrite(project, id, {
          prompt: instruction,
          model,
          canvasId: readUrl().canvas ?? 'default',
          nodeId: id,
          title: data.scriptTitle ?? undefined,
          currentRows: scriptResult.rows ?? [], directorPlan: scriptResult.director_plan,
          rewriteShotId: targetRow?.shot_id ?? undefined,
          rewriteIndex: rewriteTarget ?? undefined,
          rewriteSequenceId: rewriteSequenceId ?? undefined,
        });
        setRewriteTarget(null);
        setRewriteSequenceId(null);
      } catch (error) {
        console.error('[script-node] shot rewrite failed', error);
        setRewriteError(error instanceof Error ? error.message : '重写这一镜失败');
      } finally {
        setRewriteBusy(false);
      }
    },
    [data.model, data.scriptTitle, id, rewriteSequenceId, rewriteTarget, rewriteTextModels, scriptResult],
  );

  const optimizableIssues = useMemo(
    () => optimizableScriptIssues(data.scriptContractReport?.issues),
    [data.scriptContractReport],
  );

  const handleOptimizeScript = useCallback(async () => {
    if (optimizeBusy || !scriptResult) return;
    if (optimizableIssues.length === 0) {
      // 报告里只剩机械规则（镜号、参考图预算、角色卡冻结段……）时，文字模型改不动这些
      // 字段。以前这里是静默 return：按钮看着能点，点下去什么都不发生。
      setOptimizeError('剩下的问题都由机械规则直接修复，文字模型改不动它们。');
      return;
    }
    const project = readUrl().project;
    if (!project) {
      setOptimizeError('缺少 project 参数');
      return;
    }
    const model = resolveDirectCanvasModelId(data.model, rewriteTextModels);
    if (!model) {
      setOptimizeError('当前节点绑定的文字模型已失效，请重新选择模型。');
      return;
    }

    setOptimizeBusy(true);
    setOptimizeError(null);
    updateNodeData(id, {
      isGenerating: true,
      generationStartedAt: Date.now(),
      generationError: null,
      generationModel: model,
      generationModelId: model,
      generationProviderId: 'direct',
    });
    try {
      const ref = await submitFreezoneStoryScript(project, {
        videoModel: data.videoGenConfig?.model ?? '',
        model,
        canvasId: readUrl().canvas ?? 'default',
        nodeId: id,
        title: data.scriptTitle ?? undefined,
        currentRows: scriptResult.rows ?? [], directorPlan: scriptResult.director_plan,
        repairMode: 'script-contract',
        sourceText: data.prompt ?? undefined,
        repairIssues: optimizableIssues,
        repairPasses: 3,
      });
      updateNodeData(id, generationTaskDescriptor(ref, 'repair'));
      await awaitTaskCompletion(ref.task_key, project);
      const result = await fetchFreezoneStoryScriptResult(project, ref.job_id);
      updateNodeData(id, {
        ...CLEARED_GENERATION_TASK_PATCH,
        scriptGenerationMode: null,
        scriptResult: normalizeScriptResultIdentities(result),
        scriptTitle: result.title ?? null,
        scriptContractReport: result.contract_report ?? null,
        ...CLEARED_GENERATION_ERROR_PATCH,
      });
      toast.success(describeScriptRepairOutcome(result.contract_report?.repair));
    } catch (error) {
      console.error('[script-node] contract repair failed', error);
      const message = error instanceof Error ? error.message : '一键优化失败';
      setOptimizeError(message);
      updateNodeData(id, {
        ...CLEARED_GENERATION_TASK_PATCH,
        scriptGenerationMode: null,
        generationError: message,
      });
      toast.error(`一键优化失败：${message}`);
    } finally {
      setOptimizeBusy(false);
    }
  }, [
    data.model,
    data.scriptTitle,
    id,
    optimizableIssues,
    optimizeBusy,
    rewriteTextModels,
    scriptResult,
    updateNodeData,
  ]);

  const scriptRowOperations = useMemo<ScriptRowOperations>(
    () => ({
      onInsertAfter: handleInsertRowAfter,
      onDuplicate: handleDuplicateRow,
      onMove: handleMoveRow,
      onDelete: handleDeleteRow,
      onRewrite: handleRewriteShot,
      canDelete: rows.length > 1,
    }),
    [
      handleDeleteRow,
      handleDuplicateRow,
      handleInsertRowAfter,
      handleMoveRow,
      handleRewriteShot,
      rows.length,
    ],
  );
  // 出现表格后默认尺寸切到 800x400；用户已 resize 过的节点尊重原宽高。
  const { width: resolvedWidth, height: resolvedHeight } = resolveScriptNodeBox({
    width,
    height,
    hasResult,
  });

  // 列显隐（对齐 LibTV `views[].tableConfig.columnVisibility`）：20 列声明宽合计 2540px
  // （实测 `table.offsetWidth` = 2540 + 列边框），而节点默认宽 800px
  // （内容区 782px），一屏看得到不到三分之一。默认档按可用宽度选列 ——
  // 必留列（镜号 / 时长 / 画面描述 / 两条提示词）先摆上，再按优先级带回塞得下的列；
  // 菜单里可切到全部列或逐列勾。
  const columnMode = resolveScriptColumnMode(data.columnMode);
  const allScriptFields = useMemo(() => scriptFieldSequenceForToggle(), []);
  const viewportWidth = useViewportWidth();
  const tableAvailableWidth = Math.max(
    0,
    isFullscreen
      ? viewportWidth - SCRIPT_TABLE_FULLSCREEN_CHROME_PX
      : resolvedWidth - SCRIPT_TABLE_NODE_CHROME_PX,
  );
  const visibleScriptColumns = useMemo(
    () =>
      resolveVisibleScriptColumns({
        mode: columnMode,
        hidden: data.hiddenColumns,
        availableWidthPx: tableAvailableWidth,
      }),
    [columnMode, data.hiddenColumns, tableAvailableWidth],
  );
  const currentVisibleKeys = useMemo(
    () => visibleScriptColumns.map((field) => field.key),
    [visibleScriptColumns],
  );
  const handleColumnModeChange = useCallback(
    (next: ScriptColumnMode) => {
      updateNodeData(id, { columnMode: next });
    },
    [id, updateNodeData],
  );
  const handleToggleColumn = useCallback(
    (key: string) => {
      // 走 `toggleScriptColumnPatch` 而不是 `toggleScriptColumn`：纯函数用 `mode`，
      // 节点数据用 `columnMode`。直接塞纯函数结果会把档位写进没人读的 `data.mode`，
      // 而 `CanvasNodeData` 的索引签名吞掉拼写错误 —— 症状是「菜单勾选态变了、
      // 表格列数纹丝不动」。键名映射收在那一处，并由单测钉住。
      updateNodeData(
        id,
        toggleScriptColumnPatch({
          fields: allScriptFields,
          visibleKeys: currentVisibleKeys,
          key,
        }),
      );
    },
    [allScriptFields, currentVisibleKeys, id, updateNodeData],
  );

  // 脚本标题的回落链只有这一份：后端返回的标题 → 节点上留的标题 → 节点显示名。
  // 此前这条链在表头副标题、分镜组名、分镜生成三处各写了一遍（其中两处还漏了 trim），
  // 改一处就会和另外两处漂。
  const scriptTitleText = scriptResult?.title?.trim() || data.scriptTitle?.trim() || '';
  const scriptTitleOrNodeName = scriptTitleText || resolvedTitle;

  // 表头读数：行数 / 总时长 / 景别分布 / 缺项计数（此前只有全屏弹层里一句「共 N 个分镜」）。
  const stats = useMemo(() => computeScriptStats(rows), [rows]);

  // 上游节点 → references；用于隐藏「尝试」入口、生成 chip、提交时拼 source_text。
  const references = useMemo<ScriptReference[]>(() => {
    const upstream = [...upstreamNodes];
    upstream.sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0));
    return upstream
      .map((node) => classifyUpstreamNode(node))
      .filter((entry): entry is ScriptReference => entry != null);
  }, [upstreamNodes]);
  const hasUpstream = references.length > 0;
  const isNodeSelected = Boolean(selected) || selectedNodeId === id;
  const promptText = typeof data.prompt === 'string' ? data.prompt : '';
  // Per-node story-script history; fetched only while the node is selected.
  // 提到节点本体这一层：本体「重试」与面板「生成」共用下面同一个提交实例，任何
  // 一条提交路径 settle 都会刷新历史（拆成两个实例时重试路径不刷新，历史会缺新记录）。
  const {
    records: historyRecords,
    isLoading: historyLoading,
    refresh: refreshHistory,
  } = useNodeGenerationHistory(id, { enabled: isNodeSelected });
  const { submit, isGenerating } = useScriptStorySubmit(
    id,
    references,
    promptText,
    data,
    refreshHistory,
  );

  // task-center 里的那条真实任务；刷新后靠 generationTaskKey 重新接上，进度条续显。
  const { task: generationTask } = useNodeGenerationTaskState(data);

  // 「下载」导出 CSV（对齐 LibTV 脚本节点的下载：角色列按各行实际占用槽位展开）。
  const handleDownloadCsv = useCallback(() => {
    if (rows.length === 0) return;
    downloadScriptCsv(rows, scriptTitleText, {
      directorPlan: scriptResult?.director_plan,
      videos: scriptShotVideoNodesInRowOrder(id),
      directorPlanPending: data.scriptDirectorPlanNeedsSync === true,
      graph: useCanvasStore.getState(),
    });
  }, [rows, scriptTitleText, scriptResult?.director_plan, id, data.scriptDirectorPlanNeedsSync]);

  // 「生成分镜」：按行派生分镜图节点并在右侧组成分镜图组。
  // 已有关联组时按「重新生成分镜图」处理：原地重跑未出图 / 失败的那几张（行数变了才重建）。
  //
  // 这段订阅必须放在下面几个分镜状态 useMemo 之前：它们读的是画布上别人的节点，
  // 脚本节点自己的 `data` 在这些节点出图落定时不会变。若只依赖 `data`，
  // 单镜无组场景会一直停在「还没有分镜图 / pending」，直到重新挂载节点才刷新。
  //
  // 选择器只取脚本节点、它的分镜图和派生视频，不会让整张大表跟着无关节点拖动重渲染。
  const shotVideoChain = useCanvasStore(
    useShallow((state) => scriptShotVideoChain(id, state.nodes, state.edges)),
  );
  const canvasGraph = useMemo(() => graphSliceOf(shotVideoChain), [shotVideoChain]);

  const linkedStoryboardMembers = useMemo(
    () => storyboardImageNodesForScript(id),
    // 成员会随生成进度变化（出图后图节点对象更新）。这里依赖链快照本身，
    // 既覆盖单镜无组，也覆盖成组 / 出图散开 / 并组重锚三种血缘状态。
    [id, canvasGraph],
  );
  const linkedGroupMemberIds = useMemo(
    () => linkedStoryboardMembers.map((member) => member.id),
    [linkedStoryboardMembers],
  );
  // 资产台账（第一刀）：三族资产的身份 + 各自有没有图。行变了或资产图出图落定就重算。
  // 它同时是「生成分镜」的参考图顺序来源与过期判定的一部分，所以必须与生成时吃同一份。
  const assetLedger = useScriptAssetLedger(id, rows);
  // 脚本行改过之后，这批分镜图里哪些已经对不上了（对齐 LibTV「脚本已变更须重生成
  // storyboard」）。判定读的是节点上的派生快照，见 scriptStaleness。
  const staleness = useMemo(
    () => storyboardStalenessForScript(id, assetLedger),
    // 脚本 data 变化会经由节点重渲染触发；这里额外依赖 canvasGraph，保证组内分镜图
    // 出图落定时重新判定。assetLedger 变化（资产图出图落定）同样要重判。
    [id, canvasGraph, assetLedger],
  );
  const staleShotCount = staleness.staleNodeIds.length;
  // 逐镜清点（补 `scriptStaleness` 与 `scriptStats` 之间的那一层）：逐镜状态 + 逐镜缺项。
  // 判过期只回答「哪几张图对不上」，回答不了「第 7 镜到底能不能出、缺什么、要花几张的钱」。
  const preflight = useMemo(
    () => scriptPreflightForScript(id, assetLedger),
    // 脚本 data 变化会经由节点重渲染触发；canvasGraph 覆盖出图落定 / 组变化，
    // assetLedger 覆盖资产图出图落定后的角色缺口变化。
    [id, canvasGraph, assetLedger],
  );
  // 资产层缺口清点（第四刀）：逐镜清单会把同一个角色的缺口报 N 遍，这里按**资产**报一次。
  const assetPreflight = useMemo(
    () => scriptAssetPreflightForScript(id, assetLedger),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, assetLedger],
  );
  // 开拍雷达：只把合同、逐镜预检与资产预检合成一份当前结论，不写回任何节点字段。
  // 行一变会先清掉旧合同报告，因此这里不会把过期读数报成「可开拍」。
  const readiness = useMemo(
    () =>
      computeScriptReadiness({
        rows,
        preflight,
        assetPreflight,
        contractReport: data.scriptContractReport,
        directorPlanNeedsSync: data.scriptDirectorPlanNeedsSync === true,
      }),
    [rows, preflight, assetPreflight, data.scriptContractReport, data.scriptDirectorPlanNeedsSync],
  );
  const storyboardPaidGate = useMemo(
    () => computeScriptPaidActionGate(readiness, 'storyboard-images'),
    [readiness],
  );
  const shotVideoPaidGate = useMemo(
    () => computeScriptPaidActionGate(readiness, 'shot-videos', rows.flatMap((row, index) =>
      ['text_to_video', 'texttovideo'].includes(String(row.generation_mode ?? '').toLowerCase())
        ? [String(row.shot_no ?? index + 1)] : [])),
    [readiness, rows],
  );
  // 逐镜标记（表格行首色条用）。
  //
  // 只给「有话说」的行打标：已出图、不缺项的行一律不打 —— 每行都打标等于没打标。
  // 两种色分开是有意的：`stale`（琥珀，脚本改过要重出）与 `defect`（橙，缺开拍条件）。
  // 混成一种的话，50 镜的表里看不出「这张要重出」和「这镜本来就缺图」的区别。
  const rowFlags = useMemo(() => {
    const flags = new Map<string, { tone: 'stale' | 'defect'; title: string }>();
    for (const entry of preflight.entries) {
      const defects = describeShotDefects(entry.defects);
      const needsAttention = entry.state === 'stale' || defects.length > 0;
      if (!needsAttention) continue;
      flags.set(entry.rowKey, {
        tone: entry.state === 'stale' ? 'stale' : 'defect',
        title: [
          describeShotState(entry.state),
          entry.state === 'stale' ? describeStaleReasons(entry.reasons) : '',
          defects,
        ]
          .filter(Boolean)
          .join('；'),
      });
    }
    return flags;
  }, [preflight]);
  // 过期横幅的 tooltip 文案：把逐节点原因并成一句话说清「为什么算过期」。
  // `describeStaleReasons` 早就写好了，此前没有任何调用方 —— 横幅只报张数，
  // 用户看到「3 张需重新生成」也不知道是提示词改了还是参考图换了。
  // `rowCountChanged` 同理：它算出来了却没人读，这里一并说清。
  // 「是第几镜」由 preflight 出（逐镜清点），横幅正文与 tooltip 共用。
  const staleSummary = useMemo(() => {
    if (staleShotCount === 0) return '';
    const merged = new Set<StoryboardStaleReason>();
    for (const list of staleness.reasons.values()) {
      for (const reason of list) merged.add(reason);
    }
    const parts = [
      preflight.staleShotNumbers.length > 0
        ? `${formatShotNumbers(preflight.staleShotNumbers)}：${describeStaleReasons([...merged])}`
        : describeStaleReasons([...merged]),
      staleness.rowCountChanged
        ? `分镜图张数与脚本行数（${rows.length}）不一致，需要按当前脚本重建整组`
        : '',
    ].filter(Boolean);
    return parts.join('；');
  }, [staleness, staleShotCount, rows.length, preflight.staleShotNumbers]);
  // 点「生成分镜」会真的去重出的张数：未出图 / 失败 / 已失效的。
  const rearmShotIds = useMemo(
    () => storyboardMemberIdsToRearm(linkedGroupMemberIds, rows, assetLedger),
    // canvasGraph 变化时 linkedGroupMemberIds 会重建；显式保留它作为失效信号，
    // 避免后续有人把 IDs memo 改成只比较集合后漏掉“同 id、图状态已变”的情况。
    [linkedGroupMemberIds, canvasGraph, assetLedger],
  );
  const pendingShotCount = rearmShotIds.length;
  const willRebuildGroup =
    linkedStoryboardMembers.length > 0 &&
    !storyboardMembersCanRearm(linkedGroupMemberIds, rows, assetLedger);
  const storyboardMode: 'create' | 'regenerate' =
    linkedStoryboardMembers.length > 0 ? 'regenerate' : 'create';
  const shotsToGenerate = storyboardMode === 'create' ? rows.length : pendingShotCount;
  const storyboardGroupName = storyboardGroupLabel(scriptTitleOrNodeName);

  // 分镜图的预估点数：与图片节点同一条计价链路（image_selection + 分辨率档位 + 张数）。
  // value 取 catalogId：它同时也是图片节点提交时用的 `data.model`（等价 direct/<id>），
  // 计价接口只认这个形式（传上游模型名会 400 invalid image selection）。
  const { models: imageModels, isLoading: imageModelsLoading } = useDirectModelCatalog('image');
  const selectedImageModel = useMemo(
    () => imageModels.find((model) => model.catalogId === storyboardModel) ?? null,
    [imageModels, storyboardModel],
  );
  const storyboardCreditCost = useGenerationCreditCost(
    'image_selection',
    selectedImageModel?.catalogId ?? null,
    { surface: 'canvas', params: { size: '1K' }, quantity: Math.max(0, shotsToGenerate) },
  );
  const storyboardPriceDisplay = useMemo(() => {
    const total = storyboardCreditCost.data?.data.cost;
    return typeof total === 'number' ? formatCreditCost(total) : null;
  }, [storyboardCreditCost.data?.data.cost]);

  // 资产图的单张预估点数：一次勾几个就是几倍（弹层自己乘），所以 quantity 固定 1。
  // 模型独立解析：资产图与分镜图的模型可以不同（`assetModel` 只在没设时回落到分镜模型）。
  const selectedAssetModel = useMemo(
    () => imageModels.find((model) => model.catalogId === assetModel) ?? null,
    [assetModel, imageModels],
  );
  const assetModelReady = Boolean(selectedAssetModel);
  const assetCreditCost = useGenerationCreditCost(
    'image_selection',
    selectedAssetModel?.catalogId ?? null,
    { surface: 'canvas', params: { size: '1K' }, quantity: 1 },
  );
  const assetPriceDisplay = useMemo(() => {
    const total = assetCreditCost.data?.data.cost;
    return typeof total === 'number' ? formatCreditCost(total) : null;
  }, [assetCreditCost.data?.data.cost]);

  // ===== 「逐镜出视频」与「成片」：脚本 → 分镜图 → 视频 → 成片的最后两跳 =====
  // 链订阅已在分镜状态之前建立；这一段继续复用同一份 `canvasGraph`，避免同一脚本
  // 在相邻 render 里读到两个不同的画布快照。
  const videoGenConfig: StoryVideoGenConfig = data.videoGenConfig ?? {};
  // 模型解析与视频节点同一套：空绑定落到渠道默认模型（selectVideoModel 的兜底），
  // 派生出来的节点才会带上一个真正可提交的模型，而不是空字符串。
  const { models: videoModels } = useFreezoneVideoModels();
  const persistedVideoModelId =
    typeof videoGenConfig.model === 'string' && videoGenConfig.model.length > 0
      ? videoGenConfig.model
      : null;
  const selectedVideoModel = useMemo(
    () => selectVideoModel(videoModels, persistedVideoModelId),
    [videoModels, persistedVideoModelId],
  );
  const shotVideoModelId = selectedVideoModel?.id ?? persistedVideoModelId ?? '';
  const shotVideoAspect = videoGenConfig.aspectRatio ?? '16:9';
  // 承接边（T-153）的准入：模型合同必须声明能吃下第二张图。判据用同一份实时目录，
  // 与视频节点 `useVideoModeReconciliation` 升级 `allReference` 的前提一致。
  const shotVideoContinuityReference = shotVideoAcceptsChainReference(selectedVideoModel);
  const [isShotVideoDialogOpen, setIsShotVideoDialogOpen] = useState(false);
  const [shotVideoError, setShotVideoError] = useState<string | null>(null);

  const shotVideoPlan = useMemo(
    () =>
      planScriptShotVideos(id, canvasGraph, {
        modelCapabilities: selectedVideoModel,
        model: shotVideoModelId,
        aspectRatio: shotVideoAspect,
        continuityReference: shotVideoContinuityReference,
      }),
    // 图快照变了（这条链上任何节点 / 边变化）就要重算。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, data, canvasGraph, shotVideoContinuityReference, selectedVideoModel, shotVideoModelId, shotVideoAspect],
  );
  const shotVideoPendingCount = shotVideoPlan.ok ? shotVideoPlan.pendingCount : 0;
  const shotVideoHasDerived = shotVideoPlan.ok && shotVideoPlan.mode === 'regenerate';
  // 计价：与视频节点同口径（`video_backend` × 分辨率 × 秒数）。时长用的是计划账里
  // 那批待提交镜头的秒数合计（与落盘时写进节点的同源），不在 UI 侧另算一遍。
  const shotVideoEstimateResolution = (
    normalizeVideoQualityValue(String(selectedVideoModel?.parameterDefaults?.resolution ?? '')) ??
    '720P'
  ).toLowerCase();
  const shotVideoCreditCost = useGenerationCreditCost(
    'video_backend',
    selectedVideoModel?.apiModel ?? null,
    {
      surface: 'canvas',
      params: { resolution: shotVideoEstimateResolution },
      quantity: shotVideoPlan.ok ? shotVideoPlan.plannedSeconds : 0,
    },
  );
  const shotVideoPriceDisplay = useMemo(() => {
    const total = shotVideoCreditCost.data?.data.cost;
    return typeof total === 'number' ? formatCreditCost(total) : null;
  }, [shotVideoCreditCost.data?.data.cost]);

  const handleShotVideoModelChange = useCallback(
    (next: string) => {
      updateNodeData(id, { videoGenConfig: { ...videoGenConfig, model: next } });
    },
    [id, videoGenConfig, updateNodeData],
  );
  const handleShotVideoAspectChange = useCallback(
    (next: string) => {
      const deliverySpec = resolveScriptDeliverySpec(next);
      updateNodeData(id, {
        videoGenConfig: {
          ...videoGenConfig,
          aspectRatio: deliverySpec.aspectRatio,
          deliverySpec,
        },
      });
    },
    [id, videoGenConfig, updateNodeData],
  );

  const runShotVideoScatter = useCallback(
    (generateVideos: boolean) => {
      if (generateVideos && !shotVideoPaidGate.allowed) {
        setShotVideoError(shotVideoPaidGate.reason ?? '当前脚本还不能出片');
        return;
      }
      const result = scatterScriptShotVideos({
        modelCapabilities: selectedVideoModel,
        scriptNodeId: id,
        model: shotVideoModelId,
        aspectRatio: shotVideoAspect,
        generateVideos,
        continuityReference: shotVideoContinuityReference,
      });
      if (!result.ok) {
        setShotVideoError(result.reason);
        return;
      }
      setShotVideoError(null);
      setIsShotVideoDialogOpen(false);
      // 画布开了 onlyRenderVisibleElements：出片也是节点挂载后才自提交的，不把整批
      // 新节点带进视口，落在屏幕外的那些就永远不会提交。
      focusDerivedNodes(result);
      if (!generateVideos) {
        toast.success(`已散出 ${result.nodeIds.length} 个镜头视频节点`);
        return;
      }
      if (result.mode === 'rearmed') {
        toast.success(
          result.armed > 0 ? `已重新排队 ${result.armed} 条镜头视频` : '所有镜头视频都已出片',
        );
        return;
      }
      toast.success(`已散出 ${result.nodeIds.length} 个镜头视频节点，正在出片`);
    },
    [
      id,
      shotVideoAspect,
      shotVideoModelId,
      selectedVideoModel,
      shotVideoPaidGate,
      shotVideoContinuityReference,
    ],
  );
  const handleShotVideoConfirm = useCallback(
    () => runShotVideoScatter(true),
    [runShotVideoScatter],
  );
  const handleShotVideoCreateOnly = useCallback(
    () => runShotVideoScatter(false),
    [runShotVideoScatter],
  );

  // 「成片」：把已经出片的镜头视频按**行序**接进一个视频合成节点（幂等，重复点只补边）。
  // 不弹确认层：它不打任何模型、不花钱，唯一的产物是一个可继续编辑的时间线草稿。
  const runAssembleFilm = useCallback(() => {
    const result = assembleScriptFilm({ scriptNodeId: id, preview: true });
    if (!result.ok) {
      updateNodeData(id, { generationError: result.reason });
      toast.error(result.reason);
      return;
    }
    updateNodeData(id, { ...CLEARED_GENERATION_ERROR_PATCH });
    toast.success(
      result.mode === 'created'
        ? `已建镜头预览，接入 ${result.clipCount} 条视频`
        : `已更新镜头预览，接入 ${result.clipCount} 条视频`,
    );
    if (result.warning) toast.message(result.warning);
    if (result.skippedNoVideo > 0) {
      toast.message(`还有 ${result.skippedNoVideo} 镜没有片子，出片后点「合看镜头」会接上`);
    }
    // 合成节点是下游终点，聚焦它 —— 用户接下来要做的动作（打开时间线）在那里。
    useCanvasStore.getState().requestFocusNode(result.composeNodeId);
  }, [id, updateNodeData]);

  const shotFilm = useMemo(
    () => scriptFilmStatus(id),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, canvasGraph],
  );

  const runStoryboard = useCallback(
    (generateImages: boolean) => {
      if (generateImages && !storyboardPaidGate.allowed) {
        updateNodeData(id, {
          generationError: storyboardPaidGate.reason ?? '当前脚本还不能出图',
        });
        return;
      }
      const result = generateScriptStoryboard({
        scriptNodeId: id,
        rows,
        ledger: assetLedger,
        scriptTitle: scriptTitleOrNodeName,
        scriptSize: { width: resolvedWidth, height: resolvedHeight },
        config: { ...imageGenConfig, model: storyboardModel, aspectRatio: storyboardAspect },
        generateImages,
      });
      if (!result.ok) {
        // 与提交失败走同一处（`data.generationError`）。此前这里写组件本地 state，
        // 取消选中 / 重渲染后就消失，用户会以为没问题了；两条失败路径现在只有一份真相。
        updateNodeData(id, { generationError: result.reason });
        return;
      }
      updateNodeData(id, { ...CLEARED_GENERATION_ERROR_PATCH });
      setIsStoryboardDialogOpen(false);
      // 画布开了 onlyRenderVisibleElements：出图是节点自己挂载后自提交的，
      // 不把新分镜组带进视口，落在屏幕外的那几张就永远不提交。
      focusDerivedNodes(result);
      if (!generateImages) {
        toast.success(`已生成 ${result.nodeIds.length} 张分镜图节点`);
        return;
      }
      if (result.mode === 'rearmed') {
        toast.success(
          result.armed > 0 ? `已重新排队 ${result.armed} 张分镜图` : '所有分镜图都已出图',
        );
        return;
      }
      toast.success(`已生成 ${result.nodeIds.length} 张分镜图，正在出图`);
    },
    [
      id,
      rows,
      assetLedger,
      scriptResult,
      data.scriptTitle,
      resolvedTitle,
      resolvedWidth,
      resolvedHeight,
      imageGenConfig,
      storyboardModel,
      storyboardAspect,
      storyboardPaidGate,
      updateNodeData,
    ],
  );
  const handleStoryboardConfirm = useCallback(() => runStoryboard(true), [runStoryboard]);
  const handleStoryboardCreateOnly = useCallback(() => runStoryboard(false), [runStoryboard]);

  // 「生成资产图」（第三刀）：按台账给角色 / 场景 / 道具各建一张概念图节点，落在脚本左侧。
  // 选中的资产由弹层给（默认勾「还没有我们自己生成的资产图」的那些）。
  const runAssetImages = useCallback(
    (assetIds: string[], generateImages: boolean) => {
      if (!selectedAssetModel) {
        updateNodeData(id, {
          generationError: imageModelsLoading
            ? '正在加载直连生图模型，请稍后再试'
            : '还没有可用的直连生图模型，请先在模型中心配置',
        });
        return;
      }
      const chosen = pickScriptAssets(assetLedger, assetIds);
      const result = generateScriptAssetImages({
        scriptNodeId: id,
        assets: chosen,
        scriptSize: { width: resolvedWidth, height: resolvedHeight },
        config: {
          ...assetGenConfig,
          model: selectedAssetModel.catalogId,
          aspectRatio: assetAspect,
          viewMode: assetViewMode,
        },
        availableModelIds: imageModels.map((model) => model.catalogId),
        style: assetStyle,
        generateImages,
      });
      if (!result.ok) {
        updateNodeData(id, { generationError: result.reason });
        return;
      }
      updateNodeData(id, { ...CLEARED_GENERATION_ERROR_PATCH });
      setIsAssetDialogOpen(false);
      // 与分镜图同一个理由：画布只渲染视口内的节点，出图是节点挂载后自提交的。
      focusDerivedNodes({ nodeIds: result.nodeIds });
      if (!generateImages) {
        toast.success(`已生成 ${result.created} 个资产图节点`);
        return;
      }
      toast.success(
        `已排队 ${result.created} 张资产图，正在出图${result.reused > 0 ? `（另重跑 ${result.reused} 个已有节点）` : ''}`,
      );
    },
    [
      assetAspect,
      assetViewMode,
      assetGenConfig,
      assetLedger,
      assetStyle,
      id,
      imageModels,
      imageModelsLoading,
      resolvedHeight,
      resolvedWidth,
      selectedAssetModel,
      updateNodeData,
    ],
  );
  const handleAssetConfirm = useCallback(
    (assetIds: string[]) => runAssetImages(assetIds, true),
    [runAssetImages],
  );
  const handleAssetCreateOnly = useCallback(
    (assetIds: string[]) => runAssetImages(assetIds, false),
    [runAssetImages],
  );

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, resolvedHeight, resolvedWidth, updateNodeInternals]);

  // Esc 关闭全屏。
  useEffect(() => {
    if (!isFullscreen) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsFullscreen(false);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isFullscreen]);

  const cardToneClass = canvasNodeFrameClass();

  // 三个快捷动作：在脚本节点左侧创建对应类型的上游节点并连边。
  // - fromScript     → 1 个 text 节点
  // - fromVideoRef   → 1 个 video 节点
  // - fromCharacter  → 2 个 upload 节点（垂直堆叠）
  const handlePickAction = useCallback(
    (action: ScriptActionDef) => {
      const state = useCanvasStore.getState();
      const self = state.nodes.find((n) => n.id === id);
      if (!self) return;
      const selfHeight = self.height ?? resolvedHeight;
      const centerY = self.position.y + selfHeight / 2;
      const nodeSize = (
        node: CanvasNode,
        fallbackWidth: number,
        fallbackHeight: number,
      ) => ({
        width:
          node.measured?.width ??
          (typeof node.width === 'number' ? node.width : fallbackWidth),
        height:
          node.measured?.height ??
          (typeof node.height === 'number' ? node.height : fallbackHeight),
      });
      const overlaps = (
        a: { x: number; y: number; width: number; height: number },
        b: { x: number; y: number; width: number; height: number },
      ) => {
        const margin = 12;
        return (
          a.x < b.x + b.width + margin &&
          a.x + a.width + margin > b.x &&
          a.y < b.y + b.height + margin &&
          a.y + a.height + margin > b.y
        );
      };
      const occupiedRects = state.nodes
        .filter((node) => node.id !== self.id)
        .map((node) => {
          const size = nodeSize(node, SPAWN_UPLOAD_WIDTH, SPAWN_UPLOAD_HEIGHT);
          return {
            x: node.position.x,
            y: node.position.y,
            width: size.width,
            height: size.height,
          };
        });

      const spawn = (
        type: CanvasNodeType,
        spawnWidth: number,
        spawnHeight: number,
        offsetY: number,
        extra?: Record<string, unknown>,
      ) => {
        const x = self.position.x - spawnWidth - SPAWN_GAP_X;
        const y = centerY - spawnHeight / 2 + offsetY;
        const newId = state.addNode(type, { x, y }, extra ?? {});
        state.addEdge(newId, id);
        return newId;
      };

      const spawnStacked = (
        type: CanvasNodeType,
        spawnWidth: number,
        spawnHeight: number,
        seeds: ReadonlyArray<Record<string, unknown>>,
      ): string[] => {
        const newIds: string[] = [];
        if (seeds.length === 0) return newIds;
        const baseX = self.position.x - spawnWidth - SPAWN_GAP_X;
        const stepY = spawnHeight + SPAWN_GAP_Y;
        const totalH = spawnHeight * seeds.length + SPAWN_GAP_Y * (seeds.length - 1);
        const preferredStartY = self.position.y + (selfHeight - totalH) / 2;
        const upstreamIds = new Set(
          state.edges.filter((edge) => edge.target === id).map((edge) => edge.source),
        );
        const columnNodes = state.nodes.filter((node) => {
          if (!upstreamIds.has(node.id)) return false;
          if (node.type !== type) return false;
          return Math.abs(node.position.x - baseX) < 8;
        });
        const lastColumnY = columnNodes.reduce<number | null>(
          (maxY, node) => (maxY === null ? node.position.y : Math.max(maxY, node.position.y)),
          null,
        );
        let y =
          lastColumnY === null
            ? preferredStartY
            : Math.max(preferredStartY, lastColumnY + stepY);

        seeds.forEach((seed) => {
          for (let attempt = 0; attempt < 40; attempt += 1) {
            const candidate = { x: baseX, y, width: spawnWidth, height: spawnHeight };
            if (!occupiedRects.some((rect) => overlaps(candidate, rect))) {
              break;
            }
            y += stepY;
          }
          occupiedRects.push({ x: baseX, y, width: spawnWidth, height: spawnHeight });
          const newId = state.addNode(type, { x: baseX, y }, seed);
          state.addEdge(newId, id);
          newIds.push(newId);
          y += stepY;
        });
        return newIds;
      };

      if (action.key === 'fromScript') {
        // 上游 text 节点只用作内容输入：referenceOnly 关掉 mode 列表 / 模型 / 提交。
        const newId = spawn(CANVAS_NODE_TYPES.textAnnotation, SPAWN_TEXT_WIDTH, SPAWN_TEXT_HEIGHT, 0, {
          referenceOnly: true,
          displayName: '剧本',
        });
        state.autoGroupSpawn(id, [newId], { label: `${action.label}组` });
      } else if (action.key === 'fromVideoRef') {
        // 上游 video 节点只用作素材引用：referenceOnly 关掉底部生成操作面板，
        // 顶部 toolbar（剪辑/高清/解析/智能去字幕/...）保持可用。
        const newId = spawn(CANVAS_NODE_TYPES.video, SPAWN_VIDEO_WIDTH, SPAWN_VIDEO_HEIGHT, 0, {
          referenceOnly: true,
        });
        state.autoGroupSpawn(id, [newId], { label: `${action.label}组` });
      } else if (action.key === 'fromCharacter') {
        const newIds = spawnStacked(
          CANVAS_NODE_TYPES.upload,
          SPAWN_UPLOAD_WIDTH,
          SPAWN_UPLOAD_HEIGHT,
          [{ displayName: '角色 1' }, { displayName: '角色 2' }],
        );
        state.autoGroupSpawn(id, newIds, { label: `${action.label}组` });
      }
    },
    [id, resolvedHeight, updateNodeData],
  );

  return (
    <div
      className="group relative h-full w-full overflow-visible"
      style={{ width: resolvedWidth, height: resolvedHeight }}
      onClick={() => setSelectedNode(id)}
    >
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

      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<FileText className="h-4 w-4" />}
        titleText={resolvedTitle}
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <NodeResizeHandle
        minWidth={MIN_WIDTH}
        minHeight={MIN_HEIGHT}
        maxWidth={MAX_WIDTH}
        maxHeight={MAX_HEIGHT}
      />

      <div
        className={`relative flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border ${CANVAS_NODE_PANEL_SURFACE_CLASS} transition-colors ${cardToneClass}`}
      >
        {isGenerating && (
          <NodeGenerationOverlay
            startedAt={data.generationStartedAt ?? null}
            progress={generationTask?.progress ?? null}
            onCancel={canCancelNodeTask ? () => void cancelNodeTask() : undefined}
            cancelPending={isCancellingNodeTask}
          />
        )}
        {hasResult ? (
          <>
            <ScriptResultHeader
              title={scriptTitleText}
              view={activeView}
              onViewChange={handleViewChange}
              onFullscreen={() => setIsFullscreen(true)}
              onRegenerate={() => void submit()}
              onGenerateStoryboard={() => setIsStoryboardDialogOpen(true)}
              onGenerateAssets={() => setIsAssetDialogOpen(true)}
              pendingAssetCount={assetLedger.pendingGeneration.length}
              onShotVideo={() => {
                setShotVideoError(null);
                setIsShotVideoDialogOpen(true);
              }}
              shotVideoDerived={shotVideoHasDerived}
              shotVideoPending={shotVideoPendingCount}
              filmClipCount={shotFilm.clipCount}
              filmHasCompose={shotFilm.hasCompose}
              onAssembleFilm={runAssembleFilm}
              onDownload={handleDownloadCsv}
              isGenerating={isGenerating}
              columnMenu={
                <ScriptColumnMenu
                  mode={columnMode}
                  visibleFields={visibleScriptColumns}
                  allFields={allScriptFields}
                  onModeChange={handleColumnModeChange}
                  onToggleColumn={handleToggleColumn}
                />
              }
            />
            <ScriptStatsBar
              stats={stats}
              rows={rows}
              compact
              blockedShotNumbers={preflight.blockedShotNumbers}
            />
            <ScriptDirectorPlan plan={scriptResult?.director_plan} rows={rows} pending={Boolean(data.scriptDirectorPlanNeedsSync)} disabled={isGenerating || rewriteBusy} onRewriteSequence={handleRewriteSequence} onCommit={plan => updateNodeData(id, { scriptResult: { ...scriptResult, rows, director_plan: plan }, scriptDirectorPlanNeedsSync: true })} />
            <ScriptReadinessBanner
              readiness={readiness}
              onGenerateAssets={() => setIsAssetDialogOpen(true)}
              onGenerateStoryboard={() => setIsStoryboardDialogOpen(true)}
              onOptimize={
                optimizableIssues.length > 0
                  ? () => void handleOptimizeScript()
                  : undefined
              }
              optimizeBusy={optimizeBusy}
              optimizeDisabled={isGenerating}
              optimizeError={optimizeError}
            />
            {/* 脚本行改过 → 分镜图不再对得上。对齐 LibTV 的硬规则「脚本已变更，请先重新
                生成分镜图」：先说清楚**是哪几镜**过期、再给一个就地入口。
                只报张数的话，用户还是得自己一行行找是哪几张对不上。 */}
            {staleShotCount > 0 && (
              <div className="flex items-center gap-2 border-b border-amber-300/25 bg-amber-400/10 px-3 py-1.5">
                <AlertCircle className="h-4 w-4 shrink-0 text-amber-200" />
                <span
                  className="min-w-0 flex-1 truncate text-[12px] leading-5 text-amber-100/90"
                  // 说清**为什么**算过期：提示词改了 / 参考图换了 / 镜号变了或行被删了。
                  // `describeStaleReasons` 早就写好了却没人调用，横幅只能说张数。
                  title={staleSummary}
                >
                  {`脚本已变更，${formatShotNumbers(preflight.staleShotNumbers, 5) || `共 ${staleShotCount} 张`} 的分镜图需重新生成`}
                </span>
                <button
                  type="button"
                  className={HEADER_PRIMARY_CLASS}
                  onClick={(event) => {
                    event.stopPropagation();
                    setIsStoryboardDialogOpen(true);
                  }}
                >
                  重新生成分镜图
                </button>
              </div>
            )}
            {/* 已有结果、只是这次重跑失败：表格上方一条横幅，正文用**人话**收口
                （原始报文收进 title / 详情），否则用户看到的是上游那句机器原文。
                表格还在，所以这里不铺满整张错误卡；空态那条路才用卡片。 */}
            {data.generationError && !isGenerating && (
              <div className="flex items-center gap-2 border-b border-red-500/25 bg-red-500/10 px-3 py-2">
                <AlertCircle className="h-4 w-4 shrink-0 text-red-300" />
                <span
                  className="min-w-0 flex-1 truncate text-[12px] leading-5 text-red-200/90"
                  title={[
                    humanizeGenerationError(data.generationError, data.generationErrorDetails),
                    data.generationErrorRequestId ? `请求 ID：${data.generationErrorRequestId}` : '',
                    data.generationError,
                  ].filter(Boolean).join('\n\n')}
                >
                  {humanizeGenerationError(data.generationError, data.generationErrorDetails)}
                </span>
                <RegenerateButton label="重试" onClick={() => void submit()} />
              </div>
            )}
            <ScriptContractBanner
              report={data.scriptContractReport}
              rows={rows}
              onDismiss={handleDismissContractReport}
            />
            <div className="flex-1 overflow-hidden p-2">
              <ScriptViewBody
                scriptNodeId={id}
                view={activeView}
                rows={rows}
                ledger={assetLedger}
                onCellCommit={handleCellCommit}
                columns={visibleScriptColumns}
                rowFlags={rowFlags}
                rowOperations={scriptRowOperations}
                rowActionsDisabled={isGenerating || optimizeBusy || rewriteBusy}
              />
            </div>
          </>
        ) : data.generationError && !isGenerating ? (
          // 空态失败：铺满节点的人话错误卡 —— 原始报文收进「查看详情」，
          // 主文案是「认证失败 / 额度不足 / 上游超时」这类可操作的一句。
          // 只显示卡片、不再并列「试试」快捷入口：失败当下先解决失败。
          <div className="flex-1 overflow-hidden">
            <NodeGenerationErrorCard
              title="脚本生成失败"
              message={data.generationError}
              details={data.generationErrorDetails}
              requestId={data.generationErrorRequestId}
              stage={data.generationErrorStage}
              suggestedAction={data.generationErrorSuggestedAction}
              retryBusy={isGenerating}
              onRetry={() => void submit()}
              onDismiss={() => updateNodeData(id, { ...CLEARED_GENERATION_ERROR_PATCH })}
            />
          </div>
        ) : (
          <div className="flex-1 overflow-hidden">
            <div className="flex h-full flex-col justify-center gap-2 px-8 py-4">
              {!hasUpstream && (
                <>
                  <div className="text-xs text-[var(--canvas-node-input-helper)]">试试：</div>
                  <div className="flex flex-col gap-0.5">
                    {SCRIPT_ACTIONS.map((action) => {
                      const Icon = action.Icon;
                      return (
                        <button
                          key={action.key}
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            handlePickAction(action);
                          }}
                          className="-mx-2 inline-flex items-center gap-3 rounded-lg px-2 py-2 text-left text-sm text-text-dark transition-colors hover:bg-white/[0.08]"
                        >
                          <Icon className="h-4 w-4 shrink-0 text-text-muted/90" />
                          <span className="truncate">{action.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      {isNodeSelected && (hasUpstream || promptHasContent(data)) && (
        <ScriptOperationsPanel
          nodeId={id}
          data={data}
          references={references}
          nodeWidth={resolvedWidth}
          onSubmit={submit}
          isGenerating={isGenerating}
          historyRecords={historyRecords}
          historyLoading={historyLoading}
          refreshHistory={refreshHistory}
        />
      )}

      {hasResult && isFullscreen && typeof document !== 'undefined' &&
        createPortal(
          <div
            className="fixed inset-0 flex flex-col bg-black/85 p-6"
            style={{ zIndex: SCRIPT_NODE_Z.fullscreen }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between text-text-dark">
              <div className="flex items-center gap-3">
                <FileText className="h-5 w-5" />
                <span className="text-base font-medium">{resolvedTitle}</span>
                {scriptTitleText && (
                  <span className="text-sm text-text-muted">{scriptTitleText}</span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <ScriptViewSwitcher value={activeView} onChange={handleViewChange} />
                {/* 全屏里也留列显隐：全屏宽度远大于节点，默认档会自动带回更多列，
                    用户想钉住某一组列时仍然要有入口。 */}
                {activeView === 'table' && (
                  <ScriptColumnMenu
                    mode={columnMode}
                    visibleFields={visibleScriptColumns}
                    allFields={allScriptFields}
                    onModeChange={handleColumnModeChange}
                    onToggleColumn={handleToggleColumn}
                  />
                )}
                <button
                  type="button"
                  className="inline-flex h-8 items-center gap-1 rounded border border-[rgba(255,255,255,0.2)] bg-bg-dark/60 px-3 text-sm text-text-dark hover:border-[rgba(255,255,255,0.36)]"
                  onClick={handleDownloadCsv}
                >
                  <Download className="h-4 w-4" />
                  下载
                </button>
                <button
                  type="button"
                  className="inline-flex h-8 items-center gap-1 rounded border border-[rgba(255,255,255,0.2)] bg-bg-dark/60 px-3 text-sm text-text-dark hover:border-[rgba(255,255,255,0.36)]"
                  onClick={() => setIsFullscreen(false)}
                >
                  <X className="h-4 w-4" />
                  关闭
                </button>
              </div>
            </div>
            <ScriptStatsBar stats={stats} rows={rows} blockedShotNumbers={preflight.blockedShotNumbers} />
            <ScriptDirectorPlan plan={scriptResult?.director_plan} rows={rows} pending={Boolean(data.scriptDirectorPlanNeedsSync)} disabled={isGenerating || rewriteBusy} onRewriteSequence={handleRewriteSequence} onCommit={plan => updateNodeData(id, { scriptResult: { ...scriptResult, rows, director_plan: plan }, scriptDirectorPlanNeedsSync: true })} />
            <ScriptReadinessBanner
              readiness={readiness}
              onGenerateAssets={() => setIsAssetDialogOpen(true)}
              onGenerateStoryboard={() => setIsStoryboardDialogOpen(true)}
              onOptimize={
                optimizableIssues.length > 0
                  ? () => void handleOptimizeScript()
                  : undefined
              }
              optimizeBusy={optimizeBusy}
              optimizeDisabled={isGenerating}
              optimizeError={optimizeError}
            />
            <div className="flex-1 overflow-hidden rounded-lg border border-[rgba(255,255,255,0.12)] bg-surface-dark/95 p-2">
              <ScriptViewBody
                scriptNodeId={id}
                view={activeView}
                rows={rows}
                ledger={assetLedger}
                onCellCommit={handleCellCommit}
                columns={visibleScriptColumns}
                rowFlags={rowFlags}
                rowOperations={scriptRowOperations}
                rowActionsDisabled={isGenerating || optimizeBusy || rewriteBusy}
              />
            </div>
          </div>,
          document.body,
        )}

      {typeof document !== 'undefined' &&
        createPortal(
          <ScriptStoryboardDialog
            open={isStoryboardDialogOpen}
            mode={storyboardMode}
            shotCount={rows.length}
            pendingCount={shotsToGenerate}
            preflight={preflight}
            assetGap={{
              missingCount: assetPreflight.referenceMissing.length,
              missingSummary: assetPreflight.missingSummary,
            }}
            onFixAssetGap={() => {
              setIsStoryboardDialogOpen(false);
              setIsAssetDialogOpen(true);
            }}
            willRebuild={willRebuildGroup}
            groupLabel={storyboardGroupName}
            model={storyboardModel}
            aspectKey={storyboardAspect}
            priceDisplay={storyboardPriceDisplay}
            paidActionGate={storyboardPaidGate}
            // 出图是异步的：建完节点就返回，弹层在这期间保持「进行中」态。
            // 此前这个 prop 从没传过，弹层里的 spinner 与三处 disabled 永远不可达。
            busy={isGenerating}
            onModelChange={handleStoryboardModelChange}
            onAspectChange={handleStoryboardAspectChange}
            onCancel={() => setIsStoryboardDialogOpen(false)}
            onConfirm={handleStoryboardConfirm}
            onCreateOnly={handleStoryboardCreateOnly}
          />,
          document.body,
        )}

      {typeof document !== 'undefined' &&
        createPortal(
          <ScriptAssetGenDialog
            open={isAssetDialogOpen}
            ledger={assetLedger}
            model={selectedAssetModel?.catalogId ?? ''}
            modelReady={assetModelReady}
            aspectKey={assetAspect}
            viewMode={assetViewMode}
            priceDisplay={assetPriceDisplay}
            busy={isGenerating}
            onModelChange={handleAssetModelChange}
            onAspectChange={handleAssetAspectChange}
            onViewModeChange={handleAssetViewModeChange}
            onCancel={() => setIsAssetDialogOpen(false)}
            onConfirm={handleAssetConfirm}
            onCreateOnly={handleAssetCreateOnly}
          />,
          document.body,
        )}

      {typeof document !== 'undefined' &&
        createPortal(
          <ScriptShotVideoDialog
            open={isShotVideoDialogOpen}
            mode={shotVideoPlan.ok ? shotVideoPlan.mode : 'create'}
            shotCount={shotVideoPlan.ok ? shotVideoPlan.shotCount : 0}
            pendingCount={shotVideoPendingCount}
            willRebuild={shotVideoPlan.ok ? shotVideoPlan.willRebuild : false}
            skippedNoImage={shotVideoPlan.ok ? shotVideoPlan.skippedNoImage : 0}
            skippedNoPrompt={shotVideoPlan.ok ? shotVideoPlan.skippedNoPrompt : 0}
            fallbackPromptCount={shotVideoPlan.ok ? shotVideoPlan.fallbackPromptCount : 0}
            continuityCount={shotVideoPlan.ok ? shotVideoPlan.continuityCount : 0}
            continuitySupported={shotVideoContinuityReference}
            rows={shotVideoPlan.ok ? shotVideoPlan.rows : []}
            model={shotVideoModelId}
            aspectKey={shotVideoAspect}
            priceDisplay={shotVideoPriceDisplay}
            paidActionGate={shotVideoPaidGate}
            // 出片是异步的：建完节点就返回，弹层在这期间保持「进行中」态（与分镜图那条路同款）。
            busy={isGenerating}
            onModelChange={handleShotVideoModelChange}
            onAspectChange={handleShotVideoAspectChange}
            onCancel={() => setIsShotVideoDialogOpen(false)}
            onConfirm={handleShotVideoConfirm}
            onCreateOnly={handleShotVideoCreateOnly}
          />,
          document.body,
        )}

      {typeof document !== 'undefined' &&
        createPortal(
          <ScriptShotRewriteDialog
            open={rewriteTarget !== null || rewriteSequenceId !== null}
            rowIndex={rewriteTarget ?? 0}
            shotNo={rewriteSequenceId ? '' : rewriteShotNo}
            currentSummary={rewriteSequenceId ? rewriteSequenceRows.map(row => `镜 ${row.shot_no}：${row.visual_description ?? ''}`).join('；') : rewriteShotSummary}
            frozenFacts={rewriteSequenceId ? [] : rewriteFrozenFacts}
            untouchedCount={Math.max(0, rows.length - (rewriteSequenceId ? rewriteSequenceRows.length : 1))}
            sequenceLabel={rewriteSequenceId ? (rewriteSequence?.title || rewriteSequenceId) : undefined}
            targetCount={rewriteSequenceId ? rewriteSequenceRows.length : undefined}
            busy={rewriteBusy}
            canCancelTask={canCancelNodeTask && !isCancellingNodeTask}
            error={rewriteError}
            onCancel={handleRewriteCancel}
            onSubmit={submitRewrite}
          />,
          document.body,
        )}

      {/* 逐镜出视频的失败原因（拿不到首帧 / 表里没有可用提示词）就地显示在节点上：
          弹层会因为 onCancel 关掉，原因只留在弹层里就等于没说过。 */}
      {shotVideoError && (
        <div className="pointer-events-auto absolute left-0 right-0 top-full z-[5] mt-1 flex items-center gap-2 rounded-[8px] border border-red-500/25 bg-red-950/60 px-2 py-1 backdrop-blur">
          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-red-300" />
          <span className="min-w-0 flex-1 truncate text-[11px] leading-5 text-red-200/90">
            {shotVideoError}
          </span>
          <button
            type="button"
            className="shrink-0 text-red-200/70 transition-colors hover:text-red-100"
            onClick={(event) => {
              event.stopPropagation();
              setShotVideoError(null);
            }}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}, areCanvasNodePropsEqual);

ScriptNode.displayName = 'ScriptNode';

/** 视图渲染：节点内与全屏共用，保证「看到的视图」两处一致。 */
function ScriptViewBody({
  scriptNodeId,
  view,
  rows,
  ledger,
  onCellCommit,
  columns,
  rowFlags,
  rowOperations,
  rowActionsDisabled,
}: {
  scriptNodeId: string;
  view: ScriptViewId;
  rows: FreezoneStoryScriptRow[];
  /** 资产台账；资产视图读它认回画布上已生成的资产图。 */
  ledger: ScriptAssetLedger;
  onCellCommit: (rowIndex: number, colKey: string, nextValue: string) => void;
  /** 当前可见列（由列显隐决定）；表格视图读它，创意 / 资产视图按字段各自聚合。 */
  columns: ScriptColumnDef[];
  /** 逐镜标记（过期 / 待补），按行键索引。只有表格视图用得上。 */
  rowFlags?: Map<string, { tone: 'stale' | 'defect'; title: string }>;
  /** 行操作（复制 / 插行 / 删行 / 重排）；只有表格视图用得上。 */
  rowOperations?: ScriptRowOperations;
  /** 生成中禁掉行操作，避免编辑和出图抢同一份 `scriptResult`。 */
  rowActionsDisabled?: boolean;
}) {
  if (view === 'creative') return <ScriptCreativeView rows={rows} scriptNodeId={scriptNodeId} ledger={ledger} />;
  if (view === 'video') return <ScriptVideoReview rows={rows} scriptNodeId={scriptNodeId} onRewrite={rowOperations?.onRewrite} disabled={rowActionsDisabled} />;
  if (view === 'asset') return <ScriptAssetView rows={rows} ledger={ledger} />;
  return (
    <ScriptResultTable
      rows={rows}
      columns={columns}
      onCellCommit={onCellCommit}
      rowFlags={rowFlags}
      rowOperations={rowOperations}
      rowActionsDisabled={rowActionsDisabled}
    />
  );
}

function promptHasContent(data: ScriptNodeData): boolean {
  return typeof data.prompt === 'string' && data.prompt.trim().length > 0;
}

interface ScriptResultHeaderProps {
  title: string;
  view: ScriptViewId;
  onViewChange: (next: ScriptViewId) => void;
  onFullscreen: () => void;
  onRegenerate: () => void;
  onGenerateStoryboard: () => void;
  /** 生成资产图（角色 / 场景 / 道具的概念图）。 */
  onGenerateAssets: () => void;
  /** 台账里「还没有我们自己生成的资产图」的资产数量，写成按钮上的待办数。 */
  pendingAssetCount: number;
  /** 逐镜出视频（脚本分镜图 → 每镜一个视频节点）。 */
  onShotVideo: () => void;
  /** 已经派过镜头视频（按钮文案据此在「逐镜出视频 / 重新生成 N / 已出齐」之间切）。 */
  shotVideoDerived: boolean;
  /** 需要重新出片的镜头条数。 */
  shotVideoPending: number;
  /** 已经出好片、可以接进成片的镜头数。 */
  filmClipCount: number;
  /** 已经建过合成节点（按钮文案切到「更新成片」）。 */
  filmHasCompose: boolean;
  onAssembleFilm: () => void;
  onDownload: () => void;
  isGenerating: boolean;
  /** 列显隐菜单（只对表格视图有意义，但一直挂着 —— 切视图回来时状态还在）。 */
  columnMenu?: React.ReactNode;
}

// 与画布其它节点控件同一套语言（见 nodeControlStyles）：28px 高、无描边、
// hover 才出底色，靠间距分组而不是靠边框堆叠；只有一个高对比主按钮。
const HEADER_ACTION_CLASS =
  'nodrag inline-flex h-7 items-center gap-1.5 rounded-[8px] px-2 text-[12px] text-text-dark/85 transition-colors hover:bg-white/[0.08] hover:text-text-dark disabled:pointer-events-none disabled:opacity-40';
const HEADER_PRIMARY_CLASS =
  'nodrag inline-flex h-7 items-center gap-1.5 rounded-[8px] bg-white/[0.16] px-2.5 text-[12px] font-medium text-text-dark transition-colors hover:bg-white/[0.24] disabled:pointer-events-none disabled:opacity-40';

function ScriptResultHeader({
  title,
  view,
  onViewChange,
  onFullscreen,
  onRegenerate,
  onGenerateStoryboard,
  onGenerateAssets,
  pendingAssetCount,
  onShotVideo,
  shotVideoDerived,
  shotVideoPending,
  filmClipCount,
  filmHasCompose,
  onAssembleFilm,
  onDownload,
  isGenerating,
  columnMenu,
}: ScriptResultHeaderProps) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-[rgba(255,255,255,0.06)] px-2.5 py-1.5">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-[12px] font-medium text-text-dark">
          {title || '分镜脚本'}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {/* 视图切换（脚本视图 / 创意视图 / 资产视图）；选择写回节点数据。 */}
        <ScriptViewSwitcher value={view} onChange={onViewChange} />
        {/* 列显隐：只在表格视图下有意义（创意 / 资产视图按字段各自聚合），
            切到别的视图时收起按钮而不是留一个点了没反应的控件。 */}
        {view === 'table' && columnMenu}
        <span className="mx-1 h-4 w-px bg-white/[0.12]" aria-hidden="true" />
        {/* 浮动工具栏（对齐 LibTV 脚本节点）：重新生成 / 生成分镜 / 下载 / 全屏。 */}
        <button
          type="button"
          className={HEADER_ACTION_CLASS}
          onClick={(event) => {
            event.stopPropagation();
            onRegenerate();
          }}
          disabled={isGenerating}
          title="按当前提示词重新生成脚本"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          重新生成
        </button>
        <button
          type="button"
          className={HEADER_ACTION_CLASS}
          onClick={(event) => {
            event.stopPropagation();
            onGenerateAssets();
          }}
          disabled={isGenerating}
          title="为脚本里的角色 / 场景 / 道具生成概念图（资产图），分镜图会把它们当参考图"
        >
          <ImagePlus className="h-3.5 w-3.5" />
          生成资产图
          {pendingAssetCount > 0 && (
            <span className="rounded-full bg-white/[0.16] px-1.5 text-[10px] leading-4 text-text-dark">
              {pendingAssetCount}
            </span>
          )}
        </button>
        <button
          type="button"
          className={HEADER_PRIMARY_CLASS}
          onClick={(event) => {
            event.stopPropagation();
            onGenerateStoryboard();
          }}
          disabled={isGenerating}
          title="按分镜行生成分镜图（可只建节点或直接出图）"
        >
          <Clapperboard className="h-3.5 w-3.5" />
          生成分镜
        </button>
        {/* 「逐镜出视频」：脚本 → 分镜图 → 视频那一条的最后一跳。无镜可出时禁用而不是
            隐藏 —— 隐藏会让用户以为没这个功能，禁用 + title 说清「先出分镜图」才对。 */}
        <button
          type="button"
          className={HEADER_ACTION_CLASS}
          onClick={(event) => {
            event.stopPropagation();
            onShotVideo();
          }}
          disabled={isGenerating || filmClipCount + shotVideoPending === 0}
          title="按分镜行逐镜散出视频节点：首帧取该镜分镜图，提示词取该行的视频运动提示词"
        >
          <Video className="h-3.5 w-3.5" />
          {!shotVideoDerived
            ? '逐镜出视频'
            : shotVideoPending > 0
              ? `重新生成视频 ${shotVideoPending}`
              : '视频已出齐'}
        </button>
        {/* 「成片」：把已出片的镜头按行序接进视频合成节点（不花钱，产物是可继续编辑的时间线）。
            一镜都没出片时禁用 —— 那时候建出合成节点只会开出一个空时间线。 */}
        <button
          type="button"
          className={HEADER_ACTION_CLASS}
          onClick={(event) => {
            event.stopPropagation();
            onAssembleFilm();
          }}
          disabled={isGenerating || filmClipCount === 0}
          title="按剧本顺序合看已有视频，未完成镜头跳过；规格差异不妨碍预览"
        >
          <Film className="h-3.5 w-3.5" />
          {filmHasCompose ? `更新预览 ${filmClipCount}` : `合看镜头 ${filmClipCount}`}
        </button>
        <button
          type="button"
          className={HEADER_ACTION_CLASS}
          onClick={(event) => {
            event.stopPropagation();
            onDownload();
          }}
          title="导出分镜表 CSV"
        >
          <Download className="h-3.5 w-3.5" />
          下载
        </button>
        <button
          type="button"
          className={HEADER_ACTION_CLASS}
          onClick={(event) => {
            event.stopPropagation();
            onFullscreen();
          }}
          title="全屏查看"
        >
          <Expand className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

/**
 * 行操作回调：由节点实现、表格只负责触发。
 *
 * 表格不认识 `shot_id`，也不做任何身份推导 —— 复制 / 插行 / 删行 / 重排后的
 * 新行数组由节点侧按稳定身份合同算好，再写回 `scriptResult.rows`。
 */
export interface ScriptRowOperations {
  /** 在当前行下方插入一个新镜头。 */
  onInsertAfter: (rowIndex: number) => void;
  /** 复制当前行；新行拿新身份，不共享原镜头。 */
  onDuplicate: (rowIndex: number) => void;
  /** 上移 / 下移一位；只改片序。 */
  onMove: (rowIndex: number, delta: -1 | 1) => void;
  /** 删除当前行。 */
  onDelete: (rowIndex: number) => void;
  /** 只重写这一镜：其余行原样保留，角色卡与第 7/8 段由服务端回填。 */
  onRewrite?: (rowIndex: number) => void;
  /** 只剩一行时禁用删除，避免整张表被删空。 */
  canDelete: boolean;
}

interface ScriptResultTableProps {
  rows: FreezoneStoryScriptRow[];
  /** 当前可见列（由列显隐决定）；宽度按它现算，收起来就不留空档。 */
  columns: ScriptColumnDef[];
  onCellCommit?: (rowIndex: number, colKey: string, nextValue: string) => void;
  /** 逐镜标记（过期 / 待补），按行键索引 —— 键与 `buildScriptRowKeys` 同一套。 */
  rowFlags?: Map<string, { tone: 'stale' | 'defect'; title: string }>;
  /** 行操作（复制 / 插行 / 删行 / 重排）；不传则整列不渲染。 */
  rowOperations?: ScriptRowOperations;
  /** 生成中禁掉行操作。 */
  rowActionsDisabled?: boolean;
}

// 单格最大高度：内容多的格子封顶，避免整行被一行长文撑到几千像素。
// 滚动由**外层表格容器**统一承担 —— 早先这里还有一层 `overflow-y-auto`，
// 结果是「表格可以滚、每格也能滚」两层滚动条叠在一起（鼠标停在格子里滚不动外层）。
const CELL_MAX_HEIGHT_PX = 196;

// 行操作列：复制 / 插行 / 上移 / 下移 / 删除 / 只改这一镜六个动作排成一行，固定列宽。
const ROW_ACTIONS_WIDTH_PX = 140;
const ROW_ACTION_BUTTON_CLASS =
  'nodrag inline-flex h-5 w-5 items-center justify-center rounded-[5px] text-text-muted/80 transition-colors hover:bg-white/[0.1] hover:text-text-dark disabled:pointer-events-none disabled:opacity-30';

export function ScriptResultTable({
  rows,
  columns,
  onCellCommit,
  rowFlags,
  rowOperations,
  rowActionsDisabled,
}: ScriptResultTableProps) {
  // 镜号 / 时长这类短数字列居中、等宽数字，便于扫读。按**当前列集**现算，
  // 列被收起时不会留下「居中的空列」。
  const numericColumnKeys = new Set(
    columns.filter((col) => col.numeric).map((col) => col.key),
  );
  const showRowActions = Boolean(rowOperations);
  const tableMinWidth =
    columns.reduce((sum, col) => sum + col.widthPx, 0) +
    (showRowActions ? ROW_ACTIONS_WIDTH_PX : 0);
  // 行键用稳定行键（镜号 / 关键帧序号 / 回落下标），与「生成分镜」写节点时同一套。
  // 用下标当 key 时，删行 / 重排会让 React 复用错位的 DOM，正在编辑的格子里会
  // 留下上一行的文本。
  const rowKeys = useMemo(() => buildScriptRowKeys(rows), [rows]);
  // 「镜号」列的表头在打了标时多一枚圆点：一眼看出这张表里有几行要处理，
  // 而不必去读横幅的措辞。标本身挂在第一个可见单元格的左侧色条上（见下）。
  const flaggedCount = rowFlags ? rowKeys.filter((key) => rowFlags.has(key)).length : 0;
  return (
    <div className="ui-scrollbar h-full w-full overflow-auto rounded-lg border border-[rgba(255,255,255,0.08)] bg-bg-dark/30">
      <table
        className="border-collapse text-left text-[12px] text-text-dark"
        style={{ minWidth: tableMinWidth, tableLayout: 'fixed' }}
      >
        <thead className="sticky top-0 z-10">
          <tr>
            {showRowActions && (
              <th
                style={{ width: ROW_ACTIONS_WIDTH_PX, minWidth: ROW_ACTIONS_WIDTH_PX }}
                className="border-b border-r border-b-[rgba(255,255,255,0.14)] border-r-[rgba(255,255,255,0.06)] bg-bg-dark/95 px-1 py-2.5"
              >
                <span className="sr-only">行操作</span>
              </th>
            )}
            {columns.map((col, columnIndex) => (
              <th
                key={col.key}
                style={{ width: col.widthPx, minWidth: col.widthPx }}
                className={`border-b border-r border-b-[rgba(255,255,255,0.14)] border-r-[rgba(255,255,255,0.06)] bg-bg-dark/95 px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted/90 backdrop-blur last:border-r-0 ${
                  numericColumnKeys.has(col.key) ? 'text-center' : ''
                }`}
                title={
                  columnIndex === 0 && flaggedCount > 0
                    ? `这张表里有 ${flaggedCount} 行带标记：脚本变更过的、或缺开拍条件的`
                    : undefined
                }
              >
                {col.label}
                {columnIndex === 0 && flaggedCount > 0 && (
                  <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-300/90 align-middle" />
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const flag = rowFlags?.get(rowKeys[idx] ?? '');
            return (
              <tr
                key={rowKeys[idx] ?? idx}
                className={`group/row align-top transition-colors hover:bg-[rgb(var(--accent-rgb)/0.06)] ${
                  idx % 2 === 1 ? 'bg-white/[0.02]' : ''
                }`}
              >
                {showRowActions && rowOperations && (
                  <td
                    style={{ width: ROW_ACTIONS_WIDTH_PX, minWidth: ROW_ACTIONS_WIDTH_PX }}
                    className="border-b border-r border-[rgba(255,255,255,0.05)] px-1 py-1.5 align-top"
                  >
                    <div className="flex items-center gap-0.5 opacity-45 transition-opacity focus-within:opacity-100 group-hover/row:opacity-100">
                      <button
                        type="button"
                        title="在下方插入一行"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled}
                        onClick={() => rowOperations.onInsertAfter(idx)}
                      >
                        <Plus className="h-3 w-3" />
                        <span className="sr-only">在下方插入一行</span>
                      </button>
                      <button
                        type="button"
                        title="复制这一行（新镜头，新身份）"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled}
                        onClick={() => rowOperations.onDuplicate(idx)}
                      >
                        <Copy className="h-3 w-3" />
                        <span className="sr-only">复制这一行</span>
                      </button>
                      <button
                        type="button"
                        title="上移一位"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled || idx === 0}
                        onClick={() => rowOperations.onMove(idx, -1)}
                      >
                        <ArrowUp className="h-3 w-3" />
                        <span className="sr-only">上移一位</span>
                      </button>
                      <button
                        type="button"
                        title="下移一位"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled || idx === rows.length - 1}
                        onClick={() => rowOperations.onMove(idx, 1)}
                      >
                        <ArrowDown className="h-3 w-3" />
                        <span className="sr-only">下移一位</span>
                      </button>
                      <button
                        type="button"
                        title="删除这一行"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled || !rowOperations.canDelete}
                        onClick={() => rowOperations.onDelete(idx)}
                      >
                        <Trash2 className="h-3 w-3" />
                        <span className="sr-only">删除这一行</span>
                      </button>
                      {rowOperations.onRewrite && (
                        <button
                          type="button"
                          title="只改这一镜（其余行逐字不动）"
                          className={ROW_ACTION_BUTTON_CLASS}
                          disabled={rowActionsDisabled}
                          onClick={() => rowOperations.onRewrite?.(idx)}
                        >
                          <Wand2 className="h-3 w-3" />
                          <span className="sr-only">只改这一镜</span>
                        </button>
                      )}
                    </div>
                  </td>
                )}
                {columns.map((col, columnIndex) => {
                  const numeric = numericColumnKeys.has(col.key);
                  // 标记画在行首单元格的左边框上：一处色条标整行，不占列宽，
                  // 也不会因为列被收起就消失（只要还有任意一列可见）。
                  const flagOnFirstCell = columnIndex === 0 && Boolean(flag);
                  return (
                    <td
                      key={col.key}
                      style={{ width: col.widthPx, minWidth: col.widthPx }}
                      title={flagOnFirstCell ? flag?.title : undefined}
                      className={`border-b border-r border-[rgba(255,255,255,0.05)] px-3 py-2 align-top last:border-r-0 ${
                        numeric ? 'text-center tabular-nums text-text-dark/90' : ''
                      } ${
                        flagOnFirstCell
                          ? flag?.tone === 'stale'
                            ? 'border-l-2 border-l-amber-300/80 bg-amber-400/[0.06]'
                            : 'border-l-2 border-l-orange-400/70'
                          : ''
                      }`}
                    >
                      <div className="overflow-hidden" style={{ maxHeight: CELL_MAX_HEIGHT_PX }}>
                        <ScriptResultCell
                          row={row}
                          col={col}
                          onCommit={
                            onCellCommit
                              ? (next) => onCellCommit(idx, col.key, next)
                              : undefined
                          }
                        />
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface ScriptResultCellProps {
  row: FreezoneStoryScriptRow;
  col: ScriptColumnDef;
  onCommit?: (nextValue: string) => void;
}

function ScriptResultCell({ row, col, onCommit }: ScriptResultCellProps) {
  const raw = row[col.key];

  if (col.render === 'image') {
    // 图片 URL 由服务端从当前项目资产中回填；仅渲染可验证的图片来源，防止模型文本
    // 或失效外链冒充参考图。
    const url =
      typeof raw === 'string' && isRenderableImageSrc(raw) ? raw : null;
    if (!url) {
      return (
        <div className="flex h-14 w-14 items-center justify-center rounded border border-dashed border-[rgba(255,255,255,0.14)] text-text-muted/50">
          <ImageIcon className="h-4 w-4" />
        </div>
      );
    }
    return (
      <img
        src={resolveImageDisplayUrl(url)}
        alt=""
        className="h-14 w-14 rounded border border-[rgba(255,255,255,0.08)] object-cover"
        draggable={false}
      />
    );
  }

  const initialText =
    raw == null
      ? ''
      : typeof raw === 'string' || typeof raw === 'number'
        ? String(raw)
        : JSON.stringify(raw);

  if (!onCommit) {
    // 没传 onCommit 视为只读，保留旧渲染。
    if (initialText.length === 0) {
      return <span className="text-text-muted/50">-</span>;
    }
    return (
      <span className="block whitespace-pre-wrap break-words leading-snug">{initialText}</span>
    );
  }

  return <EditableTableCell value={initialText} onCommit={onCommit} />;
}

interface ScriptOperationsPanelProps {
  nodeId: string;
  data: ScriptNodeData;
  references: ScriptReference[];
  /** 节点当前渲染宽度 —— 外层面板的外溢量按它算（最小宽度不外溢）。 */
  nodeWidth: number;
  /** 与节点本体「重试」共用的提交实例 + 历史（见 ScriptNode 里的 hook 调用）。 */
  onSubmit: () => Promise<void>;
  isGenerating: boolean;
  historyRecords: FreezoneGenerationHistoryRecord[];
  historyLoading: boolean;
  refreshHistory: () => Promise<void>;
}

function ScriptOperationsPanel({
  nodeId,
  data,
  references,
  nodeWidth,
  onSubmit,
  isGenerating,
  historyRecords,
  historyLoading,
  refreshHistory,
}: ScriptOperationsPanelProps) {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const [isTranslating, setIsTranslating] = useState(false);
  const { models: textModels } = useDirectModelCatalog('text');
  const resolvedTextModel = resolveDirectCanvasModelId(data.model, textModels);
  const { models: visionModels } = useDirectModelCatalog('vision');
  const resolvedVisionModel = resolveDirectCanvasModelId(data.model, visionModels);
  // 收起态是节点下方的浮动面板；点右上角「放大」后改为居中弹窗展示同一份内容。
  const [panelExpanded, setPanelExpanded] = useState(false);
  const scriptCost = useGenerationCreditCost('freezone_story_script');

  const handleRestoreHistory = useCallback(
    (record: FreezoneGenerationHistoryRecord) => {
      if (!isScriptResult(record.result)) return;
      updateNodeData(nodeId, {
        scriptResult: normalizeScriptResultIdentities(record.result), scriptDirectorPlanNeedsSync: false,
        scriptTitle: record.result.title ?? null,
        isGenerating: false,
        generationStartedAt: null,
      });
    },
    [nodeId, updateNodeData],
  );

  const prompt = typeof data.prompt === 'string' ? data.prompt : '';
  // Story-script execution switches to the vision model when video or image
  // references are attached. Keep the picker on that same domain so the id
  // shown to the user is the id that the submit path resolves and executes.
  const hasVisionReferences = references.some(
    (ref) => (ref.kind === 'video' && Boolean(ref.videoUrl)) ||
      (ref.kind === 'image' && Boolean(ref.thumbUrl)),
  );

  const handleTranslate = useCallback(async () => {
    if (isGenerating || isTranslating) return;
    if (prompt.trim().length === 0) return;
    if (!resolvedTextModel) return;
    const project = readUrl().project;
    if (!project) {
      // 翻译失败此前只有 console.error：用户点完按钮看不到任何反馈，
      // 会以为是自己没点到。现在与非翻译失败同走 `data.generationError`。
      updateNodeData(nodeId, { generationError: '当前页面缺少 project 参数，无法翻译' });
      return;
    }
    setIsTranslating(true);
    try {
      const ref = await submitFreezoneTextTranslate(project, {
        text: prompt,
        nodeType: 'text',
        model: resolvedTextModel || undefined,
        canvasId: readUrl().canvas ?? 'default',
        nodeId,
      });
      await awaitTaskCompletion(ref.task_key, project);
      const result = await fetchFreezoneTextTranslateResult(project, ref.job_id);
      updateNodeData(nodeId, { prompt: result.translated_text, ...CLEARED_GENERATION_ERROR_PATCH });
    } catch (error) {
      console.error('[script-node] translate failed', error);
      const diagnostics = resolveGenerationErrorDiagnostics(error);
      updateNodeData(nodeId, {
        generationError: error instanceof Error ? error.message : '翻译失败',
        generationErrorDetails: diagnostics.details,
        generationErrorRequestId: diagnostics.requestId,
        generationErrorStage: diagnostics.stage,
        generationErrorSuggestedAction: diagnostics.suggestedAction,
        generationErrorCode: diagnostics.errorCode,
        generationErrorRetryable: diagnostics.retryable,
      });
    } finally {
      setIsTranslating(false);
    }
  }, [isGenerating, isTranslating, nodeId, prompt, resolvedTextModel, updateNodeData]);

  // 文本 / 视频 / 角色图任一有内容即可提交（与 useScriptStorySubmit 的分流一致）。
  const hasContent =
    prompt.trim().length > 0 ||
    references.some(
      (ref) =>
        (ref.kind === 'text' && (ref.text ?? '').trim().length > 0) ||
        (ref.kind === 'video' && Boolean(ref.videoUrl)) ||
        (ref.kind === 'image' && Boolean(ref.thumbUrl)),
    );
  const submitDisabled = isGenerating || !hasContent || (
    hasVisionReferences ? !resolvedVisionModel : !resolvedTextModel
  );
  // 外溢按节点当前宽度算：最小宽度（360）下为 0，面板与节点等宽，不会两侧悬空。
  const panelOverhang = resolveScriptPanelOverhang(nodeWidth);

  return (
    <OperationPanelShell
      expanded={panelExpanded}
      onCollapse={() => setPanelExpanded(false)}
      inlineClassName={`nodrag absolute z-10 flex flex-col rounded-[var(--node-radius)] border ${CANVAS_NODE_INPUT_SURFACE_CLASS} ${CANVAS_NODE_INPUT_FRAME_CLASS}`}
      inlineStyle={{
        top: `calc(100% + ${PANEL_GAP_PX}px)`,
        left: -panelOverhang,
        right: -panelOverhang,
      }}
      modalStyle={{
        width: `min(${OPS_PANEL_EXPANDED_WIDTH}px, 92vw)`,
        height: `min(${OPS_PANEL_EXPANDED_HEIGHT}px, 86vh)`,
      }}
    >
      <PanelExpandButton
        expanded={panelExpanded}
        onToggle={() => setPanelExpanded((v) => !v)}
        className="absolute right-2 top-2 z-20"
      />
      {references.length > 0 && (
        <div className="px-3 pr-10 pt-3">
          <ScriptReferencesRow references={references} />
        </div>
      )}

      <div className={`px-3 pt-3 ${panelExpanded ? 'flex-1 overflow-hidden' : ''}`}>
        <textarea
          value={prompt}
          onChange={(event) => updateNodeData(nodeId, { prompt: event.target.value })}
          placeholder="描述剧情或添加角色参考、视频参考等，为你生成分镜脚本"
          rows={3}
          className={`nodrag nowheel ui-scrollbar w-full resize-none bg-transparent text-[14px] leading-[1.6] text-text-dark outline-none ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS} ${panelExpanded ? 'h-full' : 'min-h-[72px]'}`}
          disabled={isGenerating}
        />
      </div>

      {/* 面板里的错误行也用同一句人话（原始报文进 title），
          免得同一个失败在节点上和面板里说两种话。 */}
      {data.generationError && !isGenerating && (
        <div
          className="px-3 pb-1 text-[11px] text-red-400 break-words [overflow-wrap:anywhere]"
          title={[data.generationErrorRequestId ? `请求 ID：${data.generationErrorRequestId}` : '', data.generationError].filter(Boolean).join('\n\n')}
        >
          {humanizeGenerationError(data.generationError, data.generationErrorDetails)}
        </div>
      )}

      <div className="flex shrink-0 items-center justify-between gap-2 px-3 pb-3 pt-1">
        <DirectModelPicker
          kind={hasVisionReferences ? "vision" : "text"}
          value={data.model}
          onChange={(nextModelId) => updateNodeData(nodeId, { model: nextModelId })}
          ariaLabel={hasVisionReferences ? "故事脚本视觉模型" : "故事脚本文字模型"}
        />
        <div className="flex shrink-0 items-center gap-2">
          <IconButton
            title="翻译（中英文互译）"
            onClick={handleTranslate}
            disabled={isGenerating || isTranslating || prompt.trim().length === 0 || !resolvedTextModel}
            active={isTranslating}
          >
            {isTranslating ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Languages className="h-4 w-4" />
            )}
          </IconButton>
          <CreditCostPill
            display={scriptCost.data?.data.display}
            disabled={submitDisabled}
            className={NODE_CREDIT_PILL_FLAT_CLASS}
          />
          <button
            type="button"
            disabled={submitDisabled}
            title="生成"
            onClick={() => void onSubmit()}
            className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
              submitDisabled
                ? NODE_GENERATE_BUTTON_DISABLED_CLASS
                : NODE_GENERATE_BUTTON_ENABLED_CLASS
            }`}
          >
            {isGenerating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ArrowUp className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      {hasCompletedHistoryRecords(historyRecords) && (
        <div className="border-t border-white/[0.04] px-3 py-2">
          <NodeGenerationHistory
            records={historyRecords}
            isLoading={historyLoading}
            onRestore={handleRestoreHistory}
            onRefresh={() => void refreshHistory()}
            isActive={(record) => {
              if (!isScriptResult(record.result) || !isScriptResult(data.scriptResult)) {
                return false;
              }
              return JSON.stringify(record.result) === JSON.stringify(data.scriptResult);
            }}
          />
        </div>
      )}
    </OperationPanelShell>
  );
}

interface ScriptReferencesRowProps {
  references: ScriptReference[];
}

function ScriptReferencesRow({ references }: ScriptReferencesRowProps) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {references.map((ref, index) => (
        <ScriptReferenceChip key={ref.nodeId} reference={ref} index={index} />
      ))}
    </div>
  );
}

interface ScriptReferenceChipProps {
  reference: ScriptReference;
  index: number;
}

function ScriptReferenceChip({ reference, index }: ScriptReferenceChipProps) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [previewPos, setPreviewPos] = useState<{ left: number; top: number } | null>(null);
  const PREVIEW_W = 240;
  const PREVIEW_OFFSET = 10;

  // 仅 image / video 有可视预览；text / audio chip 不弹大图。
  const hasPreview =
    (reference.kind === 'image' && Boolean(reference.thumbUrl)) ||
    (reference.kind === 'video' && Boolean(reference.videoUrl || reference.thumbUrl));

  const showPreview = useCallback(() => {
    if (!hasPreview) return;
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    const left = Math.max(
      8,
      Math.min(window.innerWidth - PREVIEW_W - 8, rect.left + rect.width / 2 - PREVIEW_W / 2),
    );
    const top = rect.top - PREVIEW_OFFSET;
    setPreviewPos({ left, top });
  }, [hasPreview]);

  const hidePreview = useCallback(() => {
    setPreviewPos(null);
  }, []);

  const titleText = reference.displayName?.trim()
    ? reference.displayName.trim()
    : `引用 ${index + 1}`;

  // chip 视觉：image 用缩略图，video 用首帧（fallback 视频元素），text 用 T 字标，audio 用 A。
  const chipBody = (() => {
    if (reference.kind === 'image' && reference.thumbUrl) {
      return (
        <img
          src={resolveImageDisplayUrl(reference.thumbUrl)}
          alt={titleText}
          className="h-full w-full object-cover"
        />
      );
    }
    if (reference.kind === 'video') {
      if (reference.thumbUrl) {
        return (
          <img
            src={resolveImageDisplayUrl(reference.thumbUrl)}
            alt={titleText}
            className="h-full w-full object-cover"
          />
        );
      }
      if (reference.videoUrl) {
        return (
          <video
            src={resolveImageDisplayUrl(reference.videoUrl)}
            muted
            playsInline
            preload="metadata"
            className="h-full w-full object-cover"
          />
        );
      }
      return <Video className="h-4 w-4 text-text-muted" />;
    }
    if (reference.kind === 'text') {
      return <span className="text-[11px] font-semibold text-text-muted">T</span>;
    }
    return <span className="text-[11px] text-text-muted">A</span>;
  })();

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onMouseEnter={showPreview}
        onMouseLeave={hidePreview}
        onFocus={showPreview}
        onBlur={hidePreview}
        onClick={() => (previewPos ? hidePreview() : showPreview())}
        disabled={!hasPreview}
        aria-expanded={hasPreview ? Boolean(previewPos) : undefined}
        className="nodrag relative flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-[7px] border border-white/10 bg-white/[0.04] transition-colors hover:border-white/30 disabled:cursor-default disabled:hover:border-white/10"
        title={hasPreview ? `${titleText} · 点击预览` : titleText}
      >
        {chipBody}
        <span className="absolute right-1 top-1 flex h-3 min-w-3 items-center justify-center rounded-full bg-black/30 px-0.5 text-[9px] font-medium leading-none text-white/90 backdrop-blur-sm">
          {index + 1}
        </span>
      </button>
      {previewPos &&
        hasPreview &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            className="pointer-events-none fixed -translate-y-full"
            style={{
              left: previewPos.left,
              top: previewPos.top,
              width: PREVIEW_W,
              // 贴在节点自己这一层：全屏/弹层打开时必须压在蒙层之下，
              // 否则节点里这张预览会画在整块暗幕上面。
              zIndex: SCRIPT_NODE_Z.chipPreview,
            }}
          >
            <div className="overflow-hidden rounded-xl border border-white/15 bg-surface-dark/95 shadow-2xl backdrop-blur-sm">
              {reference.kind === 'video' && reference.videoUrl ? (
                <video
                  src={resolveImageDisplayUrl(reference.videoUrl)}
                  autoPlay
                  loop
                  muted
                  playsInline
                  className="block h-auto w-full object-contain"
                />
              ) : reference.thumbUrl ? (
                <img
                  src={resolveImageDisplayUrl(reference.thumbUrl)}
                  alt={titleText}
                  className="block h-auto w-full object-contain"
                  draggable={false}
                />
              ) : null}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

interface IconButtonProps {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  children: React.ReactNode;
}

function IconButton({ title, onClick, disabled, active, children }: IconButtonProps) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`${NODE_INLINE_ICON_BUTTON_CLASS} ${
        active ? NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS : ''
      }`}
    >
      {children}
    </button>
  );
}
