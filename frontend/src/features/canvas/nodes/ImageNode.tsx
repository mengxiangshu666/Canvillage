// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Handle,
  Position,
  useStore,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import { Image as ImageIcon, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  CANVAS_NODE_TYPES,
  DEFAULT_ASPECT_RATIO,
  EXPORT_RESULT_NODE_MIN_WIDTH,
  EXPORT_RESULT_NODE_MIN_HEIGHT,
  EXPORT_RESULT_NODE_RESIZE_MIN_EDGE,
  type CanvasNodeType,
  type ExportImageNodeData,
  type ImageEditNodeData,
} from '@/features/canvas/domain/canvasNodes';
import {
  aspectRatioFromImageDimensions,
  resolveMinEdgeFittedSize,
  resolveResizeMinConstraintsByAspect,
  shouldForceNaturalImageSize,
} from '@/features/canvas/application/imageNodeSizing';
import {
  resolveImageDisplayUrl,
  shouldUseOriginalImageByZoom,
  withImageCacheBust,
} from '@/features/canvas/application/imageData';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import { NodeResizeHandle } from '@/features/canvas/ui/NodeResizeHandle';
import { CanvasNodeImage } from '@/features/canvas/ui/CanvasNodeImage';
import { DirectorControlBundleBadge } from '@/features/canvas/ui/DirectorControlBundleBadge';
import { NodeRightsBadge } from '@/features/canvas/ui/NodeRightsBadge';
import { CANVAS_NODE_PANEL_SURFACE_CLASS, canvasNodeFrameClass } from '@/features/canvas/ui/nodeFrameStyles';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import { NodeGenerationErrorCard } from '@/features/canvas/ui/NodeGenerationErrorCard';
import {
  CandidateBindingBadges,
  hasMainlineContexts,
} from '@/features/freezone/context/NodeContextBadges';
import { collectCandidateBindingsForNode } from '@/features/freezone/context/mainlineContext';
import {
  canRegenerateExportImageNode,
  regenerateExportImageNode,
} from '@/features/canvas/application/regenerateExportNode';
import { useNodeGenerationTaskState } from '@/features/canvas/application/useNodeGenerationTaskState';
import { mirrorResultImageToOrigin } from '@/features/canvas/application/mirrorResultToOrigin';
import { useCancelNodeGeneration } from '@/features/canvas/application/useCancelNodeGeneration';
import { selectConnectedCanvasEdges } from '@/features/canvas/application/canvasEdgeIndex';
import { useCanvasStore } from '@/stores/canvasStore';
import { fetchFreezoneJobResultWithRetry } from '@/api/ops';
import {
  buildImageGenerationSuccessPatch,
  CLEARED_GENERATION_ERROR_PATCH,
} from '@/features/canvas/application/generationTaskArbitration';
import { buildGeneratedRightsPatch } from '@/features/canvas/domain/nodeRights';
import { backendErrorToastMessage } from '@/lib/api-errors';
import { readUrl } from '@/lib/url-params';
import { toast } from 'sonner';
import { areCanvasNodePropsEqual } from './videoNodeRenderProps';

type ImageNodeProps = NodeProps & {
  id: string;
  data: ImageEditNodeData | ExportImageNodeData;
  selected?: boolean;
};

function resolveNodeDimension(value: number | undefined, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value) && value > 1) {
    return Math.round(value);
  }
  return fallback;
}

export const ImageNode = memo(({ id, data, selected, type, width, height }: ImageNodeProps) => {
  const { t } = useTranslation();
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const updateNodeSize = useCanvasStore((state) => state.updateNodeSize);
  const {
    cancel: cancelNodeTask,
    isCancelling: isCancellingNodeTask,
    canCancel: canCancelNodeTask,
  } = useCancelNodeGeneration(id, data);
  const connectedEdges = useCanvasStore((state) =>
    selectConnectedCanvasEdges(state.edges, id),
  );
  // 只订阅「是否该用原图」这个离散布尔(在 zoom 阈值处翻转),而非连续 zoom 值 ——
  // 缩放过程中本节点只在跨过阈值的那一帧重渲染,而非每一帧。
  const preferOriginalImage = useStore((state) => shouldUseOriginalImageByZoom(state.transform[2]));
  const [now, setNow] = useState(() => Date.now());
  const [isRecovering, setIsRecovering] = useState(false);
  const isExportResultNode = type === CANVAS_NODE_TYPES.exportImage;
  // `task` 是 task-center 里那条真实任务：刷新后靠 generationTaskKey 重新接上，
  // 因此进度条在重载后仍从后端进度续显，而不是归零重估。
  const { isGenerating, task: generationTask } = useNodeGenerationTaskState(data);
  const generationError =
    typeof (data as { generationError?: unknown }).generationError === 'string'
      ? ((data as { generationError?: string }).generationError ?? '').trim()
      : '';
  const hasGenerationError =
    isExportResultNode && !isGenerating && !data.imageUrl && generationError.length > 0;
  const generationErrorRequestId =
    typeof (data as { generationErrorRequestId?: unknown }).generationErrorRequestId === 'string' &&
    (data as { generationErrorRequestId?: string }).generationErrorRequestId
      ? (data as { generationErrorRequestId?: string }).generationErrorRequestId ?? ''
      : '';
  const generationErrorDetails =
    typeof (data as { generationErrorDetails?: unknown }).generationErrorDetails === 'string'
      ? (data as { generationErrorDetails?: string }).generationErrorDetails ?? ''
      : '';
  const generationErrorStage =
    typeof (data as { generationErrorStage?: unknown }).generationErrorStage === 'string'
      ? (data as { generationErrorStage?: string }).generationErrorStage ?? ''
      : '';
  const generationErrorSuggestedAction =
    typeof (data as { generationErrorSuggestedAction?: unknown }).generationErrorSuggestedAction === 'string'
      ? (data as { generationErrorSuggestedAction?: string }).generationErrorSuggestedAction ?? ''
      : '';
  const generationRecoveryJobId =
    typeof (data as { generationRecoveryJobId?: unknown }).generationRecoveryJobId === 'string'
      ? ((data as { generationRecoveryJobId?: string }).generationRecoveryJobId ?? '').trim()
      : typeof (data as { generationJobId?: unknown }).generationJobId === 'string'
        ? ((data as { generationJobId?: string }).generationJobId ?? '').trim()
        : '';
  const generationRecoveryTaskType =
    (data as { generationRecoveryTaskType?: unknown }).generationRecoveryTaskType === 'freezone_edit'
      ? 'freezone_edit'
      : (data as { generationRecoveryTaskType?: unknown }).generationRecoveryTaskType === 'freezone_gen'
        ? 'freezone_gen'
        : null;
  const canRecoverImageResult = Boolean(
    isExportResultNode
    && generationRecoveryJobId
    && generationRecoveryTaskType,
  );
  const generationStartedAt =
    typeof data.generationStartedAt === 'number' ? data.generationStartedAt : null;
  const resolvedAspectRatio = data.aspectRatio || DEFAULT_ASPECT_RATIO;
  const compactSize = resolveMinEdgeFittedSize(resolvedAspectRatio, {
    minWidth: EXPORT_RESULT_NODE_MIN_WIDTH,
    minHeight: EXPORT_RESULT_NODE_MIN_HEIGHT,
  });
  const resizeConstraints = resolveResizeMinConstraintsByAspect(resolvedAspectRatio, {
    minWidth: EXPORT_RESULT_NODE_RESIZE_MIN_EDGE,
    minHeight: EXPORT_RESULT_NODE_RESIZE_MIN_EDGE,
  });
  const resizeMinWidth = resizeConstraints.minWidth;
  const resizeMinHeight = resizeConstraints.minHeight;
  const resolvedWidth = resolveNodeDimension(width, compactSize.width);
  const resolvedHeight = resolveNodeDimension(height, compactSize.height);
  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(type as CanvasNodeType, data),
    [data, type]
  );
  const hasMainlineContext = hasMainlineContexts(
    (data as { mainline_context?: unknown }).mainline_context,
  );
  const candidateBindingRoles = useMemo(
    () => collectCandidateBindingsForNode(connectedEdges, id).map((binding) => binding.role),
    [connectedEdges, id],
  );

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, resolvedHeight, resolvedWidth, updateNodeInternals]);

  useEffect(() => {
    if (!isGenerating) {
      return;
    }

    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 120);

    return () => {
      window.clearInterval(timer);
    };
  }, [isGenerating]);

  const waitedMinutes = useMemo(() => {
    if (!isGenerating || generationStartedAt === null) {
      return 0;
    }

    const elapsed = Math.max(0, now - generationStartedAt);
    return Math.floor(elapsed / 60000);
  }, [generationStartedAt, isGenerating, now]);

  const waitingResultText = useMemo(() => {
    if (!isExportResultNode) {
      return t('node.imageNode.selectToEdit');
    }

    if (!isGenerating || waitedMinutes < 2) {
      return t('node.imageNode.waitingResult');
    }

    return t('node.imageNode.waitingResultDelayed', { minutes: waitedMinutes });
  }, [isExportResultNode, isGenerating, t, waitedMinutes]);

  const imageSource = useMemo(() => {
    const picked = preferOriginalImage
      ? data.imageUrl || data.previewImageUrl
      : data.previewImageUrl || data.imageUrl;
    return picked
      ? resolveImageDisplayUrl(withImageCacheBust(picked, (data as { committed_at?: unknown }).committed_at as string | undefined))
      : null;
  }, [data, data.imageUrl, data.previewImageUrl, preferOriginalImage]);

  // 获取原图 URL 用于查看器
  const originalImageUrl = useMemo(() => {
    if (!data.imageUrl) return null;
    return resolveImageDisplayUrl(data.imageUrl);
  }, [data.imageUrl]);

  const handleRecoverImageResult = useCallback(async () => {
    if (isRecovering || !generationRecoveryTaskType || !generationRecoveryJobId) return;
    const projectId = readUrl().project;
    if (!projectId) {
      toast.error('当前画布缺少项目标识');
      return;
    }

    setIsRecovering(true);
    try {
      const result = await fetchFreezoneJobResultWithRetry(
        projectId,
        generationRecoveryTaskType,
        generationRecoveryJobId,
        { attempts: 30, delayMs: 2_000 },
      );
      const url = typeof result.url === 'string' ? result.url.trim() : '';
      if (!url) throw new Error('重新获取完成但没有返回图片地址');

      updateNodeData(id, {
        ...buildImageGenerationSuccessPatch(url),
        ...buildGeneratedRightsPatch(data),
        generationJobId: null,
        generationProviderId: null,
        generationClientSessionId: null,
        generationErrorStage: null,
        generationErrorSuggestedAction: null,
        generationErrorRetryable: null,
        generationRecoveryJobId: null,
        generationRecoveryTaskType: null,
      });
      // 结果重新取回后同样要回写入口节点（改图 / 分镜生成），否则下游仍读空。
      mirrorResultImageToOrigin(id);
      toast.success('已重新获取上游图片结果');
    } catch (error) {
      const message = backendErrorToastMessage(error, t);
      updateNodeData(id, {
        isGenerating: false,
        generationStartedAt: null,
        generationJobId: null,
        generationError: message,
        generationErrorDetails: error instanceof Error ? error.message : String(error),
        generationErrorStage: 'query',
        generationErrorSuggestedAction: '上游结果尚未落盘，可稍后再次点击重新获取。',
        generationErrorRetryable: true,
        generationRecoveryJobId,
        generationRecoveryTaskType,
      });
      toast.error('重新获取图片失败，可稍后再次尝试');
    } finally {
      setIsRecovering(false);
    }
  }, [
    generationRecoveryJobId,
    generationRecoveryTaskType,
    id,
    isRecovering,
    t,
    updateNodeData,
  ]);

  // Natural pixel size of the displayed image, mirrored from data when present
  // (persisted by the onLoad handler below) and refreshed on every <img> load so
  // the resolution badge shows even for nodes whose size already matched (those
  // skip the persist branch). Drives a top-right resolution chip like the video node.
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(() => {
    const w = (data as { imageNaturalWidth?: unknown }).imageNaturalWidth;
    const h = (data as { imageNaturalHeight?: unknown }).imageNaturalHeight;
    return typeof w === 'number' && typeof h === 'number' && w > 0 && h > 0
      ? { width: w, height: h }
      : null;
  });

  return (
    <div
      className={`
        group relative overflow-visible rounded-[var(--node-radius)] border ${CANVAS_NODE_PANEL_SURFACE_CLASS} p-0 transition-colors duration-150
        ${hasGenerationError
          ? (selected
            ? 'border-red-400 shadow-[0_0_0_1px_rgba(248,113,113,0.42)]'
            : 'border-red-500/70 bg-[rgba(127,29,29,0.12)] hover:border-red-400/80 dark:border-red-500/70 dark:hover:border-red-400/80')
          : canvasNodeFrameClass({ mainline: hasMainlineContext })}
      `}
      style={{ width: resolvedWidth, height: resolvedHeight }}
      onClick={() => setSelectedNode(id)}
    >
      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={isExportResultNode
          ? <ImageIcon className="h-4 w-4" />
          : <Sparkles className="h-4 w-4" />}
        titleText={resolvedTitle}
        titleClassName="inline-block max-w-[220px] truncate whitespace-nowrap align-bottom"
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />
      <CandidateBindingBadges roles={candidateBindingRoles} />
      <NodeRightsBadge nodeId={id} data={data as Record<string, unknown>} />

      {data.imageUrl && naturalSize ? (
        <div
          className="absolute -top-7 right-1 z-20 flex items-center gap-1 rounded-md border border-white/10 bg-black/55 px-2 py-0.5 text-[11px] font-medium tabular-nums text-white/70 backdrop-blur-sm"
          title={t('node.imageNode.resolution')}
        >
          <ImageIcon className="h-3 w-3 text-white/45" />
          {naturalSize.width}×{naturalSize.height}
        </div>
      ) : null}

      <div
        className={`relative h-full w-full overflow-hidden rounded-[var(--node-radius)] ${hasGenerationError ? 'bg-[rgba(127,29,29,0.2)]' : 'bg-bg-dark'}`}
      >
        <DirectorControlBundleBadge bundle={(data as { director_control_bundle?: unknown }).director_control_bundle} />
        {data.imageUrl ? (
          <CanvasNodeImage
            src={imageSource ?? ''}
            alt={isExportResultNode ? t('node.imageNode.resultAlt') : t('node.imageNode.generatedAlt')}
            viewerSourceUrl={originalImageUrl}
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
                minWidth: EXPORT_RESULT_NODE_MIN_WIDTH,
                minHeight: EXPORT_RESULT_NODE_MIN_HEIGHT,
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
        ) : isGenerating ? (
          <div className="h-full w-full" />
        ) : hasGenerationError ? (
          <NodeGenerationErrorCard
            title={t('node.imageNode.generationFailed')}
            message={generationError}
            details={generationErrorDetails}
            requestId={generationErrorRequestId}
            stage={generationErrorStage}
            suggestedAction={generationErrorSuggestedAction}
            recoverBusy={isRecovering}
            onRecover={canRecoverImageResult ? () => void handleRecoverImageResult() : undefined}
            retryBusy={isGenerating}
            onRetry={canRegenerateExportImageNode(data as Record<string, unknown>, id)
              ? () => void regenerateExportImageNode(id)
              : undefined}
            onDismiss={() => updateNodeData(id, { ...CLEARED_GENERATION_ERROR_PATCH })}
          />
        ) : isGenerating ? (
          // 生成中只保留 NodeGenerationOverlay 的「蒙层 + 进度」，不再渲染占位
          // 图标 / 等待文案，避免它们透出在百分比下方重叠成杂乱内容。
          <div className="h-full w-full" />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/85">
            {isExportResultNode ? (
              <ImageIcon className="h-7 w-7 opacity-60" />
            ) : (
              <Sparkles className="h-7 w-7 opacity-60" />
            )}
            <span className="px-4 text-center text-[12px] leading-6">
              {waitingResultText}
            </span>
          </div>
        )}

        {isGenerating && (
          <NodeGenerationOverlay
            startedAt={generationStartedAt}
            progress={generationTask?.progress ?? null}
            hasBackground={Boolean(data.imageUrl)}
            onCancel={canCancelNodeTask ? () => void cancelNodeTask() : undefined}
            cancelPending={isCancellingNodeTask}
          />
        )}
      </div>

      <Handle
        type="target"
        id="target"
        position={Position.Left}
        className="!h-2 !w-2 !border-surface-dark !bg-[rgb(148,163,184)]"
      />
      <Handle
        type="source"
        id="source"
        position={Position.Right}
        className="!h-2 !w-2 !border-surface-dark !bg-[rgb(148,163,184)]"
      />
      <NodeResizeHandle
        minWidth={resizeMinWidth}
        minHeight={resizeMinHeight}
        maxWidth={1600}
        maxHeight={1600}
        keepAspectRatio
      />
    </div>
  );
}, areCanvasNodePropsEqual);

ImageNode.displayName = 'ImageNode';
