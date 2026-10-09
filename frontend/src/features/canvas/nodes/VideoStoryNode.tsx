// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import {
  AlertTriangle,
  Expand,
  FileVideo2,
  LayoutGrid,
  Scissors,
  Video,
  X,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type ScriptImageGenConfig,
  type StoryVideoGenConfig,
  type VideoStoryNodeData,
  type VideoStoryRow,
} from '@/features/canvas/domain/canvasNodes';
import {
  clampStoryboardDurationSeconds,
  parseStoryboardCharactersText,
  storyboardCharactersText,
} from '@/features/canvas/domain/storyboardRowSpec';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { focusDerivedNodes } from '@/features/canvas/application/focusDerivedNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import {
  NodeHeader,
  NODE_HEADER_FLOATING_POSITION_CLASS,
} from '@/features/canvas/ui/NodeHeader';
import { NodeResizeHandle } from '@/features/canvas/ui/NodeResizeHandle';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import { useCancelNodeGeneration } from '@/features/canvas/application/useCancelNodeGeneration';
import { useNodeGenerationTaskState } from '@/features/canvas/application/useNodeGenerationTaskState';
import { useDirectModelCatalog } from '@/features/canvas/hooks/useDirectModelCatalog';
import { upstreamGenerationGate } from '@/features/canvas/application/generationDependencies';
import { formatCreditCost } from '@/components/credits/credit-visual';
import { useGenerationCreditCost } from '@/lib/queries/generation-credit-cost';
import { toast } from 'sonner';
import { EditableTableCell } from '@/features/canvas/ui/EditableTableCell';
import { CANVAS_NODE_PANEL_SURFACE_CLASS, canvasNodeFrameClass } from '@/features/canvas/ui/nodeFrameStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { DEFAULT_SCRIPT_IMAGE_GEN_CONFIG } from './script/generateScriptStoryboard';
import { VideoStoryScatterDialog } from './videoStory/VideoStoryScatterDialog';
import { VideoStoryVideoDialog } from './videoStory/VideoStoryVideoDialog';
import { VideoStoryClipDialog } from './videoStory/VideoStoryClipDialog';
import {
  planVideoStoryScatter,
  scatterVideoStoryShots,
} from './videoStory/scatterVideoStoryShots';
import {
  planVideoStoryShotVideos,
  scatterVideoStoryShotVideos,
} from './videoStory/videoStoryShotVideos';
import {
  applyVideoStoryClipResults,
  failVideoStoryClipNodes,
  planVideoStoryClips,
  scatterVideoStoryClips,
} from './videoStory/videoStoryShotClips';
import { submitFreezoneVideoCut, fetchFreezoneVideoCutResult } from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import { readUrl } from '@/lib/url-params';
import { useFreezoneVideoModels } from '@/features/canvas/hooks/useFreezoneVideoModels';
import { selectVideoModel } from '@/features/canvas/domain/videoModelSelection';
import { normalizeVideoQualityValue } from '@/features/canvas/models/imageCapabilityValues';

type VideoStoryNodeProps = NodeProps & {
  id: string;
  data: VideoStoryNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 720;
const DEFAULT_HEIGHT = 360;
const MIN_WIDTH = 480;
const MIN_HEIGHT = 240;
const MAX_WIDTH = 1600;
const MAX_HEIGHT = 1200;
const VIDEO_STORY_CANCEL_CLEAR_PATCH = {
  isAnalyzing: false,
  analysisStartedAt: null,
  analysisError: null,
} as const;

interface ColumnDef {
  key: keyof VideoStoryRow;
  label: string;
  /** Tailwind min-width class for the table cell. */
  widthClass: string;
  /** True for narrative-style long text columns. */
  wide?: boolean;
}

const COLUMNS: ColumnDef[] = [
  { key: 'shotNumber', label: '镜号', widthClass: 'min-w-[60px]' },
  { key: 'startTime', label: '开始时间', widthClass: 'min-w-[90px]' },
  { key: 'endTime', label: '结束时间', widthClass: 'min-w-[90px]' },
  // 时长列编辑的是数值口径（写入时按 1–15 秒夹取）。解析原文 `duration` 不再单独占一列：
  // 两列「时长」并排会让人不知道该改哪个，而下游下单只认数值这一个。
  { key: 'durationSeconds', label: '时长(秒)', widthClass: 'min-w-[80px]' },
  { key: 'plotDescription', label: '剧情描述', widthClass: 'min-w-[200px]', wide: true },
  { key: 'characters', label: '出场角色', widthClass: 'min-w-[120px]' },
  { key: 'characterAction', label: '角色动作', widthClass: 'min-w-[160px]', wide: true },
  { key: 'emotion', label: '微表情心理', widthClass: 'min-w-[140px]', wide: true },
  { key: 'sceneTags', label: '场景标签', widthClass: 'min-w-[120px]' },
  { key: 'dialogue', label: '台词', widthClass: 'min-w-[200px]', wide: true },
  { key: 'visualDescription', label: '画面描述', widthClass: 'min-w-[220px]', wide: true },
  { key: 'narrative', label: '叙事内容', widthClass: 'min-w-[220px]', wide: true },
  { key: 'shotSize', label: '景别', widthClass: 'min-w-[80px]' },
  { key: 'cameraAngle', label: '摄影机角度', widthClass: 'min-w-[100px]' },
  { key: 'cameraMovement', label: '摄影机运动', widthClass: 'min-w-[120px]' },
  { key: 'focalAndDof', label: '焦距与景深', widthClass: 'min-w-[120px]' },
  { key: 'lighting', label: '光线', widthClass: 'min-w-[120px]' },
  { key: 'backgroundMusic', label: '背景音乐', widthClass: 'min-w-[140px]' },
  { key: 'voiceAndSfx', label: '人声/音效', widthClass: 'min-w-[140px]' },
  { key: 'imagePrompt', label: '图像生成提示词', widthClass: 'min-w-[260px]', wide: true },
  { key: 'videoMotionPrompt', label: '视频运动提示词', widthClass: 'min-w-[240px]', wide: true },
  { key: 'keyframeUrl', label: '关键帧', widthClass: 'min-w-[120px]' },
];

interface StoryCellProps {
  row: VideoStoryRow;
  col: ColumnDef;
  onCommit?: (nextValue: string) => void;
}

function StoryCell({ row, col, onCommit }: StoryCellProps) {
  const value = row[col.key];

  if (col.key === 'keyframeUrl') {
    // 关键帧是图片列，沿用只读 —— 替换需要文件选择器 / URL 输入，是另一
    // 条交互，等用户后续提（同 scriptNode 角色图列的处理）。
    const url = typeof value === 'string' ? value : null;
    if (!url) return <span className="text-text-muted/60">—</span>;
    return (
      <img
        src={resolveImageDisplayUrl(url)}
        alt="keyframe"
        className="h-16 w-auto rounded border border-[rgba(255,255,255,0.08)] object-cover"
        draggable={false}
      />
    );
  }

  const initialText =
    col.key === 'characters'
      ? storyboardCharactersText(row)
      : value == null
        ? ''
        : typeof value === 'string' || typeof value === 'number'
          ? String(value)
          : JSON.stringify(value);

  if (!onCommit) {
    if (initialText.length === 0) {
      return <span className="text-text-muted/60">—</span>;
    }
    return (
      <span className={col.wide ? 'whitespace-pre-wrap' : ''}>{initialText}</span>
    );
  }

  return <EditableTableCell value={initialText} onCommit={onCommit} emptyPlaceholder="—" />;
}

interface StoryTableProps {
  rows: VideoStoryRow[];
  compact?: boolean;
  onCellCommit?: (rowIndex: number, colKey: keyof VideoStoryRow, nextValue: string) => void;
}

function StoryTable({ rows, compact, onCellCommit }: StoryTableProps) {
  return (
    <div className="ui-scrollbar h-full w-full overflow-auto rounded border border-[rgba(255,255,255,0.08)]">
      <table className="min-w-full border-collapse text-left text-[12px] text-text-dark">
        <thead className="sticky top-0 z-10 bg-bg-dark/95 backdrop-blur">
          <tr>
            {COLUMNS.map((col) => (
              <th
                key={col.key as string}
                className={`${col.widthClass} border-b border-[rgba(255,255,255,0.1)] px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-text-muted`}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr
              key={idx}
              className="border-b border-[rgba(255,255,255,0.06)] align-top hover:bg-bg-dark/40"
            >
              {COLUMNS.map((col) => (
                <td
                  key={col.key as string}
                  className={`${col.widthClass} px-3 ${compact ? 'py-2' : 'py-3'} align-top`}
                >
                  <StoryCell
                    row={row}
                    col={col}
                    onCommit={
                      onCellCommit
                        ? (next) => onCellCommit(idx, col.key, next)
                        : undefined
                    }
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EmptyStoryState({ rawResult }: { rawResult?: Record<string, unknown> | null }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="flex max-w-[460px] flex-col items-center gap-3 text-center">
        <div className="text-sm font-medium text-text-dark">未识别出分镜</div>
        <div className="text-[12px] leading-5 text-text-muted/80">
          返回内容中没有可用分镜行。原始返回已保留为辅助信息，可用于排查接口结果。
        </div>
        <details className="w-full rounded-md border border-white/[0.08] bg-bg-dark/45 text-left">
          <summary className="cursor-pointer list-none px-3 py-2 text-[11px] font-medium text-text-dark/82 transition-colors hover:text-text-dark">
            查看原始返回
          </summary>
          <pre className="ui-scrollbar max-h-[120px] overflow-auto border-t border-white/[0.06] p-3 text-[11px] leading-5 text-text-muted/86">
{rawResult ? JSON.stringify(rawResult, null, 2) : '(空)'}
          </pre>
        </details>
      </div>
    </div>
  );
}

function ErrorStoryState({ message }: { message: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="flex max-w-[420px] flex-col items-center gap-3 text-center">
        <AlertTriangle className="h-7 w-7 text-red-300/90" />
        <div className="text-sm font-medium text-red-200">解析失败</div>
        <div className="max-h-[88px] overflow-auto break-words text-[12px] leading-5 text-red-200/82 [overflow-wrap:anywhere]">
          {message}
        </div>
      </div>
    </div>
  );
}

export const VideoStoryNode = memo(({ id, data, width, height }: VideoStoryNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const {
    cancel: cancelNodeTask,
    isCancelling: isCancellingNodeTask,
    canCancel: canCancelNodeTask,
  } = useCancelNodeGeneration(id, data, undefined, {
    clearPatch: VIDEO_STORY_CANCEL_CLEAR_PATCH,
  });

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.videoStory, data),
    [data],
  );
  const resolvedWidth = Math.max(MIN_WIDTH, Math.round(width ?? DEFAULT_WIDTH));
  const resolvedHeight = Math.max(MIN_HEIGHT, Math.round(height ?? DEFAULT_HEIGHT));

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, resolvedHeight, resolvedWidth, updateNodeInternals]);

  // Esc to exit fullscreen.
  useEffect(() => {
    if (!isFullscreen) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsFullscreen(false);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isFullscreen]);

  const cardToneClass = canvasNodeFrameClass();

  const rows = Array.isArray(data.rows) ? data.rows : [];
  // 拉片任务在提交时就写入了 generationTaskDescriptor；刷新后仍能从 task-center
  // 取回真实任务，所以进度条用后端进度续显，而不是从 analysisStartedAt 重估。
  const { task: analysisTask } = useNodeGenerationTaskState(data);
  const isAnalyzing = Boolean(data.isAnalyzing);
  const hasError = Boolean(data.analysisError);
  const hasRows = rows.length > 0;

  // 表格单元格编辑：把编辑后的值写回 data.rows[idx][colKey]。
  // keyframeUrl 列在 StoryCell 里走只读分支，不会调到这里。
  const handleCellCommit = useCallback(
    (rowIndex: number, colKey: keyof VideoStoryRow, nextValue: string) => {
      const existing = rows[rowIndex];
      if (!existing) return;

      // 两列不是纯文本：时长要夹到 1–15 秒存成数值，角色要拆成数组。
      // 夹取只在这里做一次 —— 之后表格展示、下游下单读到的都是同一个合法值。
      let nextCell: string | number | string[] = nextValue;
      if (colKey === 'durationSeconds') {
        const clamped = clampStoryboardDurationSeconds(nextValue);
        if (clamped === null) return; // 输入不是数字：丢弃，保留原值而不是写成 NaN
        nextCell = clamped;
      } else if (colKey === 'characters') {
        nextCell = parseStoryboardCharactersText(nextValue);
      }

      const prevRaw = existing[colKey];
      const prev = Array.isArray(prevRaw) ? prevRaw.join('、') : (prevRaw ?? '');
      if (String(prev) === String(nextCell)) return;

      const nextRows = rows.map((row, index) =>
        index === rowIndex ? { ...row, [colKey]: nextCell } : row,
      );
      updateNodeData(id, { rows: nextRows });
    },
    [id, rows, updateNodeData],
  );

  // ===== 「散开到画布」：逐镜派生成图片节点，组在源节点右侧 =====
  // 与脚本节点的「生成分镜」同一条链路（同一套落位 / 并组 / 出图后自动收拢），只是
  // 事实来源换成了拉片解析出来的分镜表。出图配置写在节点上（对齐 imageGenConfig），
  // 下次打开弹层沿用上次的模型与比例，不用每次重挑。
  const imageGenConfig = (data.imageGenConfig as ScriptImageGenConfig | undefined) ?? {};
  const scatterModel = imageGenConfig.model ?? '';
  const scatterAspect =
    imageGenConfig.aspectRatio ?? DEFAULT_SCRIPT_IMAGE_GEN_CONFIG.aspectRatio ?? '16:9';
  const [isScatterDialogOpen, setIsScatterDialogOpen] = useState(false);
  const [scatterError, setScatterError] = useState<string | null>(null);
  const canvasNodes = useCanvasStore((state) => state.nodes);
  const canvasEdges = useCanvasStore((state) => state.edges);

  // 弹层要显示的张数、按钮文案都按这份账来 —— 与点下去真正做的事共用同一套判定
  // （planVideoStoryScatter / scatterVideoStoryShots 内部同源），避免口径漂移。
  const scatterPlan = useMemo(
    () => planVideoStoryScatter(id),
    // 节点的 rows/imageGenConfig 变了、或画布上派生节点增减了都要重算。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, data, canvasNodes, canvasEdges],
  );
  const scatterPendingCount = scatterPlan.ok ? scatterPlan.pendingCount : 0;
  const scatterHasDerived = scatterPlan.ok && scatterPlan.mode === 'regenerate';

  const { models: imageModels } = useDirectModelCatalog('image');
  const selectedImageModel = useMemo(
    () => imageModels.find((model) => model.catalogId === scatterModel) ?? null,
    [imageModels, scatterModel],
  );
  const scatterCreditCost = useGenerationCreditCost(
    'image_selection',
    selectedImageModel?.catalogId ?? null,
    { surface: 'canvas', params: { size: '1K' }, quantity: Math.max(0, scatterPendingCount) },
  );
  const scatterPriceDisplay = useMemo(() => {
    const total = scatterCreditCost.data?.data.cost;
    return typeof total === 'number' ? formatCreditCost(total) : null;
  }, [scatterCreditCost.data?.data.cost]);

  const handleScatterModelChange = useCallback(
    (next: string) => {
      updateNodeData(id, { imageGenConfig: { ...imageGenConfig, model: next } });
    },
    [id, imageGenConfig, updateNodeData],
  );
  const handleScatterAspectChange = useCallback(
    (next: string) => {
      updateNodeData(id, { imageGenConfig: { ...imageGenConfig, aspectRatio: next } });
    },
    [id, imageGenConfig, updateNodeData],
  );

  const runScatter = useCallback(
    (generateImages: boolean) => {
      const result = scatterVideoStoryShots({
        videoStoryNodeId: id,
        model: scatterModel,
        aspectRatio: scatterAspect,
        generateImages,
      });
      if (!result.ok) {
        setScatterError(result.reason);
        return;
      }
      setScatterError(null);
      setIsScatterDialogOpen(false);
      // 画布开了 onlyRenderVisibleElements：出图是节点自己挂载后自提交的，
      // 不把新节点带进视口，落在屏幕外的那几张就永远不提交。
      focusDerivedNodes(result);
      if (!generateImages) {
        toast.success(`已散出 ${result.nodeIds.length} 个镜头节点`);
        return;
      }
      if (result.mode === 'rearmed') {
        toast.success(
          result.armed > 0 ? `已重新排队 ${result.armed} 张镜头图` : '所有镜头图都已出图',
        );
        return;
      }
      toast.success(`已散出 ${result.nodeIds.length} 个镜头节点，正在出图`);
    },
    [id, scatterModel, scatterAspect],
  );
  const handleScatterConfirm = useCallback(() => runScatter(true), [runScatter]);
  const handleScatterCreateOnly = useCallback(() => runScatter(false), [runScatter]);

  // ===== 「逐镜出视频」：把已经出好图的每一镜派生成视频节点 =====
  // 级联的最后一跳（脚本 → 分镜图 → 批量出视频）。与图片那条路刻意不同：视频节点
  // 不入组（分镜组在领域层是图片专用），首帧靠一条真实的镜头图 → 视频节点血缘边，
  // 所以**必须先有镜头图**才会出现在这里的账上。
  const videoGenConfig = (data.videoGenConfig as StoryVideoGenConfig | undefined) ?? {};
  // 模型解析与视频节点同一套：空绑定落到渠道默认模型（selectVideoModel 的兜底），
  // 派生的节点才会带上一个真正可提交的模型，而不是空字符串。
  const { models: videoModels } = useFreezoneVideoModels();
  const persistedVideoModelId =
    typeof videoGenConfig.model === 'string' && videoGenConfig.model.length > 0
      ? videoGenConfig.model
      : null;
  const selectedVideoModel = useMemo(
    () => selectVideoModel(videoModels, persistedVideoModelId),
    [videoModels, persistedVideoModelId],
  );
  const videoModelId = selectedVideoModel?.id ?? persistedVideoModelId ?? '';
  const videoAspect = videoGenConfig.aspectRatio ?? '16:9';
  const [isVideoDialogOpen, setIsVideoDialogOpen] = useState(false);
  const [videoError, setVideoError] = useState<string | null>(null);

  const videoPlan = useMemo(
    () => planVideoStoryShotVideos(id),
    // 与图片那条路同理：节点 rows/videoGenConfig 变了、画布上派生节点增减了都要重算。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, data, canvasNodes, canvasEdges],
  );
  const videoPendingCount = videoPlan.ok ? videoPlan.pendingCount : 0;
  const videoHasDerived = videoPlan.ok && videoPlan.mode === 'regenerate';
  // 计价：与视频节点同口径（`video_backend` × 分辨率 × 条数 × 时长）。时长不在这里
  // 另算一遍 —— 用的是计划账里那批待提交镜头的秒数合计（与落盘时写进节点的同源）。
  // 分辨率取所选模型声明的默认档，只是估价；真正提交时的档位由视频节点按模型契约收敛。
  const videoEstimateResolution = (
    normalizeVideoQualityValue(String(selectedVideoModel?.parameterDefaults?.resolution ?? ''))
    ?? '720P'
  ).toLowerCase();
  const videoCreditCost = useGenerationCreditCost(
    'video_backend',
    selectedVideoModel?.apiModel ?? null,
    {
      surface: 'canvas',
      params: { resolution: videoEstimateResolution },
      quantity: videoPlan.ok ? videoPlan.plannedSeconds : 0,
    },
  );
  const videoPriceDisplay = useMemo(() => {
    const total = videoCreditCost.data?.data.cost;
    return typeof total === 'number' ? formatCreditCost(total) : null;
  }, [videoCreditCost.data?.data.cost]);

  const handleVideoModelChange = useCallback(
    (next: string) => {
      updateNodeData(id, { videoGenConfig: { ...videoGenConfig, model: next } });
    },
    [id, videoGenConfig, updateNodeData],
  );
  const handleVideoAspectChange = useCallback(
    (next: string) => {
      updateNodeData(id, { videoGenConfig: { ...videoGenConfig, aspectRatio: next } });
    },
    [id, videoGenConfig, updateNodeData],
  );

  const runVideoScatter = useCallback(
    (generateVideos: boolean) => {
      const result = scatterVideoStoryShotVideos({
        videoStoryNodeId: id,
        model: videoModelId,
        aspectRatio: videoAspect,
        generateVideos,
      });
      if (!result.ok) {
        setVideoError(result.reason);
        return;
      }
      setVideoError(null);
      setIsVideoDialogOpen(false);
      // 同图片那条路：画布只渲染视口内的节点，而视频也是节点挂载后才自提交的，
      // 不把整批新节点带进视口，落在屏幕外的那些就永远不会提交。
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
    [id, videoModelId, videoAspect],
  );
  const handleVideoConfirm = useCallback(() => runVideoScatter(true), [runVideoScatter]);
  const handleVideoCreateOnly = useCallback(() => runVideoScatter(false), [runVideoScatter]);

  // ===== 「逐镜切片段」：把源视频按时间码切成一段一段 =====
  // 与出视频那条路的根本区别：这是**纯 ffmpeg**，不打任何模型、不花钱，所以既没有
  // 模型选择也没有计价 —— 有意义的输入只有分镜表里的时间码。结果直接回写到节点上，
  // 因此不打 `canvas_auto_generate_once`（那会触发一次无辜的生成提交）。
  const [isClipDialogOpen, setIsClipDialogOpen] = useState(false);
  const [clipError, setClipError] = useState<string | null>(null);
  const [isClipping, setIsClipping] = useState(false);

  const clipPlan = useMemo(
    () => planVideoStoryClips(id),
    // 与另外两条路同理：节点 rows 变了、画布上派生节点增减了都要重算。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, data, canvasNodes, canvasEdges],
  );
  const clipPendingCount = clipPlan.ok ? clipPlan.pendingCount : 0;
  const clipHasDerived = clipPlan.ok && clipPlan.mode === 'regenerate';
  const clipCanRun = clipPlan.ok && clipPlan.clipCount > 0;

  const handleClipConfirm = useCallback(async () => {
    if (isClipping) return;
    const project = readUrl().project;
    if (!project) {
      setClipError('拿不到项目标识，无法切片段');
      return;
    }
    // 「有顺序的按顺序」同样管这条链路：源视频还在跑上游生成时，此刻从它身上切下来
    // 的会是上一版画面。等它落地再切，而不是切一份马上过期的副本。
    if (upstreamGenerationGate(id) !== 'go') {
      setClipError('上游还在生成，等它落地后再切片段');
      return;
    }

    setIsClipping(true);
    const scattered = scatterVideoStoryClips({ videoStoryNodeId: id });
    if (!scattered.ok) {
      setIsClipping(false);
      setClipError(scattered.reason);
      return;
    }
    setClipError(null);
    setIsClipDialogOpen(false);
    // 节点是普通视频节点、结果靠回写，不需要挂载后自提交；但把整批带进视口能让用户
    // 立刻看到「哪些段已经切好了」。
    focusDerivedNodes({ nodeIds: scattered.plan.map((item) => item.nodeId) });

    const sourceVideoUrl = String(data.sourceVideoUrl ?? '').split('?')[0];
    try {
      const ref = await submitFreezoneVideoCut(project, {
        sourceUrl: sourceVideoUrl,
        segments: scattered.segments,
      });
      const completed = await awaitTaskCompletion(ref.task_key, project);
      if (completed.status === 'failed') {
        throw new Error(completed.error ?? '切片段任务失败');
      }
      const result = await fetchFreezoneVideoCutResult(project, ref.job_id);
      const applied = applyVideoStoryClipResults(scattered.plan, result.segments);
      if (applied === 0) throw new Error('切片段完成但没有拿到可用的片段地址');
      toast.success(`已切出 ${applied} 段片段`);
      focusDerivedNodes({ nodeIds: scattered.plan.map((item) => item.nodeId) });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      failVideoStoryClipNodes(scattered.plan, message);
      setClipError(message);
    } finally {
      setIsClipping(false);
    }
  }, [id, isClipping, data.sourceVideoUrl]);

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
      {/* 逐镜派生出去的血缘边落在这个 handle 上（与脚本节点同款）：只由「散开到
          画布」程序化发起，注册表里两个 connectMenu 标志都关着，不会出现手动连线
          入口 —— 但 handle 本身要有，否则 React Flow 解析不到边的起点。 */}
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
      />

      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<FileVideo2 className="h-4 w-4" />}
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
        <div className="flex items-center justify-between border-b border-[rgba(255,255,255,0.08)] px-3 py-2">
          <div className="flex items-center gap-2 text-[12px] text-text-muted">
            {isAnalyzing ? (
              <span>解析中…</span>
            ) : hasError ? (
              <span className="text-red-300">解析失败</span>
            ) : hasRows ? (
              <span>{rows.length} 条分镜</span>
            ) : (
              <span>未识别出分镜</span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className="inline-flex h-6 items-center gap-1 rounded border border-[rgba(255,255,255,0.18)] bg-bg-dark/60 px-2 text-[11px] text-text-dark hover:border-[rgba(255,255,255,0.32)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-[rgba(255,255,255,0.18)]"
              onClick={(event) => {
                event.stopPropagation();
                setScatterError(null);
                setIsScatterDialogOpen(true);
              }}
              disabled={!hasRows}
              title="把分镜表的每一镜散成图片节点，参考图取该镜关键帧"
            >
              <LayoutGrid className="h-3 w-3" />
              {!scatterHasDerived
                ? '散开到画布'
                : scatterPendingCount > 0
                  ? `重新生成镜头图 ${scatterPendingCount}`
                  : '镜头图已出齐'}
            </button>
            <button
              type="button"
              className="inline-flex h-6 items-center gap-1 rounded border border-[rgba(255,255,255,0.18)] bg-bg-dark/60 px-2 text-[11px] text-text-dark hover:border-[rgba(255,255,255,0.32)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-[rgba(255,255,255,0.18)]"
              onClick={(event) => {
                event.stopPropagation();
                setVideoError(null);
                setIsVideoDialogOpen(true);
              }}
              disabled={!hasRows}
              title="把已经出图的每一镜散成视频节点，首帧取该镜的镜头图"
            >
              <Video className="h-3 w-3" />
              {!videoHasDerived
                ? '逐镜出视频'
                : videoPendingCount > 0
                  ? `重新生成镜头视频 ${videoPendingCount}`
                  : '镜头视频已出齐'}
            </button>
            <button
              type="button"
              className="inline-flex h-6 items-center gap-1 rounded border border-[rgba(255,255,255,0.18)] bg-bg-dark/60 px-2 text-[11px] text-text-dark hover:border-[rgba(255,255,255,0.32)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-[rgba(255,255,255,0.18)]"
              onClick={(event) => {
                event.stopPropagation();
                setClipError(null);
                setIsClipDialogOpen(true);
              }}
              disabled={!hasRows || !clipCanRun || isClipping}
              title="按分镜表的时间码把源视频切成一段一段（纯本地切，不出模型、不花钱）"
            >
              <Scissors className="h-3 w-3" />
              {isClipping
                ? '切片段中…'
                : !clipHasDerived
                  ? '逐镜切片段'
                  : clipPendingCount > 0
                    ? `重新切片段 ${clipPendingCount}`
                    : '片段已切齐'}
            </button>
            <button
              type="button"
              className="inline-flex h-6 items-center gap-1 rounded border border-[rgba(255,255,255,0.18)] bg-bg-dark/60 px-2 text-[11px] text-text-dark hover:border-[rgba(255,255,255,0.32)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-[rgba(255,255,255,0.18)]"
              onClick={(event) => {
                event.stopPropagation();
                setIsFullscreen(true);
              }}
              disabled={!hasRows}
            >
              <Expand className="h-3 w-3" />
              全屏
            </button>
          </div>
        </div>
        {scatterError && (
          <div className="flex items-center gap-2 border-b border-red-500/25 bg-red-500/10 px-3 py-1.5">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-300" />
            <span className="min-w-0 flex-1 truncate text-[11px] leading-5 text-red-200/90">
              {scatterError}
            </span>
          </div>
        )}
        {videoError && (
          <div className="flex items-center gap-2 border-b border-red-500/25 bg-red-500/10 px-3 py-1.5">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-300" />
            <span className="min-w-0 flex-1 truncate text-[11px] leading-5 text-red-200/90">
              {videoError}
            </span>
          </div>
        )}
        {clipError && (
          <div className="flex items-center gap-2 border-b border-red-500/25 bg-red-500/10 px-3 py-1.5">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-300" />
            <span className="min-w-0 flex-1 truncate text-[11px] leading-5 text-red-200/90">
              {clipError}
            </span>
          </div>
        )}
        <div className="flex-1 overflow-hidden p-2">
          {isAnalyzing ? (
            <div className="h-full w-full" />
          ) : hasError ? (
            <ErrorStoryState message={data.analysisError ?? '未知错误'} />
          ) : hasRows ? (
            <StoryTable rows={rows} compact onCellCommit={handleCellCommit} />
          ) : (
            <EmptyStoryState rawResult={data.rawResult ?? null} />
          )}
        </div>

        {isAnalyzing && (
          <NodeGenerationOverlay
            startedAt={data.analysisStartedAt ?? null}
            progress={analysisTask?.progress ?? null}
            hasBackground={false}
            onCancel={canCancelNodeTask ? () => void cancelNodeTask() : undefined}
            cancelPending={isCancellingNodeTask}
          />
        )}
      </div>

      {typeof document !== 'undefined' && isFullscreen && createPortal(
        <div
          className="fixed inset-0 z-[220] flex flex-col bg-black/85 p-6"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="mb-3 flex items-center justify-between text-text-dark">
            <div className="flex items-center gap-3">
              <FileVideo2 className="h-5 w-5" />
              <span className="text-base font-medium">{resolvedTitle}</span>
              <span className="text-sm text-text-muted">共 {rows.length} 条分镜</span>
            </div>
            <button
              type="button"
              className="inline-flex h-8 items-center gap-1 rounded border border-[rgba(255,255,255,0.2)] bg-bg-dark/60 px-3 text-sm text-text-dark hover:border-[rgba(255,255,255,0.36)]"
              onClick={() => setIsFullscreen(false)}
            >
              <X className="h-4 w-4" />
              关闭
            </button>
          </div>
          <div className="flex-1 overflow-hidden rounded-lg border border-[rgba(255,255,255,0.12)] bg-surface-dark/95">
            <StoryTable rows={rows} onCellCommit={handleCellCommit} />
          </div>
        </div>,
        document.body,
      )}

      {/* 「散开到画布」确认弹层：照脚本节点「生成分镜」那一套（先确认模型与比例、
          再决定要不要真出图），文案按「镜头」而不是「分镜图」。 */}
      {typeof document !== 'undefined' &&
        createPortal(
          <VideoStoryScatterDialog
            open={isScatterDialogOpen}
            mode={scatterHasDerived ? 'regenerate' : 'create'}
            shotCount={scatterPlan.ok ? scatterPlan.shotCount : rows.length}
            pendingCount={scatterPendingCount}
            willRebuild={scatterPlan.ok ? scatterPlan.willRebuild : false}
            skippedCount={scatterPlan.ok ? scatterPlan.skipped : 0}
            groupLabel={scatterPlan.ok ? scatterPlan.groupLabel : '镜头表'}
            model={scatterModel}
            aspectKey={scatterAspect}
            priceDisplay={scatterPriceDisplay}
            onModelChange={handleScatterModelChange}
            onAspectChange={handleScatterAspectChange}
            onCancel={() => {
              setScatterError(null);
              setIsScatterDialogOpen(false);
            }}
            onConfirm={handleScatterConfirm}
            onCreateOnly={handleScatterCreateOnly}
          />,
          document.body,
        )}

      {/* 「逐镜出视频」确认弹层：同一套皮的第二跳。时长由分镜行的时间码决定，
          所以这里只有模型与画幅两个可调项（清晰度留给视频节点按模型契约收敛）。 */}
      {typeof document !== 'undefined' &&
        createPortal(
          <VideoStoryVideoDialog
            open={isVideoDialogOpen}
            mode={videoHasDerived ? 'regenerate' : 'create'}
            shotCount={videoPlan.ok ? videoPlan.shotCount : 0}
            pendingCount={videoPendingCount}
            willRebuild={videoPlan.ok ? videoPlan.willRebuild : false}
            skippedNoImage={videoPlan.ok ? videoPlan.skippedNoImage : 0}
            skippedNoPrompt={videoPlan.ok ? videoPlan.skippedNoPrompt : 0}
            model={videoModelId}
            aspectKey={videoAspect}
            priceDisplay={videoPriceDisplay}
            onModelChange={handleVideoModelChange}
            onAspectChange={handleVideoAspectChange}
            onCancel={() => {
              setVideoError(null);
              setIsVideoDialogOpen(false);
            }}
            onConfirm={handleVideoConfirm}
            onCreateOnly={handleVideoCreateOnly}
          />,
          document.body,
        )}

      {/* 「逐镜切片段」确认弹层：无可调项 —— 切片段不打模型、不花钱，唯一有意义的
          输入是分镜表里的时间码，而那正由用户在这张表里编辑。 */}
      {typeof document !== 'undefined' &&
        createPortal(
          <VideoStoryClipDialog
            open={isClipDialogOpen}
            mode={clipHasDerived ? 'regenerate' : 'create'}
            clipCount={clipPlan.ok ? clipPlan.clipCount : 0}
            pendingCount={clipPendingCount}
            willRebuild={clipPlan.ok ? clipPlan.willRebuild : false}
            skippedNoRange={clipPlan.ok ? clipPlan.skippedNoRange : 0}
            plannedSeconds={clipPlan.ok ? clipPlan.plannedSeconds : 0}
            busy={isClipping}
            onCancel={() => {
              setClipError(null);
              setIsClipDialogOpen(false);
            }}
            onConfirm={() => void handleClipConfirm()}
          />,
          document.body,
        )}
    </div>
  );
});

VideoStoryNode.displayName = 'VideoStoryNode';
