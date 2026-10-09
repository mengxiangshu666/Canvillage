// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

import { CANVAS_NODE_OPS_PANEL_CLASS } from '@/features/canvas/ui/nodeFrameStyles';

interface OperationPanelShellProps {
  /** 展开时改用 body 级居中弹窗展示，收起时是节点下方的浮动面板。 */
  expanded: boolean;
  /** 关闭弹窗（点遮罩 / Esc / 再点收起按钮）。 */
  onCollapse: () => void;
  /** 收起态：节点下方浮动面板的定位 class。 */
  inlineClassName: string;
  /** 收起态：浮动面板的定位/尺寸 style（top/left/right/width/height）。 */
  inlineStyle: CSSProperties;
  /** 展开态：弹窗盒子的尺寸 style（width/height，可只给 width 让高度随内容）。 */
  modalStyle?: CSSProperties;
  children: ReactNode;
}

function stopPropagation(event: { stopPropagation: () => void }): void {
  event.stopPropagation();
}

// 节点激活时，操作区从节点下方淡入+轻微下滑出现（而非生硬地直接出现）。
// 收起态浮动面板挂在节点下方，故从上方滑入读起来像「从节点里展开」。
// motion-reduce 下不做位移/缩放动画，尊重系统的「减弱动态效果」。
//
// 导出供节点下方的「历史记录」面板复用同一套入场动画，使三块（顶部工具栏 /
// 操作区 / 历史记录）激活时同向同时长地浮现、视觉对齐，不再各跳各的。
export const NODE_OPS_PANEL_ENTER_CLASS =
  'animate-in fade-in-0 zoom-in-95 slide-in-from-top-2 duration-200 ease-out motion-reduce:animate-none';
const INLINE_ENTER_CLASS = NODE_OPS_PANEL_ENTER_CLASS;

/**
 * 节点浮动面板的「反向缩放」系数上下限。
 *
 * React Flow 给 viewport 加了 CSS transform，节点连同它下方的面板一起被缩放：
 * 画布缩到 60% 时面板里的 12px 文字只剩 7px，缩到 30% 就基本没法读了。
 *
 * 外部参考怎么解：`libtv §10_规格/UI_UX/CANVAS_UI_SPEC.md` §12 第 1、3 条 ——
 * 「一切浮动 UI 都是 portal，菜单/弹层挂在 body 尾部」「工具栏定位跟随选中节点
 * （视口坐标），缩放/平移时实时跟随」，所以它屏幕上尺寸恒定、怎么缩放都不变小。
 *
 * 本仓不照搬 portal 那条路：portal 需要在每次缩放/平移时重算屏幕坐标，
 * 与设计合同 §5「高频事件不触发无关 React 重渲染」相冲。改用等价且零重渲染的做法 ——
 * 套一层 `scale(1/zoom)`；缩放比例取自根元素的 `--st-canvas-zoom`
 * （由 `Canvas.tsx` 的单一写入器维护），与 `ZoomScaledToolbar` 同一套机制。
 *
 * 缩放锚点取**节点底边中心**：面板挂在节点下方，锚在底边中心时面板与节点的间距
 * 在屏幕上也是恒定的，不会因为放大而飘远。
 *
 * 上下限只影响极端缩放：0.25x–5x 之间屏幕尺寸严格恒定；超出后按边界继续缩放，
 * 免得 10% 缩放下 720px 的面板铺满整屏、或 800% 下缩放系数爆掉。
 */
const NODE_PANEL_COUNTER_MIN = 0.2;
const NODE_PANEL_COUNTER_MAX = 4;
const NODE_PANEL_COUNTER_SCALE = `clamp(${NODE_PANEL_COUNTER_MIN}, calc(1 / var(--st-canvas-zoom, 1)), ${NODE_PANEL_COUNTER_MAX})`;

/**
 * 包住节点下方任意浮动面板，使其在屏幕上尺寸恒定、不随画布缩放变小。
 *
 * 外层盒子与节点同尺寸、`pointer-events-none`（否则会挡住节点本身的拖拽与点击），
 * 内层面板需要 `pointer-events-auto` 才能继续交互 —— `OperationPanelShell`
 * 已代为补上；直接使用本组件时请自行给子元素加。
 */
export function NodePanelZoomAnchor({ children }: { children: ReactNode }) {
  return (
    <div
      className="pointer-events-none absolute inset-0"
      style={{
        transform: `scale(${NODE_PANEL_COUNTER_SCALE})`,
        transformOrigin: 'bottom center',
      }}
    >
      {children}
    </div>
  );
}

/**
 * 节点操作区的「外壳」：收起时就是节点下方的浮动面板；点「放大」后改为
 * document.body 级的居中弹窗展示同一份内容。
 *
 * 为什么必须 portal 到 body —— React Flow 给画布 viewport 加了 CSS transform，
 * 面板若用 `position: fixed` 会相对「被 transform 的祖先」定位而不是视口，无法
 * 真正居中；createPortal 到 body 才能脱离这个 transform 上下文。
 */
export function OperationPanelShell({
  expanded,
  onCollapse,
  inlineClassName,
  inlineStyle,
  modalStyle,
  children,
}: OperationPanelShellProps) {
  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onCollapse();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [expanded, onCollapse]);

  if (!expanded) {
    return (
      <NodePanelZoomAnchor>
        <div
          className={`${inlineClassName} ${INLINE_ENTER_CLASS} pointer-events-auto`}
          style={inlineStyle}
          onClick={stopPropagation}
        >
          {children}
        </div>
      </NodePanelZoomAnchor>
    );
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
      onClick={onCollapse}
      onPointerDown={stopPropagation}
    >
      <div
        className={`nodrag nowheel relative flex max-h-full max-w-full flex-col rounded-[var(--node-radius)] ${CANVAS_NODE_OPS_PANEL_CLASS}`}
        style={modalStyle}
        onClick={stopPropagation}
        onPointerDown={stopPropagation}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
