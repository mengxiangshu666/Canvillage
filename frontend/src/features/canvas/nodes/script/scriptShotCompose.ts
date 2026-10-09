// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  isVideoComposeNode,
  type CanvasNode,
  type VideoDeliverySpec,
  type VideoShotContractFacts,
  type VideoComposeNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveAbsolutePosition, useCanvasStore } from '@/stores/canvasStore';
import {
  normalizeScriptDeliverySpec,
  resolveScriptDeliverySpec,
  scriptDeliverySpecsEqual,
  scriptRowsOf,
  scriptShotVideoNodesInRowOrder,
  SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD,
  scriptTailReferenceStaleReason,
} from './scriptShotVideos';
import { scriptRowFingerprint } from './scriptRowFingerprint';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import { buildScriptRowKeys } from './scriptViews';
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import { parseDurationSeconds } from './scriptStats';
import { scriptShotFrameChangeReason } from './scriptShotKeyframes';
import { scriptVideoExecutionPromptMatches } from './scriptShotVideoReferences';
import { videoGenerationSourceMatches } from '@/features/canvas/application/videoGenerationSource';
/**
 * 「成片」：在脚本节点派生的那排镜头视频下游，自动建一个**视频合成**节点并把边连好。
 *
 * 为什么需要它：合成节点早就在（时间线剪辑器 `/compose/VideoComposeModal`，后端
 * `submitFreezoneVideoCompose`），但它是**手工**用的 —— 用户得自己从菜单里拖一个出来、
 * 再一个个把镜头视频连进去。一条从脚本到成片的链路，最后一跳要手画 N 条边，这个断点
 * 就是「一键成片」缺的那一块。
 *
 * 三个刻意的选择：
 *
 * 1. **正式成片全有或全无**。脚本里的每一镜都必须已经派生视频、已经出片，
 *    且没有任何失败标记；缺一镜就拒绝创建成片节点。不能让部分拼接的粗剪
 *    伪装成正式成片，也不能让失败镜头被静默吞掉。
 * 2. **顺序取行序**，不取连线顺序。合成台的时间线初始化按 `seedNodeIds` 的顺序摆放，
 *    而合成节点的上游收集是按边序 —— 边是我们自己建的，按行序建，两者因此天然一致。
 *    LibTV 的分镜表就是片子顺序（`§DISTILL/06_AGENT_BEHAVIOR_SPEC.md §5` 的时间轴
 *    写法），这一点必须守住：把第 3 镜排到第 1 镜前面，成片就散了。
 * 3. **幂等**。已经连过这个脚本的合成节点就复用（补边、不新建），否则连点两次会在
 *    画布上堆出两个时间线，用户分不清哪个是那条。
 */

/** 合成节点上记录「我是哪个脚本节点的成片」。 */
export const SCRIPT_COMPOSE_SOURCE_FIELD = 'scriptComposeSourceNodeId';

/** 合成节点的设计尺寸（与 `VideoComposeNode` 的 NODE_WIDTH / NODE_HEIGHT 一致）。 */
export const SCRIPT_COMPOSE_CELL_WIDTH = 240;
export const SCRIPT_COMPOSE_CELL_HEIGHT = 136;
export const SCRIPT_EDIT_DURATION_FIELD = 'scriptEditDurationMsByShotId';

export interface AssembleScriptFilmParams {
  scriptNodeId: string;
  preview?: boolean;
}

export type AssembleScriptFilmResult =
  | {
      ok: true;
      /** 本次复用的还是新建的。 */
      mode: 'created' | 'reused';
      composeNodeId: string;
      /** 连进去的镜头视频条数（按行序）。 */
      clipCount: number;
      /** 正式成片不留可跳过项；成功时恒为 0。 */
      skippedNoVideo: number;
      warning?: string;
    }
  | { ok: false; reason: string };

interface ScriptFilmReadiness {
  ready: boolean;
  clips: CanvasNode[];
  skippedNoVideo: number;
  reason: string | null;
}

function expectedScriptDeliverySpec(scriptNode: CanvasNode | undefined): VideoDeliverySpec {
  const videoConfig =
    scriptNode?.data?.videoGenConfig &&
    typeof scriptNode.data.videoGenConfig === 'object' &&
    !Array.isArray(scriptNode.data.videoGenConfig)
      ? (scriptNode.data.videoGenConfig as { aspectRatio?: unknown; deliverySpec?: unknown })
      : undefined;
  return (
    normalizeScriptDeliverySpec(videoConfig?.deliverySpec) ??
    resolveScriptDeliverySpec(
      typeof videoConfig?.aspectRatio === 'string' ? videoConfig.aspectRatio : null,
    )
  );
}

function hasCompleteContractFacts(value: unknown): value is VideoShotContractFacts {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const facts = value as Record<string, unknown>;
  const requiredText = ['shotId', 'subject', 'action', 'cameraMovement'];
  if (requiredText.some((key) => typeof facts[key] !== 'string' || !facts[key].trim())) {
    return false;
  }
  return (
    typeof facts.continuityIn === 'object' &&
    facts.continuityIn !== null &&
    !Array.isArray(facts.continuityIn) &&
    typeof facts.continuityOut === 'object' &&
    facts.continuityOut !== null &&
    !Array.isArray(facts.continuityOut)
  );
}

function shotVideoSpec(node: CanvasNode): VideoDeliverySpec | null {
  return normalizeScriptDeliverySpec(node.data?.deliverySpec);
}

/** 正式成片的完整对账结果；UI 只读，不产生副作用。 */
export function scriptFilmReadiness(scriptNodeId: string): ScriptFilmReadiness {
  const store = useCanvasStore.getState();
  const scriptNode = store.nodes.find((node) => node.id === scriptNodeId);
  if (!scriptNode) {
    return { ready: false, clips: [], skippedNoVideo: 0, reason: '脚本节点已不存在' };
  }
  if (scriptNode.data?.scriptDirectorPlanNeedsSync === true) {
    return { ready: false, clips: [], skippedNoVideo: 0, reason: DIRECTOR_PLAN_PENDING_REASON };
  }
  const rows = scriptRowsOf(scriptNodeId);
  const videos = scriptShotVideoNodesInRowOrder(scriptNodeId);
  if (rows.length === 0) {
    return {
      ready: false,
      clips: [],
      skippedNoVideo: 0,
      reason: '脚本表里还没有分镜行，先生成脚本',
    };
  }
  if (videos.length === 0) {
    return {
      ready: false,
      clips: [],
      skippedNoVideo: rows.length,
      reason: '还没有派生的镜头视频，先「逐镜出视频」',
    };
  }
  const expectedSpec = expectedScriptDeliverySpec(scriptNode);
  const byRow = new Map<string, CanvasNode>();
  videos.forEach((node) => {
    const rowKey = readScriptShotId(node.data);
    if (rowKey && !byRow.has(rowKey)) byRow.set(rowKey, node);
  });
  const orderedRowKeys = buildScriptRowKeys(rows);
  const rowKeys = new Set(orderedRowKeys);
  const unknownClip = videos.find((node) => {
    const rowKey = readScriptShotId(node.data);
    return !rowKey || !rowKeys.has(rowKey);
  });
  if (unknownClip) {
    return {
      ready: false,
      clips: [],
      skippedNoVideo: 0,
      reason: '画布上存在不属于当前脚本行的旧镜头视频，请先重新逐镜出视频',
    };
  }
  if (rows.length >= 2) {
    const missingContract = videos.find((node) => !hasCompleteContractFacts(node.data?.shotContractFacts));
    if (missingContract) {
      return {
        ready: false,
        clips: [],
        skippedNoVideo: 0,
        reason: '多镜正式成片缺少完整镜头合同事实，请重新逐镜出视频',
      };
    }
  }
  const clips: CanvasNode[] = [];
  let missingCount = 0;
  for (let index = 0; index < rows.length; index += 1) {
    const rowKey = orderedRowKeys[index] ?? `__row_${index}`;
    const clip = byRow.get(rowKey);
    if (!clip) {
      missingCount += 1;
      continue;
    }
    const videoUrl = typeof clip.data?.videoUrl === 'string' ? clip.data.videoUrl.trim() : '';
    if (!videoUrl) {
      missingCount += 1;
      continue;
    }
    const facts = clip.data?.shotContractFacts as VideoShotContractFacts | null | undefined;
    if (facts && !facts.executionPrompt?.trim()) {
      return { ready: false, clips: [], skippedNoVideo: 0, reason: `第 ${index + 1} 镜尚无正文执行版本，请重新逐镜出视频` };
    }
    if (facts?.executionPrompt && !scriptVideoExecutionPromptMatches(clip.data)) {
      return { ready: false, clips: [], skippedNoVideo: 0, reason: `第 ${index + 1} 镜正文已修改，当前镜头合同仍对应旧内容，请回脚本同步后重新出视频` };
    }
    if (clip.data?.canvas_auto_generate_once === true || clip.data?.isGenerating === true) {
      return { ready: false, clips: [], skippedNoVideo: 0, reason: `第 ${index + 1} 镜正在重做，请等新视频完成后再成片` };
    }
    if (facts?.executionPrompt && !videoGenerationSourceMatches(videoUrl, clip.data.videoGenerationSource, facts.executionPrompt)) {
      return { ready: false, clips: [], skippedNoVideo: 0, reason: `第 ${index + 1} 镜视频没有对应当前内容的生成记录，请重新出这一镜；已有视频仍可预览` };
    }
    if (clip.data?.generationError) {
      return {
        ready: false,
        clips: [],
        skippedNoVideo: 0,
        reason: `第 ${index + 1} 镜出片失败：${String(clip.data.generationError)}`,
      };
    }
    const contentSnapshot = clip.data?.[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD];
    if (typeof contentSnapshot === 'string' && contentSnapshot !== scriptRowFingerprint(rows[index], index)) {
      return {
        ready: false, clips: [], skippedNoVideo: 0,
        reason: `第 ${index + 1} 镜脚本已修改，当前视频仍对应旧内容，请重新出这一镜的视频`,
      };
    }
    const clipSpec = shotVideoSpec(clip);
    const tailReason = scriptShotFrameChangeReason(clip, store) ?? scriptTailReferenceStaleReason(clip, store);
    if (tailReason) {
      return { ready: false, clips: [], skippedNoVideo: 0, reason: `第 ${index + 1} 镜${tailReason}` };
    }
    if (rows.length >= 2) {
      if (!clipSpec || !scriptDeliverySpecsEqual(clipSpec, expectedSpec)) {
        return {
          ready: false,
          clips: [],
          skippedNoVideo: 0,
          reason: `第 ${index + 1} 镜的交付规格与全片不一致，请重新逐镜出视频`,
        };
      }
    } else if (clipSpec && !scriptDeliverySpecsEqual(clipSpec, expectedSpec)) {
      return {
        ready: false,
        clips: [],
        skippedNoVideo: 0,
        reason: '镜头的交付规格与全片不一致，请重新出视频',
      };
    }
    clips.push(clip);
  }
  if (missingCount > 0) {
    return {
      ready: false,
      clips: [],
      skippedNoVideo: missingCount,
      reason: `还有 ${missingCount} 镜没有出好片，正式成片必须等全部镜头完成`,
    };
  }
  return { ready: true, clips, skippedNoVideo: 0, reason: null };
}

/** 这个脚本节点已经有的成片节点（幂等复用的关键）。 */
export function existingScriptComposeNode(scriptNodeId: string, preview = false): CanvasNode | null {
  const store = useCanvasStore.getState();
  return (
    store.nodes.find(
      (node) =>
        isVideoComposeNode(node) && node.data?.[SCRIPT_COMPOSE_SOURCE_FIELD] === scriptNodeId &&
        (node.data?.scriptFilmPreview === true) === preview,
    ) ?? null
  );
}

/** 脚本节点派生的镜头视频里「已经有片子」的（行序）。 */
export function scriptFilmClips(scriptNodeId: string): {
  clips: CanvasNode[];
  skippedNoVideo: number;
} {
  const rowKeys = new Set(buildScriptRowKeys(scriptRowsOf(scriptNodeId)));
  const videos = scriptShotVideoNodesInRowOrder(scriptNodeId).filter(node => rowKeys.has(readScriptShotId(node.data) ?? ''));
  const clips = videos.filter(
    (node) => typeof node.data?.videoUrl === 'string' && node.data.videoUrl.length > 0,
  );
  return { clips, skippedNoVideo: Math.max(0, rowKeys.size - clips.length) };
}

/**
 * 建 / 复用合成节点并按行序连边。
 *
 * 落位放在整排镜头视频的**下方**（`y + 高度 + 间距`，x 对齐那一排的左边缘）：
 * 用户的阅读方向是「分镜图 → 视频 → 成片」自上而下，放到右侧会把成片推到画布深处，
 * 与它下游的地位（这一段的终点）不符。
 */
export function assembleScriptFilm(params: AssembleScriptFilmParams): AssembleScriptFilmResult {
  const store = useCanvasStore.getState();
  const scriptNode = store.nodes.find((node) => node.id === params.scriptNodeId);
  if (!scriptNode) return { ok: false, reason: '脚本节点已不存在' };
  const strictReadiness = scriptFilmReadiness(params.scriptNodeId);
  const previewClips = params.preview ? scriptFilmClips(params.scriptNodeId) : null;
  const readiness = previewClips ? {
    ready: previewClips.clips.length > 0,
    clips: previewClips.clips,
    skippedNoVideo: previewClips.skippedNoVideo,
    reason: '还没有可播放的镜头视频，先完成至少一镜',
  } : strictReadiness;
  if (!readiness.ready) {
    return { ok: false, reason: readiness.reason ?? '镜头视频尚未准备好，不能创建正式成片' };
  }
  const clips = readiness.clips;

  const warning = params.preview && !strictReadiness.ready
    ? `镜头预览：${(strictReadiness.reason ?? '尚未达到正式交付要求').replace(/，请.*$/, '')}。可先合看已有视频。` : undefined;
  const existing = existingScriptComposeNode(params.scriptNodeId, params.preview === true);
  let composeNodeId = existing?.id ?? '';
  const scriptRows = ((scriptNode.data?.scriptResult as { rows?: FreezoneStoryScriptRow[] } | undefined)?.rows ?? []);
  const rowKeys = buildScriptRowKeys(scriptRows);
  const editDurations = Object.fromEntries(scriptRows.flatMap((row, index) => {
    const shotId = rowKeys[index];
    const seconds = parseDurationSeconds(row.duration);
    return shotId && seconds != null ? [[shotId, Math.round(seconds * 1000)]] : [];
  }));

  if (!composeNodeId) {
    const nodeMap = new Map(store.nodes.map((node) => [node.id, node] as const));
    const rects = clips.map((node) => {
      const absolute = resolveAbsolutePosition(node, nodeMap);
      return {
        x: absolute.x,
        y: absolute.y,
        width: node.measured?.width ?? 580,
        height: node.measured?.height ?? 380,
      };
    });
    const left = Math.min(...rects.map((rect) => rect.x));
    const bottom = Math.max(...rects.map((rect) => rect.y + rect.height));
    const position = { x: Math.round(left), y: Math.round(bottom + 48) };
    composeNodeId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.videoCompose,
      position,
      {
        displayName: `${params.preview ? '镜头预览' : '成片'} · ${resolveScriptTitle(scriptNode)}`,
        scriptFilmPreview: params.preview === true,
        scriptFilmPreviewWarning: warning ?? null,
        [SCRIPT_COMPOSE_SOURCE_FIELD]: params.scriptNodeId,
        [SCRIPT_EDIT_DURATION_FIELD]: editDurations,
        scriptClipNodeIds: clips.map(clip => clip.id),
      } as Partial<VideoComposeNodeData>,
    );
  }
  if (!composeNodeId) return { ok: false, reason: '视频合成节点创建失败' };
  if (existing) useCanvasStore.getState().updateNodeData(composeNodeId, {
    scriptFilmPreviewWarning: warning ?? null,
    [SCRIPT_EDIT_DURATION_FIELD]: editDurations,
    scriptClipNodeIds: clips.map(clip => clip.id),
  });

  // A preview refresh must not keep clips removed from the current script.
  if (params.preview) {
    const currentIds = new Set(clips.map(clip => clip.id));
    useCanvasStore.getState().edges.filter(edge => edge.target === composeNodeId && edge.data?.role === 'scriptFilmClip' && !currentIds.has(edge.source))
      .forEach(edge => useCanvasStore.getState().deleteEdge(edge.id));
  }

  // 边按行序建：合成台初始化时间线时按 seedNodeIds / 上游边序遍历，两者必须同序。
  const liveEdges = useCanvasStore.getState().edges;
  clips.forEach((clip) => {
    const connected = liveEdges.some(
      (edge) => edge.source === clip.id && edge.target === composeNodeId,
    );
    if (connected) return;
    useCanvasStore.getState().addEdgeWithData(
      clip.id,
      composeNodeId,
      { edgeKind: 'mainline_data', propagates: true, role: 'scriptFilmClip', label: '成片素材' },
      {
        id: `edge_${clip.id}_to_${composeNodeId}_scriptFilmClip`,
        sourceHandle: 'source',
        targetHandle: 'target',
      },
    );
  });

  return {
    ok: true,
    mode: existing ? 'reused' : 'created',
    composeNodeId,
    clipCount: clips.length,
    skippedNoVideo: readiness.skippedNoVideo,
    warning,
  };
}

function resolveScriptTitle(node: CanvasNode): string {
  const title = node.data?.scriptTitle;
  return typeof title === 'string' && title.trim().length > 0 ? title.trim() : '脚本';
}

/** 下游成片的读数（UI 侧判定按钮该显示什么，不产生副作用）。 */
export function scriptFilmStatus(scriptNodeId: string): {
  clipCount: number;
  skippedNoVideo: number;
  hasCompose: boolean;
  ready: boolean;
  blockingReason: string | null;
  /** 已经接过但这次会多接几镜（新出片的）—— 按钮文案据此提示「更新成片」。 */
  composeMissingClips: boolean;
} {
  const { clips, skippedNoVideo } = scriptFilmClips(scriptNodeId);
  const readiness = scriptFilmReadiness(scriptNodeId);
  const compose = existingScriptComposeNode(scriptNodeId, true) ?? existingScriptComposeNode(scriptNodeId);
  const connected = compose
    ? new Set(
        useCanvasStore
          .getState()
          .edges.filter((edge) => edge.target === compose.id)
          .map((edge) => edge.source),
      )
    : new Set<string>();
  return {
    clipCount: clips.length,
    skippedNoVideo,
    hasCompose: Boolean(compose),
    ready: readiness.ready,
    blockingReason: readiness.reason,
    composeMissingClips: Boolean(compose) && clips.some((clip) => !connected.has(clip.id)),
  };
}
import { DIRECTOR_PLAN_PENDING_REASON } from './directorSequenceCoverage';
