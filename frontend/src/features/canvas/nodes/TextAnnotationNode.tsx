// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import {
  AlignJustify,
  ArrowUp,
  Check,
  FileText,
  Image as ImageIcon,
  Languages,
  Loader2,
  Music,
  Music2,
  PlaySquare,
  WandSparkles,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  CANVAS_NODE_TYPES,
  type TextAnnotationNodeData,
  type TextNodeMode,
  type TextPrepareMode,
  type TextPreparePreview,
  type UploadImageNodeData,
  type VideoGenCount,
  type VideoGenQuality,
  type VideoNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { normalizeVideoAudioPreference } from '@/features/canvas/domain/videoCapabilityCompiler';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { isSystemManagedNodeData } from '@/features/canvas/domain/mainlineNodeFlags';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import { NodeResizeHandle } from '@/features/canvas/ui/NodeResizeHandle';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import {
  CANVAS_NODE_INPUT_BODY_FRAME_CLASS,
  CANVAS_NODE_INPUT_FRAME_CLASS,
  CANVAS_NODE_INPUT_PLACEHOLDER_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
} from '@/features/canvas/ui/nodeFrameStyles';
import { useCanvasStore, useIsBoxSelecting } from '@/stores/canvasStore';
import {
  ensureBackendImageUrl,
  fetchFreezoneReversePromptResult,
  fetchFreezoneTextTranslateResult,
  submitFreezoneReversePrompt,
  submitFreezoneTextTranslate,
  submitFreezoneVideoGen,
  type FreezoneVideoAspectRatio,
  type FreezoneVideoResolution,
} from '@/api/ops';
import {
  fetchFreezoneTextPrepareResult,
  submitFreezoneTextPrepare,
} from '@/api/freezoneTextPrepare';
import { awaitTaskCompletion } from '@/api/tasks';
import { generationTaskDescriptor } from '@/features/canvas/application/resumeGeneration';
import {
  GENERATION_CONCURRENCY_DEFAULT,
  clampGenerationBatchCount,
  clearGenerationIntent,
  markGenerationIntent,
  runGenerationQueue,
  withGlobalGenerationSlot,
} from '@/features/canvas/application/generationConcurrency';
import { upstreamGenerationGate } from '@/features/canvas/application/generationDependencies';
import { useNodeGenerationTaskState } from '@/features/canvas/application/useNodeGenerationTaskState';
import { useCancelNodeGeneration } from '@/features/canvas/application/useCancelNodeGeneration';
import { readUrl } from '@/lib/url-params';
import {
  DEFAULT_SHARED_MODEL_ID,
  DEFAULT_VIDEO_MODEL_ID,
  ProviderModelPicker,
} from '@/features/canvas/ui/ProviderModelPicker';
import { DirectModelPicker } from '@/features/canvas/ui/DirectModelPicker';
import {
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
  NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS,
  NODE_INLINE_ICON_BUTTON_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import { useFreezoneVideoModels } from '@/features/canvas/hooks/useFreezoneVideoModels';
import { selectVideoModel } from '@/features/canvas/domain/videoModelSelection';
import {
  resolveDirectCanvasModelId,
  useDirectModelCatalog,
} from '@/features/canvas/hooks/useDirectModelCatalog';
import { CreditCostInline } from '@/components/credit-cost-inline';
import { UiSelect } from '@/components/ui';
import { useGenerationCreditCost } from '@/lib/queries/generation-credit-cost';
import {
  buildTextPrepareApplyPatch,
  buildTextPreparePreview,
  isTextPreparePreviewStale,
} from './text/textPrepare';

type TextAnnotationNodeProps = NodeProps & {
  id: string;
  data: TextAnnotationNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 440;
const DEFAULT_HEIGHT = 220;
const COMPACT_DEFAULT_HEIGHT = 320;
const MIN_WIDTH = 380;
const MIN_HEIGHT = 240;
const COMPACT_MIN_HEIGHT = 240;
const MAX_WIDTH = 900;
const MAX_HEIGHT = 1200;

const PICKER_INSET = 32;
const COMPACT_OPS_PANEL_HEIGHT = 140;
const COMPACT_OPS_PANEL_GAP = 12;
const COMPACT_OPS_PANEL_MIN_WIDTH = 480;
const WRITING_OPS_PANEL_HEIGHT = 140;
const WRITING_PREVIEW_PANEL_HEIGHT = 300;
const WRITING_PREVIEW_PANEL_MIN_WIDTH = 560;

const COMPACT_MODES = new Set<TextNodeMode>(['textToVideo', 'imageToPrompt']);

const IMAGE_TO_PROMPT_DEFAULT_CONTENT =
  '根据图片生成结构化中文提示词，包括主体描述、环境、光影、镜头语言、风格关键词。';

// 「文字生成音乐」的默认音乐描述——点击后预填进文本节点，用户可在此基础上改。
const TEXT_TO_MUSIC_DEFAULT_CONTENT =
  '生成一首现代品牌电子音乐（约 110 BPM），干净有力的低频贝斯，清晰电子鼓点，整体风格高级、未来感强。开场节奏型贝斯与简洁合成器音色建立律动。主段加入稳定鼓点，节奏清晰，保持克制的张力。强化段加入更丰富的音层，合成器音色提升，律动增强但不过度拥挤。结尾鼓点减弱，仅保留低频与氛围音渐出，干净利落收尾。';

const SPAWN_UPLOAD_WIDTH = 320;

/**
 * 解析上游图片节点「眼下展示的那张图」的 URL。和 graphContentResolver 的图片分支
 * 保持同一套回退顺序：生成结果 imageUrl → previewImageUrl → referenceImageUrl，
 * 这样图生节点只挂了参考图（还没生成）时也能被识别为可引用素材。
 */
function resolveUpstreamImageUrl(data: unknown): string | null {
  const d = data as
    | { imageUrl?: unknown; previewImageUrl?: unknown; referenceImageUrl?: unknown }
    | undefined;
  for (const candidate of [d?.imageUrl, d?.previewImageUrl, d?.referenceImageUrl]) {
    if (typeof candidate === 'string' && candidate.length > 0) return candidate;
  }
  return null;
}

const REAL_MODES = new Set<TextNodeMode>([
  'writing',
  'textToVideo',
  'imageToPrompt',
  'textToMusic',
  'textToMusicGen',
]);

function resolveVideoOutputUrl(result: Record<string, unknown> | null | undefined): string | null {
  if (!result) return null;
  for (const key of ['video_url', 'output_url', 'url']) {
    const value = result[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return null;
}

function qualityToResolution(q: VideoGenQuality): FreezoneVideoResolution {
  return q.toLowerCase() as FreezoneVideoResolution;
}

const MODES: ReadonlyArray<{
  key: TextNodeMode;
  icon: typeof FileText;
  labelKey: string;
}> = [
  { key: 'writing', icon: FileText, labelKey: 'node.textNode.modes.writing' },
  { key: 'textToVideo', icon: PlaySquare, labelKey: 'node.textNode.modes.textToVideo' },
  { key: 'imageToPrompt', icon: ImageIcon, labelKey: 'node.textNode.modes.imageToPrompt' },
  { key: 'textToMusic', icon: Music, labelKey: 'node.textNode.modes.textToMusic' },
  { key: 'textToMusicGen', icon: Music2, labelKey: 'node.textNode.modes.textToMusicGen' },
];

const EDIT_VIEW_ZOOM = 1.4;

export const TextAnnotationNode = memo(({
  id,
  data,
  selected,
  width,
  height,
}: TextAnnotationNodeProps) => {
  const { t } = useTranslation();
  const reactFlow = useReactFlow();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const isBoxSelecting = useIsBoxSelecting();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const deleteEdge = useCanvasStore((state) => state.deleteEdge);
  const addNode = useCanvasStore((state) => state.addNode);
  const addEdge = useCanvasStore((state) => state.addEdge);
  const duplicateNodeAsSibling = useCanvasStore((state) => state.duplicateNodeAsSibling);
  const findNodePosition = useCanvasStore((state) => state.findNodePosition);
  const content = typeof data.content === 'string' ? data.content : '';
  const instruction = typeof data.instruction === 'string' ? data.instruction : '';
  const mode: TextNodeMode = data.mode && REAL_MODES.has(data.mode) ? data.mode : 'writing';
  // 已从能力 picker 选过一次(如「文字生成音乐」)：恒为纯文本编辑区,空内容也不再弹「试试」。
  const pickerDismissed = Boolean(data.pickerDismissed);
  const modelId = typeof data.model === 'string' && data.model.length > 0
    ? data.model
    : DEFAULT_SHARED_MODEL_ID;
  const directModelId = typeof data.model === 'string' ? data.model : '';
  const { models: visionModels } = useDirectModelCatalog('vision');
  const resolvedVisionModelId = resolveDirectCanvasModelId(directModelId, visionModels);
  // 文生视频默认模型取「视频模型接口返回的第一个」，而非文本节点的图像默认 id。
  const { models: videoModels } = useFreezoneVideoModels();
  const reversePromptCost = useGenerationCreditCost(
    mode === 'imageToPrompt' ? 'freezone_image_reverse_prompt' : '',
    null,
    { surface: 'canvas' },
  );
  const { isGenerating, task: generationTask } = useNodeGenerationTaskState(data);
  const {
    cancel: cancelNodeTask,
    isCancelling: isCancellingNodeTask,
    canCancel: canCancelNodeTask,
  } = useCancelNodeGeneration(id, data);
  // referenceOnly: 节点被作为上游引用素材使用（脚本节点 spawn 出来的）。
  // 复用 compact 视图（只渲染编辑卡片），同时 selected ops panel 也不显示。
  const isReferenceOnly = Boolean(data.referenceOnly);
  const isSystemManaged = isSystemManagedNodeData(data);
  const isCompactView = isReferenceOnly || COMPACT_MODES.has(mode);
  const [isEditingContent, setIsEditingContent] = useState(false);
  const editTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const resolvedTitle = resolveNodeDisplayName(CANVAS_NODE_TYPES.textAnnotation, data);
  const minHeightForView = isCompactView ? COMPACT_MIN_HEIGHT : MIN_HEIGHT;
  const defaultHeightForView = isCompactView ? COMPACT_DEFAULT_HEIGHT : DEFAULT_HEIGHT;
  const resolvedWidth = Math.max(MIN_WIDTH, Math.round(width ?? DEFAULT_WIDTH));
  const resolvedHeight = Math.max(minHeightForView, Math.round(height ?? defaultHeightForView));

  // Reactive lookup of the upstream image URL (e.g. uploaded reference image
  // for `imageToPrompt` mode). Re-evaluates whenever edges/nodes change.
  const upstreamImageUrl = useCanvasStore((state) => {
    const edge = state.edges.find((e) => e.target === id);
    if (!edge) return null;
    const node = state.nodes.find((n) => n.id === edge.source);
    return resolveUpstreamImageUrl(node?.data);
  });

  // 取消关联上游参考图：删掉指向本节点的那根连线（缩略图只在该边的源带图时
  // 才显示，所以这里删第一条 target===id 的边即对应当前展示的素材）。
  const handleDetachUpstreamImage = useCallback(() => {
    const state = useCanvasStore.getState();
    const edge = state.edges.find((e) => e.target === id);
    if (edge) {
      deleteEdge(edge.id);
    }
  }, [id, deleteEdge]);

  // libtv-style edit mode: dbl-click the preview card → zoom canvas to 200%
  // centered on this node and focus a fullsize textarea. Escape / blur exits.
  const enterEditMode = useCallback(() => {
    if (isSystemManaged) return;
    const node = reactFlow.getNode(id);
    if (node) {
      const w = (node.measured?.width ?? width ?? DEFAULT_WIDTH);
      const h = (node.measured?.height ?? height ?? DEFAULT_HEIGHT);
      // 组内成员的 node.position 是「相对父组」的坐标，而 setCenter 需要绝对画布坐标。
      // 用 internalNode 的 positionAbsolute（无父组时等于 position），否则双击组里的
      // 节点会把视野按组原点的偏移量平移到别处。
      const absolute =
        reactFlow.getInternalNode(id)?.internals.positionAbsolute ?? node.position;
      const cx = absolute.x + w / 2;
      const cy = absolute.y + h / 2;
      void reactFlow.setCenter(cx, cy, { zoom: EDIT_VIEW_ZOOM, duration: 280 });
    }
    setIsEditingContent(true);
  }, [height, id, isSystemManaged, reactFlow, width]);

  useEffect(() => {
    if (isSystemManaged && isEditingContent) {
      setIsEditingContent(false);
    }
  }, [isEditingContent, isSystemManaged]);

  useEffect(() => {
    if (!isEditingContent) return;
    // Focus on next tick so the textarea exists after the swap.
    const timer = window.setTimeout(() => {
      editTextareaRef.current?.focus();
      const len = editTextareaRef.current?.value.length ?? 0;
      editTextareaRef.current?.setSelectionRange(len, len);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [isEditingContent]);

  const spawnVideoNode = useCallback(() => {
    const position = findNodePosition(id, 580, 680);
    const seedData: Partial<VideoNodeData> = {
      genMode: 'textToVideo',
      prompt: typeof data.content === 'string' ? data.content : '',
    };
    const newNodeId = addNode(CANVAS_NODE_TYPES.video, position, seedData);
    addEdge(id, newNodeId);
    useCanvasStore.getState().autoGroupSpawn(id, [newNodeId], { label: '文生视频组' });
  }, [addEdge, addNode, data.content, findNodePosition, id]);

  const spawnUploadNode = useCallback(() => {
    const sourceNode = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === id);
    const sourceX = sourceNode?.position.x ?? 0;
    const sourceY = sourceNode?.position.y ?? 0;
    const position = {
      x: sourceX - SPAWN_UPLOAD_WIDTH - 60,
      y: sourceY,
    };
    // 反推提示词只能吃图片，限制上传节点仅接受图片类型（标题变「上传图片」、
    // 文件选择器 accept=image/*、拖入视频/音频会被拒）。
    const seedData: Partial<UploadImageNodeData> = { imageOnly: true };
    const newNodeId = addNode(CANVAS_NODE_TYPES.upload, position, seedData);
    addEdge(newNodeId, id);
    useCanvasStore.getState().autoGroupSpawn(id, [newNodeId], { label: '图片反推提示词组' });
  }, [addEdge, addNode, id]);

  // 克隆音频 / 文字生成音乐：在文本节点下游派生一个音频节点并连边（文本 → 音频），
  // 与「文生视频」派生视频节点同构。音频节点默认尺寸 480×180。
  // audioKind 决定下游音频节点走语音克隆(speech) 还是文本生成音乐(music)。
  const spawnAudioNode = useCallback((audioKind: 'speech' | 'music') => {
    const position = findNodePosition(id, 480, 180);
    const newNodeId = addNode(CANVAS_NODE_TYPES.audio, position, { audioKind });
    addEdge(id, newNodeId);
    const label = audioKind === 'music' ? '文字生成音乐组' : '克隆音频组';
    useCanvasStore.getState().autoGroupSpawn(id, [newNodeId], { label });
  }, [addEdge, addNode, findNodePosition, id]);

  const handlePickMode = useCallback((nextMode: TextNodeMode) => {
    if (nextMode === 'writing') {
      updateNodeData(id, { mode: nextMode });
      enterEditMode();
      return;
    }
    // 文字生成音乐：派生下游音乐音频节点后，左侧文本节点回到纯文本编辑态
    // (writing)——音乐描述在这里输入并同步给下游音频节点，不再显示能力 picker。
    if (nextMode === 'textToMusicGen') {
      spawnAudioNode('music');
      // 文本节点回到纯文本编辑态、关闭能力 picker,并预填默认音乐描述(同步给下游音频节点)。
      updateNodeData(id, {
        mode: 'writing',
        pickerDismissed: true,
        content: TEXT_TO_MUSIC_DEFAULT_CONTENT,
      });
      enterEditMode();
      return;
    }
    updateNodeData(id, { mode: nextMode });
    if (nextMode === 'textToVideo') {
      spawnVideoNode();
    } else if (nextMode === 'imageToPrompt') {
      spawnUploadNode();
    } else if (nextMode === 'textToMusic') {
      spawnAudioNode('speech');
      // The actual generation model lives on the spawned audio node. Keep the
      // source text node in a stable editor state instead of reopening the
      // capability picker on every selection.
      updateNodeData(id, { pickerDismissed: true });
      enterEditMode();
    }
  }, [enterEditMode, id, spawnAudioNode, spawnUploadNode, spawnVideoNode, updateNodeData]);

  const runImageToPrompt = useCallback(async () => {
    if (!resolvedVisionModelId) {
      updateNodeData(id, { generationError: '当前节点绑定的视觉模型已失效，请重新选择模型。' });
      return;
    }
    const projectId = readUrl().project;
    if (!projectId) {
      console.error('[text-node] no project in URL');
      return;
    }
    const state = useCanvasStore.getState();
    const upstreamEdge = state.edges.find((edge) => edge.target === id);
    const sourceNode = upstreamEdge
      ? state.nodes.find((node) => node.id === upstreamEdge.source)
      : null;
    const rawUrl = resolveUpstreamImageUrl(sourceNode?.data);
    if (!rawUrl) {
      console.warn('[text-node] imageToPrompt: no upstream image url');
      return;
    }
    updateNodeData(id, { isGenerating: true, generationStartedAt: Date.now() });
    try {
      // Backend looks up the file by static path — `data:` URLs get uploaded
      // first via /freezone/upload to obtain a real path; `?v=<ts>` cache
      // busters are stripped either way.
      const sourceUrl = await ensureBackendImageUrl(projectId, rawUrl);
      const ref = await submitFreezoneReversePrompt(projectId, {
        sourceUrl,
        model: resolvedVisionModelId || undefined,
        canvasId: readUrl().canvas ?? 'default',
        nodeId: id,
      });
      // Persist the task handle so a page refresh can resume this job.
      updateNodeData(id, generationTaskDescriptor(ref));
      await awaitTaskCompletion(ref.task_key, projectId);
      // SSE task.result only carries `{ output_format: "json" }`; the prompt
      // text comes from the dedicated job-result endpoint.
      const { prompt } = await fetchFreezoneReversePromptResult(projectId, ref.job_id);
      if (prompt && prompt.trim().length > 0) {
        updateNodeData(id, { content: prompt, isGenerating: false, generationStartedAt: null });
      } else {
        console.warn('[text-node] reverse-prompt returned empty prompt', { jobId: ref.job_id });
        updateNodeData(id, { isGenerating: false, generationStartedAt: null });
      }
    } catch (error) {
      console.error('[text-node] reverse-prompt failed', error);
      updateNodeData(id, { isGenerating: false, generationStartedAt: null });
    }
  }, [id, resolvedVisionModelId, updateNodeData]);

  const runTextToVideo = useCallback(async () => {
    const promptText = content.trim();
    if (promptText.length === 0) return;
    const projectId = readUrl().project;
    if (!projectId) {
      console.error('[text-node] no project in URL');
      return;
    }
    const state = useCanvasStore.getState();
    const downstreamEdge = state.edges.find((edge) => edge.source === id);
    const targetNode = downstreamEdge
      ? state.nodes.find((node) => node.id === downstreamEdge.target)
      : null;
    if (!targetNode || targetNode.type !== CANVAS_NODE_TYPES.video) {
      console.warn('[text-node] textToVideo: no downstream video node');
      return;
    }
    const videoData = targetNode.data as VideoNodeData;
    const aspectRatio = (videoData.aspectRatio ?? '16:9') as FreezoneVideoAspectRatio;
    const quality: VideoGenQuality = videoData.quality ?? '720P';
    const durationSec = typeof videoData.durationSec === 'number' ? videoData.durationSec : 5;
    const generateAudio = normalizeVideoAudioPreference(
      videoData.generateAudio,
      undefined,
      videoData.generateAudioUserSet,
    );
    const count: VideoGenCount = (videoData.count ?? 1) as VideoGenCount;
    const selectedVideoModel = selectVideoModel(
      videoModels,
      typeof videoData.model === 'string' && videoData.model.length > 0 ? videoData.model : null,
    );
    const videoModel = selectedVideoModel?.apiModel ?? selectedVideoModel?.id ?? DEFAULT_VIDEO_MODEL_ID;
    if (!videoModel) {
      updateNodeData(targetNode.id, {
        isGenerating: false,
        generationError: '尚未配置可用的视频模型，请先在模型中心完成检测。',
      });
      return;
    }

    // 一次可建立最多 12 个视频结果节点；付费请求都走全画布 semaphore，并发上限为 500，
    // 所以这 12 条同时提交——只有上游还没落地时会排队等它（见 generationDependencies）。
    const total = clampGenerationBatchCount(count);
    updateNodeData(targetNode.id, {
      prompt: promptText,
      isGenerating: true,
      generationStartedAt: Date.now(),
      // 目标节点可能带着上次批量生成的画册，本次单条回填后画册与主视频脱钩——清掉。
      generationBatch: null,
      generationQueueTotal: total,
      generationQueueCompleted: 0,
      generationQueueConcurrency: Math.min(GENERATION_CONCURRENCY_DEFAULT, total),
      generationQueuePosition: 1,
    });
    const targetIds: string[] = [targetNode.id];
    for (let i = 1; i < total; i += 1) {
      const siblingId = duplicateNodeAsSibling(targetNode.id, i, {
        prompt: promptText,
        isGenerating: true,
        generationStartedAt: Date.now(),
        count: 1,
        videoUrl: null,
        sourceFileName: null,
        // duplicateNodeAsSibling 整份展开源节点 data，画册字段必须显式清空，
        // 否则兄弟节点会继承源节点的旧画册（卡边 + 徽标显示别人的结果）。
        generationBatch: null,
        generationQueueTotal: total,
        generationQueueCompleted: 0,
        generationQueueConcurrency: Math.min(GENERATION_CONCURRENCY_DEFAULT, total),
        generationQueuePosition: i + 1,
      });
      if (siblingId) targetIds.push(siblingId);
    }
    // 这些视频节点从这一刻就算「在出图」，下游节点按顺序排在它们后面。
    targetIds.forEach(markGenerationIntent);

    const runOne = async (videoNodeId: string) => {
      try {
        const ref = await submitFreezoneVideoGen(projectId, {
          prompt: promptText,
          aspectRatio,
          resolution: qualityToResolution(quality),
          durationSeconds: durationSec,
          generateAudio,
          model: videoModel,
          canvasId: readUrl().canvas ?? 'default',
          nodeId: videoNodeId,
        });
        // Persist the task handle so a page refresh can resume this job.
        updateNodeData(videoNodeId, generationTaskDescriptor(ref));
        const completed = await awaitTaskCompletion(ref.task_key, projectId);
        const url = resolveVideoOutputUrl(completed.result);
        if (url) {
          updateNodeData(videoNodeId, {
            videoUrl: url,
            isGenerating: false,
            generationStartedAt: null,
            sourceFileName: null,
          });
        } else {
          console.warn('[text-node] textToVideo completed without output url', completed);
          updateNodeData(videoNodeId, { isGenerating: false, generationStartedAt: null });
        }
      } catch (error) {
        console.error('[text-node] textToVideo failed', error);
        updateNodeData(videoNodeId, { isGenerating: false, generationStartedAt: null });
      }
    };

    await runGenerationQueue(
      targetIds,
      async (videoNodeId) => withGlobalGenerationSlot(async () => {
        try {
          await runOne(videoNodeId);
        } finally {
          updateNodeData(videoNodeId, {
            generationQueueTotal: null,
            generationQueueCompleted: null,
            generationQueueConcurrency: null,
            generationQueuePosition: null,
          });
        }
      }, undefined, {
        // 每个兄弟视频节点各占一个槽；上游那一侧还在出图时先等它（跟图片节点同一套闸门）。
        nodeId: videoNodeId,
        gate: () => upstreamGenerationGate(videoNodeId),
      }).finally(() => {
        clearGenerationIntent(videoNodeId);
      }),
      GENERATION_CONCURRENCY_DEFAULT,
      (completed) => updateNodeData(targetNode.id, { generationQueueCompleted: completed }),
    );
  }, [content, duplicateNodeAsSibling, id, videoModels, updateNodeData]);

  const textPlaceholder = t('node.textNode.placeholder');
  const hasUserContent = content.trim().length > 0 && content.trim() !== textPlaceholder.trim();

  const handleSubmit = useCallback(() => {
    if (isGenerating) return;
    if (mode === 'imageToPrompt') {
      void runImageToPrompt();
      return;
    }
    if (mode === 'textToVideo') {
      void runTextToVideo();
      return;
    }
  }, [isGenerating, mode, runImageToPrompt, runTextToVideo]);

  const submitDisabled = isGenerating
    || (mode === 'imageToPrompt' && !resolvedVisionModelId)
    || (mode !== 'imageToPrompt' && !hasUserContent);
  // Inner panels stay neutral regardless of selection — the React Flow node
  // wrapper already shows the active state as an outer outline. Doubling it
  // up on the inner cards (libtv-style reference shows neutral borders only)
  // looks busy.
  const inputBodyToneClass = `${CANVAS_NODE_INPUT_SURFACE_CLASS} ${CANVAS_NODE_INPUT_BODY_FRAME_CLASS}`;
  const inputPanelToneClass = `${CANVAS_NODE_INPUT_SURFACE_CLASS} ${CANVAS_NODE_INPUT_FRAME_CLASS}`;

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
        editable={!isSystemManaged}
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <NodeResizeHandle
        minWidth={MIN_WIDTH}
        minHeight={minHeightForView}
        maxWidth={MAX_WIDTH}
        maxHeight={MAX_HEIGHT}
      />

      {!isCompactView && selected && !isBoxSelecting && !isReferenceOnly && !isSystemManaged && !isEditingContent && (
        <WritingOpsPanel
          nodeId={id}
          content={content}
          model={directModelId}
          mode={mode}
          prepareMode={data.textPrepareMode === 'creative' ? 'creative' : 'faithful'}
          preview={data.textPreparePreview ?? null}
          error={typeof data.textPrepareError === 'string' ? data.textPrepareError : null}
          isGenerating={isGenerating}
          width={resolvedWidth}
        />
      )}

      {isCompactView ? (
        <>
          <div
            className={`flex h-full w-full flex-col items-center justify-center rounded-[var(--node-radius)] border transition-colors ${inputBodyToneClass}`}
            onDoubleClick={(event) => {
              if (isEditingContent || isSystemManaged) return;
              event.stopPropagation();
              enterEditMode();
            }}
          >
            {isEditingContent ? (
              <textarea
                ref={editTextareaRef}
                value={content}
                onChange={(event) => updateNodeData(id, { content: event.target.value })}
                onMouseDown={(event) => event.stopPropagation()}
                onClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => {
                  event.stopPropagation();
                  if (event.key === 'Escape') {
                    setIsEditingContent(false);
                    editTextareaRef.current?.blur();
                  }
                }}
                onBlur={() => setIsEditingContent(false)}
                placeholder={textPlaceholder}
                className={`ui-scrollbar nodrag nowheel h-full w-full resize-none border-none bg-transparent px-4 py-4 text-sm leading-6 text-text-dark outline-none ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS}`}
              />
            ) : hasUserContent ? (
              <div className="ui-scrollbar nowheel max-h-full w-full overflow-y-auto px-4 py-4 text-sm leading-6 text-text-dark">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkBreaks]}
                  components={{
                    h1: ({ ...props }) => <h1 className="mb-1 mt-2 text-base font-semibold" {...props} />,
                    h2: ({ ...props }) => <h2 className="mb-1 mt-2 text-sm font-semibold" {...props} />,
                    h3: ({ ...props }) => <h3 className="mb-1 mt-1 text-sm font-semibold" {...props} />,
                    p: ({ ...props }) => <p className="my-1" {...props} />,
                    strong: ({ ...props }) => <strong className="font-semibold text-text-dark" {...props} />,
                    em: ({ ...props }) => <em className="italic" {...props} />,
                    ul: ({ ...props }) => <ul className="my-1 ml-5 list-disc" {...props} />,
                    ol: ({ ...props }) => <ol className="my-1 ml-5 list-decimal" {...props} />,
                    li: ({ ...props }) => <li className="my-0.5" {...props} />,
                    code: ({ ...props }) => (
                      <code className="rounded bg-white/10 px-1 py-0.5 text-xs" {...props} />
                    ),
                    hr: () => <hr className="my-2 border-white/10" />,
                  }}
                >
                  {content}
                </ReactMarkdown>
              </div>
            ) : (
              <AlignJustify className="h-12 w-12 stroke-[1.5] text-text-muted/40" />
            )}
            {isGenerating && (
              <NodeGenerationOverlay
                startedAt={data.generationStartedAt}
                progress={generationTask?.progress ?? null}
                hasBackground={hasUserContent}
                onCancel={canCancelNodeTask ? () => void cancelNodeTask() : undefined}
                cancelPending={isCancellingNodeTask}
              />
            )}
          </div>

          {selected && !isBoxSelecting && !isReferenceOnly && !isSystemManaged && !isEditingContent && (
            <div
              className={`nodrag absolute left-1/2 z-[300] flex -translate-x-1/2 flex-col rounded-[var(--node-radius)] border ${inputPanelToneClass}`}
              style={{
                top: `calc(100% + ${COMPACT_OPS_PANEL_GAP}px)`,
                height: COMPACT_OPS_PANEL_HEIGHT,
                width: Math.max(resolvedWidth, COMPACT_OPS_PANEL_MIN_WIDTH),
              }}
              onClick={(event) => event.stopPropagation()}
            >
              {upstreamImageUrl && (
                <div className="flex shrink-0 items-center px-3 pt-3">
                  <div className="group relative h-9 w-9 overflow-hidden rounded-md border border-white/10">
                    <img
                      src={resolveImageDisplayUrl(upstreamImageUrl)}
                      alt=""
                      className="h-full w-full object-cover"
                      draggable={false}
                    />
                    <button
                      type="button"
                      title="取消引用此素材"
                      className="nodrag absolute right-0 top-0 z-10 hidden h-4 w-4 items-center justify-center rounded-bl-md bg-black/75 text-white transition-colors hover:bg-red-500 group-hover:flex"
                      onMouseDown={(event) => event.stopPropagation()}
                      onClick={(event) => {
                        event.stopPropagation();
                        handleDetachUpstreamImage();
                      }}
                    >
                      <X className="h-3 w-3" strokeWidth={2.5} />
                    </button>
                  </div>
                </div>
              )}
              <textarea
                value={
                  mode === 'imageToPrompt'
                    ? instruction === IMAGE_TO_PROMPT_DEFAULT_CONTENT ? '' : instruction
                    : content
                }
                onChange={(event) => {
                  const field = mode === 'imageToPrompt' ? 'instruction' : 'content';
                  updateNodeData(id, { [field]: event.target.value });
                }}
                onMouseDown={(event) => event.stopPropagation()}
                onClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => event.stopPropagation()}
                placeholder={mode === 'imageToPrompt' ? IMAGE_TO_PROMPT_DEFAULT_CONTENT : textPlaceholder}
                className={`ui-scrollbar nodrag nowheel min-h-0 w-full flex-1 resize-none border-none bg-transparent px-3 pt-3 text-sm leading-6 text-text-dark outline-none ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS}`}
              />
              <div className="flex shrink-0 items-center justify-between gap-2 px-3 py-2">
                {/* 文生视频沿用下游视频节点；反推提示词是视觉任务，直接选择视觉模型。 */}
                {mode === 'textToVideo' ? (
                  <span />
                ) : mode === 'imageToPrompt' ? (
                  <DirectModelPicker
                    kind="vision"
                    value={directModelId}
                    onChange={(nextModelId) => updateNodeData(id, { model: nextModelId })}
                    ariaLabel="反推提示词视觉模型"
                  />
                ) : (
                  <ProviderModelPicker
                    selectedModelId={modelId}
                    onChange={(nextModelId) => updateNodeData(id, { model: nextModelId })}
                    popoverPlacement="top"
                  />
                )}
                <div className="flex items-center gap-1.5">
                  {mode === 'imageToPrompt' && (
                    <CreditCostInline display={reversePromptCost.data?.data.display} />
                  )}
                  <button
                    type="button"
                    disabled={submitDisabled}
                    title={t('node.textNode.submit')}
                    onClick={(event) => {
                      event.stopPropagation();
                      handleSubmit();
                    }}
                    className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
                      submitDisabled
                        ? NODE_GENERATE_BUTTON_DISABLED_CLASS
                        : NODE_GENERATE_BUTTON_ENABLED_CLASS
                    }`}
                  >
                    {isGenerating ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <ArrowUp className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>
            </div>
          )}
        </>
      ) : (
        // 默认视图（writing / textToMusic）：节点本体即文本编辑区——有内容
        // 时直接渲染 markdown，双击进入编辑；空内容时退回到 capability
        // picker（让用户选下一步要做什么 / 切到其它模式）。底部那条原本
        // 的 prompt + send 面板已移除——writing/textToMusic 的 submit
        // 之前就是 stub，留着只会和正文重复编辑同一个 content 字段。
        <div
          className={`flex h-full w-full flex-col rounded-[var(--node-radius)] border transition-colors ${inputBodyToneClass}`}
          onDoubleClick={(event) => {
            if (isEditingContent || isSystemManaged) return;
            event.stopPropagation();
            enterEditMode();
          }}
        >
          {isEditingContent ? (
            <textarea
              ref={editTextareaRef}
              value={content}
              onChange={(event) => updateNodeData(id, { content: event.target.value })}
              onMouseDown={(event) => event.stopPropagation()}
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => {
                event.stopPropagation();
                if (event.key === 'Escape') {
                  setIsEditingContent(false);
                  editTextareaRef.current?.blur();
                }
              }}
              onBlur={() => setIsEditingContent(false)}
              placeholder={textPlaceholder}
              className={`ui-scrollbar nodrag nowheel h-full w-full resize-none border-none bg-transparent px-4 py-4 text-sm leading-6 text-text-dark outline-none ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS}`}
            />
          ) : hasUserContent ? (
            <div className="ui-scrollbar nowheel max-h-full w-full flex-1 overflow-y-auto px-4 py-4 text-sm leading-6 text-text-dark">
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkBreaks]}
                components={{
                  h1: ({ ...props }) => <h1 className="mb-1 mt-2 text-base font-semibold" {...props} />,
                  h2: ({ ...props }) => <h2 className="mb-1 mt-2 text-sm font-semibold" {...props} />,
                  h3: ({ ...props }) => <h3 className="mb-1 mt-1 text-sm font-semibold" {...props} />,
                  p: ({ ...props }) => <p className="my-1" {...props} />,
                  strong: ({ ...props }) => <strong className="font-semibold text-text-dark" {...props} />,
                  em: ({ ...props }) => <em className="italic" {...props} />,
                  ul: ({ ...props }) => <ul className="my-1 ml-5 list-disc" {...props} />,
                  ol: ({ ...props }) => <ol className="my-1 ml-5 list-decimal" {...props} />,
                  li: ({ ...props }) => <li className="my-0.5" {...props} />,
                  code: ({ ...props }) => (
                    <code className="rounded bg-white/10 px-1 py-0.5 text-xs" {...props} />
                  ),
                  hr: () => <hr className="my-2 border-white/10" />,
                }}
              >
                {content}
              </ReactMarkdown>
            </div>
          ) : isSystemManaged ? (
            <div className="flex min-h-0 flex-1 items-center justify-center py-4">
              <AlignJustify className="h-12 w-12 stroke-[1.5] text-text-muted/40" />
            </div>
          ) : pickerDismissed ? (
            // 已选过模式的纯文本节点：空内容时显示占位文案、点击进入编辑，不再显示 picker。
            <div
              className={`flex min-h-0 flex-1 cursor-text items-start px-4 py-4 text-sm leading-6 ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS}`}
              onClick={(event) => {
                event.stopPropagation();
                enterEditMode();
              }}
            >
              {textPlaceholder}
            </div>
          ) : (
            <div
              className="flex min-h-0 flex-1 flex-col justify-center gap-2 py-4"
              style={{ marginInline: PICKER_INSET }}
            >
              <div className="text-xs text-[var(--canvas-node-input-helper)]">{t('node.textNode.tryHint')}</div>
              <div className="flex flex-col gap-0.5">
                {MODES.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        handlePickMode(item.key);
                      }}
                      className="-mx-2 inline-flex items-center gap-3 rounded-lg px-2 py-2 text-sm text-text-dark transition-colors hover:bg-white/[0.08]"
                    >
                      <Icon className="h-4 w-4 text-text-muted/90" />
                      <span>{t(item.labelKey)}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
});

TextAnnotationNode.displayName = 'TextAnnotationNode';

interface WritingOpsPanelProps {
  nodeId: string;
  content: string;
  model: string;
  mode: TextNodeMode;
  prepareMode: TextPrepareMode;
  preview: TextPreparePreview | null;
  error: string | null;
  isGenerating: boolean;
  width: number;
}

function WritingOpsPanel({
  nodeId,
  content,
  model,
  mode,
  prepareMode,
  preview,
  error,
  isGenerating,
  width,
}: WritingOpsPanelProps) {
  const { t } = useTranslation();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const { models: textModels } = useDirectModelCatalog('text');
  const resolvedTextModel = resolveDirectCanvasModelId(model, textModels);
  const [isTranslating, setIsTranslating] = useState(false);
  const [isPreparing, setIsPreparing] = useState(false);
  const textPlaceholder = t('node.textNode.placeholder');
  const previewStale = isTextPreparePreviewStale(preview, content);

  const handleTranslate = useCallback(async () => {
    if (isGenerating || isTranslating) return;
    const trimmed = content.trim();
    if (trimmed.length === 0) return;
    if (!resolvedTextModel) return;
    const project = readUrl().project;
    if (!project) {
      console.error('[text-node] translate: no project in URL');
      return;
    }
    setIsTranslating(true);
    try {
      const ref = await submitFreezoneTextTranslate(project, {
        text: content,
        nodeType: 'text',
        model: resolvedTextModel || undefined,
        canvasId: readUrl().canvas ?? 'default',
        nodeId,
      });
      await awaitTaskCompletion(ref.task_key, project);
      const result = await fetchFreezoneTextTranslateResult(project, ref.job_id);
      updateNodeData(nodeId, { content: result.translated_text });
    } catch (error) {
      console.error('[text-node] translate failed', error);
    } finally {
      setIsTranslating(false);
    }
  }, [content, isGenerating, isTranslating, nodeId, resolvedTextModel, updateNodeData]);

  const handlePrepare = useCallback(async () => {
    if (isGenerating || isPreparing) return;
    const trimmed = content.trim();
    if (trimmed.length === 0) return;
    if (!resolvedTextModel) return;
    const project = readUrl().project;
    if (!project) {
      console.error('[text-node] prepare: no project in URL');
      return;
    }
    setIsPreparing(true);
    updateNodeData(nodeId, { textPrepareError: null });
    try {
      const ref = await submitFreezoneTextPrepare(project, {
        text: content,
        mode: prepareMode,
        model: resolvedTextModel || undefined,
        canvasId: readUrl().canvas ?? 'default',
        nodeId,
      });
      await awaitTaskCompletion(ref.task_key, project);
      const result = await fetchFreezoneTextPrepareResult(project, ref.job_id);
      updateNodeData(nodeId, {
        textPreparePreview: buildTextPreparePreview({
          result,
          jobId: ref.job_id,
          taskKey: ref.task_key,
        }),
        textPrepareError: null,
      });
    } catch (prepareError) {
      console.error('[text-node] prepare failed', prepareError);
      updateNodeData(nodeId, {
        textPrepareError:
          prepareError instanceof Error ? prepareError.message : '故事素材整理失败',
      });
    } finally {
      setIsPreparing(false);
    }
  }, [
    content,
    isGenerating,
    isPreparing,
    nodeId,
    prepareMode,
    resolvedTextModel,
    updateNodeData,
  ]);

  const handleApplyPreparedText = useCallback(() => {
    const patch = buildTextPrepareApplyPatch(preview, content);
    if (!patch) return;
    updateNodeData(nodeId, patch);
  }, [content, nodeId, preview, updateNodeData]);

  const translateDisabled = isGenerating || isTranslating || content.trim().length === 0 || !resolvedTextModel;
  const prepareDisabled =
    mode !== 'writing'
    || isGenerating
    || isPreparing
    || content.trim().length === 0
    || !resolvedTextModel;

  return (
    <div
      className={`nodrag absolute left-1/2 z-[300] flex -translate-x-1/2 flex-col rounded-[var(--node-radius)] border ${CANVAS_NODE_INPUT_SURFACE_CLASS} ${CANVAS_NODE_INPUT_FRAME_CLASS}`}
      style={{
        top: `calc(100% + ${COMPACT_OPS_PANEL_GAP}px)`,
        height: preview ? WRITING_PREVIEW_PANEL_HEIGHT : WRITING_OPS_PANEL_HEIGHT,
        width: Math.max(width, preview ? WRITING_PREVIEW_PANEL_MIN_WIDTH : COMPACT_OPS_PANEL_MIN_WIDTH),
      }}
      onClick={(event) => event.stopPropagation()}
    >
      {preview ? (
        <>
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-white/10 px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <WandSparkles className="h-4 w-4 shrink-0 text-text-muted" />
              <span className="truncate text-xs font-medium text-text-dark">
                {t('node.textNode.prepare.previewTitle')}
              </span>
              {previewStale ? (
                <span className="shrink-0 rounded-full border border-amber-300/25 bg-amber-400/10 px-2 py-0.5 text-[10px] text-amber-100">
                  {t('node.textNode.prepare.stale')}
                </span>
              ) : null}
            </div>
            <button
              type="button"
              title={t('node.textNode.prepare.discard')}
              aria-label={t('node.textNode.prepare.discard')}
              onClick={() => updateNodeData(nodeId, { textPreparePreview: null, textPrepareError: null })}
              className={NODE_INLINE_ICON_BUTTON_CLASS}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="ui-scrollbar nowheel min-h-0 flex-1 overflow-y-auto px-3 py-2 text-xs leading-5 text-text-dark">
            <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-6">
              {preview.preparedText}
            </pre>
            {preview.changeSummary.length > 0 ? (
              <div className="mt-3 border-t border-white/10 pt-2">
                <div className="mb-1 text-[11px] font-medium text-text-muted">
                  {t('node.textNode.prepare.changeSummary')}
                </div>
                <ul className="list-disc space-y-0.5 pl-4">
                  {preview.changeSummary.slice(0, 4).map((item, index) => (
                    <li key={`change-${index}`}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {preview.unresolved.length > 0 ? (
              <div className="mt-3 border-t border-white/10 pt-2">
                <div className="mb-1 text-[11px] font-medium text-amber-100/90">
                  {t('node.textNode.prepare.unresolved')}
                </div>
                <ul className="list-disc space-y-0.5 pl-4">
                  {preview.unresolved.slice(0, 4).map((item, index) => (
                    <li key={`unresolved-${index}`}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {preview.warnings.length > 0 ? (
              <div className="mt-3 border-t border-white/10 pt-2 text-amber-100/90">
                {preview.warnings.slice(0, 2).join('；')}
              </div>
            ) : null}
            {previewStale ? (
              <div className="mt-3 rounded-md border border-amber-300/20 bg-amber-400/10 px-2 py-1.5 text-amber-100">
                {t('node.textNode.prepare.staleHint')}
              </div>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center justify-between gap-2 border-t border-white/10 px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <DirectModelPicker
                kind="text"
                value={model}
                onChange={(nextModelId) => updateNodeData(nodeId, { model: nextModelId })}
                ariaLabel={t('node.textNode.prepare.modelAria')}
              />
              <UiSelect
                value={prepareMode}
                onChange={(event) =>
                  updateNodeData(nodeId, {
                    textPrepareMode: event.target.value as TextPrepareMode,
                  })
                }
                aria-label={t('node.textNode.prepare.modeAria')}
                className="!h-8 !max-w-[92px] !rounded-lg !border-white/[0.1] !bg-white/[0.04] !px-2.5 !text-[12px] !text-text-dark"
                menuClassName="!z-[320] !min-w-[180px] !border-white/10 !bg-[#202024] !text-text-dark"
              >
                <option value="faithful">{t('node.textNode.prepare.mode.faithful')}</option>
                <option value="creative">{t('node.textNode.prepare.mode.creative')}</option>
              </UiSelect>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  void handlePrepare();
                }}
                disabled={prepareDisabled}
                className="nodrag inline-flex h-8 items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.06] px-3 text-xs text-text-dark transition-colors hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-45"
              >
                {isPreparing ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <WandSparkles className="h-4 w-4" />
                )}
                {isPreparing
                  ? t('node.textNode.prepare.running')
                  : t('node.textNode.prepare.regenerate')}
              </button>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  handleApplyPreparedText();
                }}
                disabled={previewStale}
                className="nodrag inline-flex h-8 items-center gap-1.5 rounded-md bg-white px-3 text-xs font-medium text-bg-dark transition-colors hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-45"
              >
                <Check className="h-4 w-4" />
                {t('node.textNode.prepare.apply')}
              </button>
            </div>
          </div>
        </>
      ) : (
        <>
          <textarea
            value={content}
            onChange={(event) => updateNodeData(nodeId, { content: event.target.value })}
            onMouseDown={(event) => event.stopPropagation()}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
            placeholder={textPlaceholder}
            className={`ui-scrollbar nodrag nowheel min-h-0 w-full flex-1 resize-none border-none bg-transparent px-3 pt-3 text-sm leading-6 text-text-dark outline-none ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS}`}
            disabled={isGenerating}
          />
          <div className="flex shrink-0 items-center justify-between gap-2 px-3 py-2">
            {mode === 'writing' ? (
              <DirectModelPicker
                kind="text"
                value={model}
                onChange={(nextModelId) => updateNodeData(nodeId, { model: nextModelId })}
                ariaLabel={t('node.textNode.prepare.modelAria')}
              />
            ) : (
              <span className="max-w-[220px] truncate text-[11px] text-text-muted/70">
                音频模型请在下游音频节点选择
              </span>
            )}
            <div className="flex items-center gap-1">
              {mode === 'writing' ? (
                <>
                  <UiSelect
                    value={prepareMode}
                    onChange={(event) =>
                      updateNodeData(nodeId, {
                        textPrepareMode: event.target.value as TextPrepareMode,
                      })
                    }
                    aria-label={t('node.textNode.prepare.modeAria')}
                    className="!h-7 !max-w-[88px] !rounded-md !border-white/[0.1] !bg-white/[0.04] !px-2 !text-[11px] !text-text-dark"
                    menuClassName="!z-[320] !min-w-[176px] !border-white/10 !bg-[#202024] !text-text-dark"
                  >
                    <option value="faithful">{t('node.textNode.prepare.mode.faithful')}</option>
                    <option value="creative">{t('node.textNode.prepare.mode.creative')}</option>
                  </UiSelect>
                  <button
                    type="button"
                    title={t('node.textNode.prepare.action')}
                    onClick={(event) => {
                      event.stopPropagation();
                      void handlePrepare();
                    }}
                    disabled={prepareDisabled}
                    className="nodrag inline-flex h-7 items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.06] px-2.5 text-[11px] text-text-dark transition-colors hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    {isPreparing ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <WandSparkles className="h-3.5 w-3.5" />
                    )}
                    {t('node.textNode.prepare.action')}
                  </button>
                </>
              ) : null}
              <button
                type="button"
                title={t('node.textNode.translate')}
                onClick={(event) => {
                  event.stopPropagation();
                  void handleTranslate();
                }}
                disabled={translateDisabled}
                className={`${NODE_INLINE_ICON_BUTTON_CLASS} ${
                  isTranslating ? NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS : ''
                }`}
              >
                {isTranslating ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Languages className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>
          {error ? (
            <div className="shrink-0 border-t border-red-300/15 px-3 py-1.5 text-[11px] leading-4 text-red-100">
              {error}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
