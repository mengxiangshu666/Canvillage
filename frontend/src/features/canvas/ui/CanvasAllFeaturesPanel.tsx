// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo } from 'react';
import {
  Boxes,
  Clapperboard,
  Crop,
  Eraser,
  Expand,
  Globe2,
  Image as ImageIcon,
  ImageUpscale,
  LayoutGrid,
  Lightbulb,
  PenLine,
  RotateCw,
  ScanLine,
  User,
  Wand2,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import {
  isVideoNode,
  resolveNodeSourceImageUrl,
  type CanvasNode,
} from '@/features/canvas/domain/canvasNodes';
import { canvasEventBus } from '@/features/canvas/application/canvasServices';
import { useNodePowerHubStore } from '@/features/freezone/nodePowerHubStore';

/**
 * 画布底栏「全部功能」：把当前节点能用的编辑工具按图片/视频分组铺开。
 *
 * 工具本身还是节点上那几套浮层和画布事件，这里只做统一入口——不复制执行逻辑，
 * 也不新建第二条实现。没有可用工具的节点（文本、分组等）只显示一句说明。
 */
export interface CanvasAllFeaturesPanelProps {
  node: CanvasNode;
  onClose: () => void;
  onOpenUpscale: (nodeId: string) => void;
  onOpenMultiAngleEditor: (nodeId: string) => void;
  onOpenLightEditor: (nodeId: string) => void;
  onOpenScene360: (nodeId: string) => void;
  onOpenOutpaint: (nodeId: string) => void;
  onOpenRedraw: (nodeId: string) => void;
  onOpenErase: (nodeId: string) => void;
  onOpenRotate: (nodeId: string) => void;
  onOpenExtractFrames: (nodeId: string) => void;
}

interface FeatureAction {
  id: string;
  label: string;
  hint: string;
  icon: LucideIcon;
  run: () => void;
}

export function CanvasAllFeaturesPanel({
  node,
  onClose,
  onOpenUpscale,
  onOpenMultiAngleEditor,
  onOpenLightEditor,
  onOpenScene360,
  onOpenOutpaint,
  onOpenRedraw,
  onOpenErase,
  onOpenRotate,
  onOpenExtractFrames,
}: CanvasAllFeaturesPanelProps) {
  const openPowerTool = useNodePowerHubStore((state) => state.open);
  const nodeId = node.id;
  const hasImage = Boolean(resolveNodeSourceImageUrl(node));
  const hasVideo = isVideoNode(node);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const { imageActions, videoActions } = useMemo(() => {
    const runThenClose = (run: () => void) => () => {
      onClose();
      run();
    };
    const openToolDialog = (toolType: 'crop' | 'annotate' | 'split-storyboard') =>
      canvasEventBus.publish('tool-dialog/open', { nodeId, toolType });
    const captureFrame = (mode: 'first' | 'last' | 'current') =>
      canvasEventBus.publish('video-node/capture-frame', { nodeId, mode });

    const imageActions: FeatureAction[] = hasImage
      ? [
          {
            id: 'upscale',
            label: '高清',
            hint: '放大并补细节',
            icon: ImageUpscale,
            run: runThenClose(() => onOpenUpscale(nodeId)),
          },
          {
            id: 'expression',
            label: '情绪调节',
            hint: '人物表情锚点',
            icon: User,
            run: runThenClose(() => openPowerTool('expression', nodeId)),
          },
          {
            id: 'panorama',
            label: '全景',
            hint: '生成可环视场景',
            icon: Globe2,
            run: runThenClose(() => onOpenScene360(nodeId)),
          },
          {
            id: 'multi-angle',
            label: '多角度',
            hint: '换机位看同一主体',
            icon: Boxes,
            run: runThenClose(() => onOpenMultiAngleEditor(nodeId)),
          },
          {
            id: 'relight',
            label: '打光',
            hint: '重设画面光线',
            icon: Lightbulb,
            run: runThenClose(() => onOpenLightEditor(nodeId)),
          },
          {
            id: 'redraw',
            label: '重绘',
            hint: '局部重画',
            icon: Wand2,
            run: runThenClose(() => onOpenRedraw(nodeId)),
          },
          {
            id: 'erase',
            label: '擦除',
            hint: '抹掉不想要的物体',
            icon: Eraser,
            run: runThenClose(() => onOpenErase(nodeId)),
          },
          {
            id: 'outpaint',
            label: '扩图',
            hint: '向外补画面',
            icon: Expand,
            run: runThenClose(() => onOpenOutpaint(nodeId)),
          },
          {
            id: 'crop',
            label: '裁剪',
            hint: '截取局部或比例',
            icon: Crop,
            run: runThenClose(() => openToolDialog('crop')),
          },
          {
            id: 'annotate',
            label: '标注',
            hint: '圈出重点再生成',
            icon: PenLine,
            run: runThenClose(() => openToolDialog('annotate')),
          },
          {
            id: 'split',
            label: '分格抽取',
            hint: '九宫格拆成单帧',
            icon: LayoutGrid,
            run: runThenClose(() => openToolDialog('split-storyboard')),
          },
          {
            id: 'rotate',
            label: '旋转',
            hint: '按角度转正',
            icon: RotateCw,
            run: runThenClose(() => onOpenRotate(nodeId)),
          },
        ]
      : [];

    const videoActions: FeatureAction[] = hasVideo
      ? [
          {
            id: 'extract-frames',
            label: '逐帧拉片',
            hint: '把镜头拆成帧',
            icon: Clapperboard,
            run: runThenClose(() => onOpenExtractFrames(nodeId)),
          },
          {
            id: 'video-upscale',
            label: '视频高清',
            hint: '放大并补细节',
            icon: ImageUpscale,
            run: runThenClose(() => onOpenUpscale(nodeId)),
          },
          {
            id: 'capture-first',
            label: '截首帧',
            hint: '取第一帧做图',
            icon: ScanLine,
            run: runThenClose(() => captureFrame('first')),
          },
          {
            id: 'capture-last',
            label: '截尾帧',
            hint: '取最后一帧做图',
            icon: ScanLine,
            run: runThenClose(() => captureFrame('last')),
          },
          {
            id: 'capture-current',
            label: '截当前帧',
            hint: '取播放位置这一帧',
            icon: ImageIcon,
            run: runThenClose(() => captureFrame('current')),
          },
        ]
      : [];

    return { imageActions, videoActions };
  }, [
    hasImage,
    hasVideo,
    nodeId,
    onClose,
    onOpenErase,
    onOpenExtractFrames,
    onOpenLightEditor,
    onOpenMultiAngleEditor,
    onOpenOutpaint,
    onOpenRedraw,
    onOpenRotate,
    onOpenScene360,
    onOpenUpscale,
    openPowerTool,
  ]);

  const empty = imageActions.length === 0 && videoActions.length === 0;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/55 p-6 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-label="全部功能"
      onClick={onClose}
    >
      <div
        className="flex max-h-[78vh] w-[min(880px,94vw)] flex-col overflow-hidden rounded-[14px] border border-white/[0.12] bg-[#141519]/96 shadow-[0_24px_70px_rgba(0,0,0,0.55)] backdrop-blur-md"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between border-b border-white/[0.07] px-5 py-3.5">
          <div>
            <h2 className="text-[15px] font-semibold text-white">全部功能</h2>
            <p className="mt-0.5 text-[11px] text-white/40">
              当前节点：{String(node.data.displayName ?? node.id)}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="inline-flex size-8 items-center justify-center rounded-lg text-white/55 transition-colors hover:bg-white/10 hover:text-white"
          >
            <X className="size-4" />
          </button>
        </header>

        <div className="ui-scrollbar min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {empty ? (
            <p className="py-10 text-center text-xs text-white/40">
              这个节点没有可用的编辑工具，先选中图片或视频节点再打开。
            </p>
          ) : (
            <div className="flex flex-col gap-5">
              {videoActions.length > 0 ? (
                <FeatureGroup title="视频编辑" actions={videoActions} />
              ) : null}
              {imageActions.length > 0 ? (
                <FeatureGroup title="图片编辑" actions={imageActions} />
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FeatureGroup({ title, actions }: { title: string; actions: FeatureAction[] }) {
  return (
    <section>
      <h3 className="mb-2.5 text-[11px] font-medium text-white/38">{title}</h3>
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
        {actions.map((action) => {
          const Icon = action.icon;
          return (
            <button
              key={action.id}
              type="button"
              onClick={action.run}
              className="group flex items-center gap-2.5 rounded-[9px] px-2.5 py-2 text-left transition-colors hover:bg-white/[0.07]"
            >
              <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-[8px] border border-white/[0.09] bg-white/[0.05] text-white/70 group-hover:text-white">
                <Icon className="size-4" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-[12.5px] font-medium text-white/88">
                  {action.label}
                </span>
                <span className="block truncate text-[10.5px] text-white/35">
                  {action.hint}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
