// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useMemo, useState } from 'react';
import { NodeToolbar as ReactFlowNodeToolbar } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Combine,
  Crop,
  Download,
  Grid2x2,
  Hash,
  Layers,
  Loader2,
  RefreshCw,
  Unlink2,
} from 'lucide-react';

import { UiChipButton, UiPanel } from '@/components/ui';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/shadcn/dropdown-menu';
import { useCanvasStore } from '@/stores/canvasStore';
import type { CanvasNode, GroupNodeData } from '@/features/canvas/domain/canvasNodes';
import { resolveNodeSourceImageUrl } from '@/features/canvas/domain/canvasNodes';
import { mergeStoryboardImages } from '@/commands/image';
import { reduceAspectRatio } from '@/features/canvas/application/imageData';
import { uploadLocalImageToBackend } from '@/features/canvas/application/uploadToolOutput';
import {
  DEFAULT_STORYBOARD_ASPECT,
  STORYBOARD_ASPECTS,
  resolveStoryboardGrid,
} from '@/features/canvas/domain/storyboardGroup';
import {
  NODE_TOOLBAR_ALIGN,
  NODE_TOOLBAR_CLASS,
  NODE_TOOLBAR_OFFSET,
  NODE_TOOLBAR_POSITION,
} from '@/features/canvas/ui/nodeToolbarConfig';
import { ZoomScaledToolbar } from '@/features/canvas/ui/ZoomScaledToolbar';
import {
  regenerateStoryboardGroupImages,
  storyboardGroupMembers,
  storyboardMemberIdsToRearm,
} from '@/features/canvas/nodes/script/generateScriptStoryboard';
import {
  scriptRowsOf,
  storyboardStalenessForGroup,
} from '@/features/canvas/nodes/script/scriptStaleness';
import { scriptNodeIdForGroup } from '@/features/canvas/nodes/script/storyboardSettle';
import {
  regenerateVideoStoryShotGroup,
  videoStoryGroupPendingCount,
  videoStoryOwnerNodeIdForGroup,
} from '@/features/canvas/nodes/videoStory/scatterVideoStoryShots';
import { downloadStoryboardGroupImages } from '@/features/canvas/nodes/script/storyboardDownload';

const PANEL_CLASS =
  // 与 NodeActionToolbar 一致的入场动画：激活时从节点上沿淡入+轻微上滑浮现。
  'flex animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-2 items-center gap-1.5 rounded-[18px] !border-white/10 !bg-[#242426]/95 px-2 py-1.5 text-sm shadow-[0_10px_24px_rgba(0,0,0,0.28)] backdrop-blur-2xl duration-200 ease-out motion-reduce:animate-none [&_svg]:h-4 [&_svg]:w-4';
const CHIP_CLASS =
  'h-9 gap-1.5 rounded-[12px] !border-transparent !bg-transparent px-3 text-sm text-text-dark hover:!bg-[rgba(255,255,255,0.075)] focus:!border-transparent focus:!shadow-none focus-visible:!ring-0';
const MENU_CONTENT_CLASS =
  'z-[120] min-w-[120px] border-white/10 bg-[#242426]/95 text-text-dark shadow-none backdrop-blur-3xl';
const MENU_ITEM_CLASS =
  'gap-2 rounded-[10px] text-text-dark focus:bg-[rgba(255,255,255,0.075)] focus:text-text-dark';

// Max columns offered in the 宫格 picker — beyond this the cells get too small.
const MAX_GRID_COLS = 6;

interface StoryboardGroupToolbarProps {
  node: CanvasNode;
}

/**
 * Top toolbar shown when a single 分镜组 (storyboard group) node is selected.
 * Provides cell aspect, grid columns, index toggle, real stitched export,
 * convert-to-plain, and ungroup. Rendered in place of the generic
 * NodeActionToolbar (which early-returns to this for storyboard groups).
 */
export const StoryboardGroupToolbar = memo(({ node }: StoryboardGroupToolbarProps) => {
  const { t } = useTranslation();
  const data = node.data as GroupNodeData;
  const setStoryboardGroupConfig = useCanvasStore((state) => state.setStoryboardGroupConfig);
  const convertStoryboardGroupToPlain = useCanvasStore(
    (state) => state.convertStoryboardGroupToPlain
  );
  const ungroupNode = useCanvasStore((state) => state.ungroupNode);
  const requestFocusNode = useCanvasStore((state) => state.requestFocusNode);
  const nodes = useCanvasStore((state) => state.nodes);
  const addDerivedExportNode = useCanvasStore((state) => state.addDerivedExportNode);
  const addEdge = useCanvasStore((state) => state.addEdge);
  const [isStitching, setIsStitching] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const childNodes = useMemo(
    () => nodes
      .filter((candidate) => candidate.parentId === node.id)
      .sort((left, right) => left.position.y - right.position.y || left.position.x - right.position.x),
    [node.id, nodes],
  );
  const childCount = childNodes.length;
  // 分镜组自身拿不到脚本行，要靠反查脚本节点（见 scriptNodeIdForGroup）。
  const scriptNodeId = useMemo(() => scriptNodeIdForGroup(node.id), [node.id, nodes]);
  // 另一种来源：视频故事节点「散开到画布」派生的镜头组。它没有脚本节点，判定
  // 「哪几张还要重跑」要看分镜表的当前行 —— 两条路各查各的源。
  const videoStoryOwnerId = useMemo(
    () => videoStoryOwnerNodeIdForGroup(node.id),
    [node.id, nodes],
  );
  const groupMemberIds = useMemo(
    () => storyboardGroupMembers(node.id).map((member) => member.id),
    // nodes 变了成员集合就可能变（出图 / 解组都会动它）。
    [node.id, nodes],
  );
  // 重新生成只针对「还没出图 / 上次失败 / 脚本行已变而失效」的成员 —— 已经出好的图
  // 不会被白跑（真出图有成本）；但脚本改过的那几张必须算进来，否则闸门形同虚设。
  const regeneratableCount = useMemo(() => {
    if (videoStoryOwnerId) return videoStoryGroupPendingCount(node.id);
    const rows = scriptNodeId ? scriptRowsOf(scriptNodeId) : [];
    return storyboardMemberIdsToRearm(groupMemberIds, rows).length;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoStoryOwnerId, scriptNodeId, groupMemberIds, nodes]);
  // 「脚本已变更」徽标：这几张图与当前脚本行对不上了（对齐 LibTV 的级联规则）。
  const staleCount = useMemo(() => {
    const staleness = storyboardStalenessForGroup(node.id, scriptNodeId);
    return staleness.staleNodeIds.length;
  }, [node.id, scriptNodeId, nodes]);

  const aspectKey = data.storyboardAspect ?? DEFAULT_STORYBOARD_ASPECT;
  const currentGrid = resolveStoryboardGrid(childCount, {
    cols: data.storyboardCols,
    rows: data.storyboardRows,
  });
  const currentCols = currentGrid.cols;
  const currentRows = currentGrid.rows;
  const showIndex = data.storyboardShowIndex === true;

  const colOptions = useMemo(() => {
    const max = Math.max(1, Math.min(childCount, MAX_GRID_COLS));
    return Array.from({ length: max }, (_, index) => index + 1);
  }, [childCount]);

  const rowOptions = useMemo(() => {
    const max = Math.max(1, Math.min(childCount, MAX_GRID_COLS));
    return Array.from({ length: max }, (_, index) => index + 1);
  }, [childCount]);

  const handleStitch = useCallback(async () => {
    if (isStitching) return;
    const frameSources = childNodes
      .map((child) => resolveNodeSourceImageUrl(child))
      .filter((source): source is string => Boolean(source));
    if (frameSources.length === 0) {
      toast.error(t('canvas.storyboardGroup.stitchEmpty'));
      return;
    }

    setIsStitching(true);
    let temporaryImageUrl: string | null = null;
    let keepTemporaryImageUrl = false;
    try {
      const cols = Math.max(1, Math.min(currentCols, frameSources.length));
      const rows = Math.ceil(frameSources.length / cols);
      const merged = await mergeStoryboardImages({
        frameSources,
        rows,
        cols,
        cellGap: 2,
        outerPadding: 2,
        noteHeight: 0,
        fontSize: 16,
        backgroundColor: data.backgroundColor || '#111113',
        maxDimension: 4096,
        showFrameIndex: showIndex,
        showFrameNote: false,
        imageFit: 'cover',
        textColor: '#ffffff',
      });
      temporaryImageUrl = merged.imagePath;
      const uploadedUrl = await uploadLocalImageToBackend(
        merged.imagePath,
        `storyboard-group-${node.id}-${Date.now()}.png`,
      );
      const aspectRatio = reduceAspectRatio(merged.canvasWidth, merged.canvasHeight);
      const outputNodeId = addDerivedExportNode(
        node.id,
        uploadedUrl,
        aspectRatio,
        uploadedUrl,
        {
          defaultTitle: t('canvas.storyboardGroup.stitchedTitle'),
          resultKind: 'generic',
          sizeStrategy: 'autoMinEdge',
        },
      );
      if (!outputNodeId) {
        throw new Error('failed to create stitched output node');
      }
      keepTemporaryImageUrl = uploadedUrl === merged.imagePath;
      addEdge(node.id, outputNodeId);
      toast.success(t('canvas.storyboardGroup.stitchSuccess', { count: frameSources.length }));
    } catch (error) {
      console.error('[storyboard-group] stitch failed', error);
      toast.error(t('canvas.storyboardGroup.stitchFailed'));
    } finally {
      if (!keepTemporaryImageUrl && temporaryImageUrl?.startsWith('blob:')) {
        URL.revokeObjectURL(temporaryImageUrl);
      }
      setIsStitching(false);
    }
  }, [
    addDerivedExportNode,
    addEdge,
    childNodes,
    currentCols,
    data.backgroundColor,
    isStitching,
    node.id,
    showIndex,
    t,
  ]);

  const handleBatchDownload = useCallback(async () => {
    if (isDownloading) return;
    setIsDownloading(true);
    try {
      const { total, downloaded } = await downloadStoryboardGroupImages(node.id);
      if (total === 0) {
        toast.error(t('canvas.storyboardGroup.batchDownloadEmpty'));
        return;
      }
      if (downloaded < total) {
        toast.error(
          t('canvas.storyboardGroup.batchDownloadPartial', {
            total,
            failed: total - downloaded,
          })
        );
        return;
      }
      toast.success(t('canvas.storyboardGroup.batchDownloadSuccess', { count: downloaded }));
    } finally {
      setIsDownloading(false);
    }
  }, [isDownloading, node.id, t]);

  const handleRegenerate = useCallback(() => {
    // 来源决定走哪条重跑：脚本行那条会按行快照判失效，视频故事的镜头组要按分镜表
    // 当前行判。走错那条的话，收拢时回写的组 id 会落到错误的字段上（脚本那条不认
    // linkedShotGroupId），源节点就再也找不到自己的组了。
    const result: { ok: boolean; armed: number; stale?: number; reason?: string; focusNodeIds?: string[] } =
      videoStoryOwnerId
        ? regenerateVideoStoryShotGroup(node.id)
        : regenerateStoryboardGroupImages(node.id);
    if (!result.ok) {
      toast.error(result.reason ?? t('canvas.storyboardGroup.regenerateFailed'));
      return;
    }
    if (result.armed === 0) {
      toast.success(t('canvas.storyboardGroup.regenerateNone'));
      return;
    }
    // 出图由节点挂载后自提交：先把组带回视口，屏外的成员才不会一直不提交。
    if (result.focusNodeIds?.length) useCanvasStore.getState().requestFocusNodes(result.focusNodeIds);
    else requestFocusNode(node.id);
    // 有脚本失效的那部分时把话说清楚：这次重跑包含「已出过图但脚本改过」的张。
    if (result.stale && result.stale > 0) {
      toast.success(
        t('canvas.storyboardGroup.regenerateStale', {
          count: result.armed,
          stale: result.stale,
        }),
      );
      return;
    }
    toast.success(t('canvas.storyboardGroup.regeneratePending', { count: result.armed }));
  }, [node.id, requestFocusNode, t, videoStoryOwnerId]);

  return (
    <ReactFlowNodeToolbar
      nodeId={node.id}
      isVisible
      position={NODE_TOOLBAR_POSITION}
      align={NODE_TOOLBAR_ALIGN}
      offset={NODE_TOOLBAR_OFFSET}
      className={NODE_TOOLBAR_CLASS}
    >
      <ZoomScaledToolbar origin="bottom center">
      <UiPanel className={PANEL_CLASS} onClick={(event) => event.stopPropagation()}>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <UiChipButton className={CHIP_CLASS}>
              <Crop className="h-4 w-4 text-text-muted" />
              <span>{t('canvas.storyboardGroup.aspect')}</span>
              <span className="text-text-muted">{aspectKey}</span>
              <ChevronDown className="h-3.5 w-3.5 text-text-muted" />
            </UiChipButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent className={MENU_CONTENT_CLASS} align="start">
            {STORYBOARD_ASPECTS.map((option) => (
              <DropdownMenuItem
                key={option.key}
                className={MENU_ITEM_CLASS}
                onClick={() => setStoryboardGroupConfig(node.id, { aspectKey: option.key })}
              >
                {option.key === aspectKey ? (
                  <Check className="h-4 w-4 text-text-muted" />
                ) : (
                  <span className="h-4 w-4" />
                )}
                <span>{option.label}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <UiChipButton className={CHIP_CLASS}>
              <Grid2x2 className="h-4 w-4 text-text-muted" />
              <span>{t('canvas.storyboardGroup.cols')}</span>
              <span className="text-text-muted">
                {t('canvas.storyboardGroup.colsOption', { cols: currentCols })}
              </span>
              <ChevronDown className="h-3.5 w-3.5 text-text-muted" />
            </UiChipButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent className={MENU_CONTENT_CLASS} align="start">
            {colOptions.map((cols) => (
              <DropdownMenuItem
                key={cols}
                className={MENU_ITEM_CLASS}
                onClick={() => setStoryboardGroupConfig(node.id, { cols })}
              >
                {cols === currentCols ? (
                  <Check className="h-4 w-4 text-text-muted" />
                ) : (
                  <span className="h-4 w-4" />
                )}
                <span>{t('canvas.storyboardGroup.colsOption', { cols })}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <UiChipButton className={CHIP_CLASS}>
              <Grid2x2 className="h-4 w-4 text-text-muted" />
              <span>{t('canvas.storyboardGroup.rows')}</span>
              <span className="text-text-muted">
                {t('canvas.storyboardGroup.rowsOption', { rows: currentRows })}
              </span>
              <ChevronDown className="h-3.5 w-3.5 text-text-muted" />
            </UiChipButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent className={MENU_CONTENT_CLASS} align="start">
            {rowOptions.map((rows) => (
              <DropdownMenuItem
                key={`rows-${rows}`}
                className={MENU_ITEM_CLASS}
                onClick={() => setStoryboardGroupConfig(node.id, { rows })}
              >
                {rows === currentRows ? (
                  <Check className="h-4 w-4 text-text-muted" />
                ) : (
                  <span className="h-4 w-4" />
                )}
                <span>{t('canvas.storyboardGroup.rowsOption', { rows })}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <UiChipButton
          className={`${CHIP_CLASS} ${showIndex ? '!text-cyan-200' : ''}`}
          onClick={() => setStoryboardGroupConfig(node.id, { showIndex: !showIndex })}
        >
          <Hash className="h-4 w-4" />
          <span>{t('canvas.storyboardGroup.index')}</span>
        </UiChipButton>

        {/* 脚本已变更 ⇒ 这几张图与当前脚本行对不上（LibTV 的级联规则）。 */}
        {staleCount > 0 ? (
          <UiChipButton
            className={`${CHIP_CLASS} !text-amber-200`}
            onClick={() => void handleRegenerate()}
            title={t('canvas.storyboardGroup.staleHint', { count: staleCount })}
          >
            <AlertTriangle className="h-4 w-4" />
            <span>{t('canvas.storyboardGroup.stale', { count: staleCount })}</span>
          </UiChipButton>
        ) : null}

        <UiChipButton
          className={CHIP_CLASS}
          onClick={() => void handleStitch()}
          disabled={isStitching}
        >
          {isStitching ? (
            <Loader2 className="h-4 w-4 animate-spin text-text-muted" />
          ) : (
            <Combine className="h-4 w-4 text-text-muted" />
          )}
          <span>{t('canvas.storyboardGroup.stitch')}</span>
        </UiChipButton>

        <div className="mx-1 h-4 w-px shrink-0 bg-white/[0.14]" />

        <UiChipButton
          className={CHIP_CLASS}
          onClick={() => void handleBatchDownload()}
          disabled={isDownloading}
        >
          {isDownloading ? (
            <Loader2 className="h-4 w-4 animate-spin text-text-muted" />
          ) : (
            <Download className="h-4 w-4 text-text-muted" />
          )}
          <span>{t('canvas.storyboardGroup.batchDownload')}</span>
        </UiChipButton>

        <UiChipButton
          className={CHIP_CLASS}
          onClick={handleRegenerate}
          disabled={regeneratableCount === 0}
          title={
            regeneratableCount > 0
              ? t('canvas.storyboardGroup.regenerate')
              : t('canvas.storyboardGroup.regenerateNone')
          }
        >
          <RefreshCw className="h-4 w-4 text-text-muted" />
          <span>{t('canvas.storyboardGroup.regenerate')}</span>
          {regeneratableCount > 0 ? (
            <span className="text-text-muted">{regeneratableCount}</span>
          ) : null}
        </UiChipButton>

        <div className="mx-1 h-4 w-px shrink-0 bg-white/[0.14]" />

        <UiChipButton
          className={CHIP_CLASS}
          onClick={() => convertStoryboardGroupToPlain(node.id)}
        >
          <Layers className="h-4 w-4 text-text-muted" />
          <span>{t('canvas.storyboardGroup.convertToPlain')}</span>
        </UiChipButton>

        <UiChipButton
          className={`${CHIP_CLASS} hover:!text-amber-200`}
          onClick={() => ungroupNode(node.id)}
        >
          <Unlink2 className="h-4 w-4" />
          <span>{t('canvas.storyboardGroup.ungroup')}</span>
        </UiChipButton>
      </UiPanel>
      </ZoomScaledToolbar>
    </ReactFlowNodeToolbar>
  );
});

StoryboardGroupToolbar.displayName = 'StoryboardGroupToolbar';
