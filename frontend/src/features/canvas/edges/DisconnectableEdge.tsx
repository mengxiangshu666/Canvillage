// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  Position,
  type EdgeProps,
} from '@xyflow/react';
import { Scissors } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type CanvasEdge,
} from '@/features/canvas/domain/canvasNodes';
import {
  isCanvasMotionSuppressed,
  subscribeCanvasMotionState,
} from '@/features/canvas/application/canvasLod';
import { isPresetManagedEdge } from '@/features/canvas/domain/mainlineNodeFlags';
import { useCanvasStore } from '@/stores/canvasStore';
import { selectCanvasNodeById } from '@/features/canvas/application/canvasNodeIndex';
import { useSettingsStore } from '@/stores/settingsStore';
import {
  colorWithAlpha,
  useCanvasViewSettingsStore,
} from '@/features/canvas/ui/canvasViewSettingsStore';
import { selectDataFlowEdgeIds } from './edgeDataFlowBudget';
import {
  buildOrthogonalRoute,
  selectCanvasHasDraggingNode,
  selectCanvasRoutingSnapshot,
} from './edgeRouting';

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

const EDGE_ACTIVE_TRANSITION_MS = 300;
// 连接端点圆点半径，以及沿 port 方向朝节点外的偏移量（>=半径 → 整颗圆点落在节点外、贴边）。
const PORT_DOT_RADIUS = 4;
const PORT_DOT_OFFSET = 4;

// 端点圆点的外移向量：按 handle 所在边朝节点外偏移，避免被上层节点挡掉一半。
function portDotOffset(position: Position | undefined): { dx: number; dy: number } {
  switch (position) {
    case Position.Left:
      return { dx: -PORT_DOT_OFFSET, dy: 0 };
    case Position.Right:
      return { dx: PORT_DOT_OFFSET, dy: 0 };
    case Position.Top:
      return { dx: 0, dy: -PORT_DOT_OFFSET };
    case Position.Bottom:
      return { dx: 0, dy: PORT_DOT_OFFSET };
    default:
      return { dx: 0, dy: 0 };
  }
}
const EDGE_DISCONNECT_HOVER_DELAY_MS = 500;
const EDGE_DISCONNECT_LEAVE_GRACE_MS = 160;
const EDGE_DISCONNECT_ACTION_SIZE = 40;

export const DisconnectableEdge = memo(function DisconnectableEdge(props: EdgeProps) {
  const {
    id,
    source,
    target,
    selected,
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    markerEnd,
    style,
    data,
  } = props;
  const deleteEdge = useCanvasStore((state) => state.deleteEdge);
  const selectedNodeId = useCanvasStore((state) => state.selectedNodeId);
  const canvasEdgeRoutingMode = useSettingsStore((state) => state.canvasEdgeRoutingMode);
  const edgeColor = useCanvasViewSettingsStore((state) => state.edgeColor);
  const routingSnapshotRef = useRef<ReturnType<typeof selectCanvasRoutingSnapshot> | null>(null);
  // 仅 smartOrthogonal 避障需要路由快照。快照按 nodes 数组 identity 缓存，
  // 每条边只比较一个 snapshot 引用，不再对完整 nodes 数组做 E×N shallow 比较。
  // 节点拖动期间保持上一帧稳定快照，端点位置仍由 React Flow 实时更新；
  // 松手后才建立新快照，避免所有智能避让边在每个拖动帧重复做障碍扫描。
  const routingSnapshot = useCanvasStore((state) =>
    canvasEdgeRoutingMode === 'smartOrthogonal'
      ? selectCanvasHasDraggingNode(state.nodes)
        ? null
        : selectCanvasRoutingSnapshot(state.nodes)
      : null,
  );
  const canvasHasDraggingNode =
    canvasEdgeRoutingMode === 'smartOrthogonal'
    && routingSnapshot === null
    && routingSnapshotRef.current !== null;
  if (routingSnapshot) routingSnapshotRef.current = routingSnapshot;
  const effectiveRoutingSnapshot = routingSnapshot ?? routingSnapshotRef.current;
  const [isHovered, setIsHovered] = useState(false);
  const [showDisconnectAction, setShowDisconnectAction] = useState(false);
  const disconnectHoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disconnectLeaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const edgeIsPresetManaged = isPresetManagedEdge({ id, source, target, data } as CanvasEdge);

  const clearDisconnectHoverTimer = () => {
    if (disconnectHoverTimerRef.current === null) return;
    clearTimeout(disconnectHoverTimerRef.current);
    disconnectHoverTimerRef.current = null;
  };

  const clearDisconnectLeaveTimer = () => {
    if (disconnectLeaveTimerRef.current === null) return;
    clearTimeout(disconnectLeaveTimerRef.current);
    disconnectLeaveTimerRef.current = null;
  };

  const handleInteractiveEnter = () => {
    clearDisconnectLeaveTimer();
    setIsHovered(true);
    if (edgeIsPresetManaged || showDisconnectAction || disconnectHoverTimerRef.current !== null) {
      return;
    }
    disconnectHoverTimerRef.current = setTimeout(() => {
      setShowDisconnectAction(true);
      disconnectHoverTimerRef.current = null;
    }, EDGE_DISCONNECT_HOVER_DELAY_MS);
  };

  const handleInteractiveLeave = () => {
    clearDisconnectHoverTimer();
    clearDisconnectLeaveTimer();
    disconnectLeaveTimerRef.current = setTimeout(() => {
      setIsHovered(false);
      setShowDisconnectAction(false);
      disconnectLeaveTimerRef.current = null;
    }, EDGE_DISCONNECT_LEAVE_GRACE_MS);
  };

  useEffect(() => {
    return () => {
      clearDisconnectHoverTimer();
      clearDisconnectLeaveTimer();
    };
  }, []);

  // 选中态高亮：没有选中节点时所有连线保持灰色；选中某节点后，与它相连的
  // 连线点亮（accent），其余连线压暗，突出与当前节点的关系。
  const hasSelection = selectedNodeId != null;
  const isConnectedToSelected =
    hasSelection && (source === selectedNodeId || target === selectedNodeId);

  const { edgePath, labelX, labelY } = useMemo(() => {
    if (canvasEdgeRoutingMode === 'spline') {
      const [path, nextLabelX, nextLabelY] = getBezierPath({
        sourceX,
        sourceY,
        sourcePosition,
        targetX,
        targetY,
        targetPosition,
      });
      return {
        edgePath: path,
        labelX: nextLabelX,
        labelY: nextLabelY,
      };
    }

    const route = buildOrthogonalRoute({
      sourceId: source,
      targetId: target,
      sourceX,
      sourceY,
      sourcePosition: sourcePosition ?? Position.Right,
      targetX,
      targetY,
      targetPosition: targetPosition ?? Position.Left,
      nodes: [],
      // During a drag the endpoints move every frame. Keep that hot path to a
      // cheap orthogonal midpoint route; the first non-drag frame rebuilds the
      // complete obstacle-aware route from the fresh snapshot.
      smartAvoidance: canvasEdgeRoutingMode === 'smartOrthogonal' && !canvasHasDraggingNode,
      routingSnapshot: effectiveRoutingSnapshot ?? undefined,
    });
    return {
      edgePath: route.path,
      labelX: route.labelX,
      labelY: route.labelY,
    };
  }, [
    canvasEdgeRoutingMode,
    canvasHasDraggingNode,
    effectiveRoutingSnapshot,
    source,
    sourcePosition,
    sourceX,
    sourceY,
    target,
    targetPosition,
    targetX,
    targetY,
  ]);

  // 直接在 selector 里算成布尔值:返回原始值,Object.is 比较使本边只在「是否处理中」
  // 翻转时才重渲染,而非每次任意节点变化都重算。
  const isProcessingEdge = useCanvasStore((state) => {
    const sourceNode = selectCanvasNodeById(state.nodes, source);
    const targetNode = selectCanvasNodeById(state.nodes, target);

    if (!sourceNode || !targetNode || targetNode.type !== CANVAS_NODE_TYPES.exportImage) {
      return false;
    }

    const isSupportedSource =
      sourceNode.type === CANVAS_NODE_TYPES.storyboardGen ||
      sourceNode.type === CANVAS_NODE_TYPES.imageEdit;
    if (!isSupportedSource) {
      return false;
    }

    return (targetNode.data as { isGenerating?: boolean } | undefined)?.isGenerating === true;
  });

  const dataRecord = recordValue(data);
  const bindingRole =
    ['candidate_binding', 'role_binding'].includes(String(dataRecord.edgeKind || '')) &&
    typeof dataRecord.role === 'string'
      ? dataRecord.role
      : null;

  const processingStroke = colorWithAlpha(edgeColor, 0.94);
  const processingDashStroke = colorWithAlpha(edgeColor, 1);
  const baseStrokeWidth = isProcessingEdge ? (selected ? 2.7 : 2.2) : 2;

  // 处理中的连线始终保持自己的 accent 高亮样式，不参与选中态调光。
  // hover/选中相连连线轻微点亮；常态灰色半透明；选中后无关连线再压暗一档。
  const highlightStroke = colorWithAlpha(edgeColor, 0.82);
  const bindingStroke = colorWithAlpha(edgeColor, 0.7);
  const bindingHighlightStroke = colorWithAlpha(edgeColor, 0.92);
  const baseStroke = colorWithAlpha(edgeColor, 0.48);
  const dimStroke = colorWithAlpha(edgeColor, 0.22);
  const resolvedStroke = isProcessingEdge
    ? processingStroke
    : isConnectedToSelected || selected || isHovered
      ? (bindingRole ? bindingHighlightStroke : highlightStroke)
      : hasSelection
        ? dimStroke
        : (bindingRole ? bindingStroke : baseStroke);
  const resolvedStrokeWidth = baseStrokeWidth;
  // 平移/缩放/拖节点期间每帧都在重排，低清态下光带也看不清；两种情况一律不渲染。
  // 这些光带是 SMIL `animateMotion` + `feGaussianBlur`，CSS 的 animation-play-state
  // 管不到它们，只能靠不挂载来省掉开销。
  const motionSuppressed = useSyncExternalStore(
    subscribeCanvasMotionState,
    isCanvasMotionSuppressed,
    () => false,
  );
  const isConnectedToSelectedNode = isConnectedToSelected && !isHovered && !selected;
  // 选中一个节点会牵动它全部入边。接 9 张图时那就是 9 个模糊滤镜 + 27 个无限动画，
  // 所以「因选中而点亮」的光带按稳定顺序限量；hover 的是单条，不受限额影响。
  const selectedFlowEdgeIds = useCanvasStore((state) =>
    isConnectedToSelectedNode
      ? selectDataFlowEdgeIds(state.edges, state.selectedNodeId)
      : null,
  );
  const isWithinDataFlowBudget =
    selectedFlowEdgeIds !== null && selectedFlowEdgeIds.has(id);
  const shouldShowDataFlow =
    !isProcessingEdge
    && !motionSuppressed
    && (isHovered || selected || isWithinDataFlowBudget);
  const flowPathId = `canvas-data-flow-path-${id.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
  const flowGradientId = `canvas-data-flow-gradient-${id.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
  const flowGlowId = `canvas-data-flow-glow-${id.replace(/[^a-zA-Z0-9_-]/g, '_')}`;

  const sourceDotOffset = portDotOffset(sourcePosition ?? Position.Right);
  const targetDotOffset = portDotOffset(targetPosition ?? Position.Left);

  return (
    <>
      {isProcessingEdge && (
        <path
          d={edgePath}
          fill="none"
          stroke={processingDashStroke}
          strokeWidth={selected ? 2.5 : 2.1}
          strokeLinecap="round"
          strokeDasharray="8 10"
          className="canvas-processing-edge__flow"
          style={{ pointerEvents: 'none' }}
        />
      )}
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          ...style,
          stroke: resolvedStroke,
          strokeWidth: resolvedStrokeWidth,
          transition: `stroke ${EDGE_ACTIVE_TRANSITION_MS}ms ease, stroke-width ${EDGE_ACTIVE_TRANSITION_MS}ms ease`,
        }}
      />
      {/* 连接端点小圆点（对标 libtv）：连线建立后，在两端节点的连接口各画一个连接点，
          让「已连线」状态在节点上可见——过去节点的 handle 被全局 opacity:0 藏起，连上
          之后节点看起来和没连一样。画在边上（而非各节点的 Handle）天然覆盖所有节点类型
          （图片/视频/…），且随节点移动、随边高亮一起变色。深色描边保证压在亮图上也看得清。
          端点正好压在节点边沿，而边图层在节点之下，直接画会被节点挡掉一半——沿各自 port
          方向朝外偏移 PORT_DOT_OFFSET，让圆点整颗落在节点外、贴着边、正好压在连线上。 */}
      <g style={{ pointerEvents: 'none' }}>
        <circle
          cx={sourceX + sourceDotOffset.dx}
          cy={sourceY + sourceDotOffset.dy}
          r={PORT_DOT_RADIUS}
          fill={resolvedStroke}
          stroke="rgba(9, 9, 9, 0.55)"
          strokeWidth={1}
          style={{ transition: `fill ${EDGE_ACTIVE_TRANSITION_MS}ms ease` }}
        />
        <circle
          cx={targetX + targetDotOffset.dx}
          cy={targetY + targetDotOffset.dy}
          r={PORT_DOT_RADIUS}
          fill={resolvedStroke}
          stroke="rgba(9, 9, 9, 0.55)"
          strokeWidth={1}
          style={{ transition: `fill ${EDGE_ACTIVE_TRANSITION_MS}ms ease` }}
        />
      </g>
      {!isProcessingEdge && (
        <path
          className="nodrag nopan"
          d={edgePath}
          fill="none"
          stroke="transparent"
          strokeWidth={24}
          strokeLinecap="round"
          style={{ pointerEvents: 'stroke', cursor: 'default' }}
          onPointerEnter={handleInteractiveEnter}
          onPointerLeave={handleInteractiveLeave}
          onPointerDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
        />
      )}
      {shouldShowDataFlow && (
        <>
          <defs>
            <path id={flowPathId} d={edgePath} />
            <linearGradient
              id={flowGradientId}
              gradientUnits="userSpaceOnUse"
              x1="-48"
              y1="0"
              x2="48"
              y2="0"
            >
              <stop offset="0%" stopColor={edgeColor} stopOpacity="0" />
              <stop offset="42%" stopColor={edgeColor} stopOpacity="0.28" />
              <stop offset="100%" stopColor={edgeColor} stopOpacity="0.72" />
            </linearGradient>
            <filter id={flowGlowId} x="-80%" y="-240%" width="260%" height="580%">
              <feGaussianBlur stdDeviation="14" />
            </filter>
          </defs>
          {[0, -2.33, -4.67].map((begin) => (
            <g
              key={begin}
              className="canvas-data-edge__packet"
              style={{ pointerEvents: 'none', opacity: 0.72 }}
            >
              <g transform="scale(0.45, 1)">
                <line
                  x1="-46"
                  y1="0"
                  x2="46"
                  y2="0"
                  fill="none"
                  stroke={`url(#${flowGradientId})`}
                  strokeLinecap="round"
                  strokeWidth={12}
                  opacity={0.34}
                  filter={`url(#${flowGlowId})`}
                />
                <line
                  x1="-42"
                  y1="0"
                  x2="42"
                  y2="0"
                  fill="none"
                  stroke={`url(#${flowGradientId})`}
                  strokeLinecap="round"
                  strokeWidth={4}
                />
              </g>
              <animateMotion
                className="canvas-data-edge__packet-motion"
                dur="7s"
                begin={`${begin}s`}
                repeatCount="indefinite"
                rotate="auto"
              >
                <mpath href={`#${flowPathId}`} />
              </animateMotion>
            </g>
          ))}
        </>
      )}
      {showDisconnectAction && !edgeIsPresetManaged && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan absolute"
            style={{
              height: EDGE_DISCONNECT_ACTION_SIZE,
              width: EDGE_DISCONNECT_ACTION_SIZE + 16,
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'all',
            }}
            onPointerEnter={handleInteractiveEnter}
            onPointerLeave={handleInteractiveLeave}
            onPointerDown={(event) => {
              event.preventDefault();
              event.stopPropagation();
            }}
          >
            <button
              type="button"
              className="absolute left-1/2 top-0 flex h-10 w-10 -translate-x-1/2 items-center justify-center rounded-full border border-white/15 bg-[#17191d]/95 text-white/85 shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_12px_28px_rgba(0,0,0,0.45)] backdrop-blur transition-[border-color,color,box-shadow] duration-150 hover:border-white/30 hover:text-white hover:shadow-[0_0_0_1px_rgba(255,255,255,0.12),0_0_22px_rgba(120,180,255,0.22),0_12px_30px_rgba(0,0,0,0.5)]"
              onClick={(event) => {
                event.stopPropagation();
                deleteEdge(id);
              }}
              aria-label="断开连线"
            >
              <Scissors className="h-6 w-6 stroke-[2.35]" />
            </button>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});
