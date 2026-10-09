// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import {
  CANVAS_NODE_TYPES,
  isImageGenNode,
  type ImageGenNodeData,
  type ImageSize,
  type ScriptImageGenConfig,
  type VideoDeliverySpec,
} from '@/features/canvas/domain/canvasNodes';
import {
  readScriptShotId,
  SCRIPT_SHOT_ID_NODE_FIELD,
} from '@/features/canvas/domain/scriptShotIdentity';
import { resolveAbsolutePosition, useCanvasStore } from '@/stores/canvasStore';
import {
  collectScriptAssetLedger,
  scriptAssetImageNodes,
  type ScriptAssetLedger,
} from './scriptAssets';
import { promptIsScriptOwned, stripScriptAssetAnchor } from './scriptAssetAnchor';
import {
  rearmImageNodePatch,
  scriptNodeIdForGroup,
  watchStoryboardSettle,
} from './storyboardSettle';
import {
  buildScriptShotSpecs,
  buildScriptStoryboardPlan,
  findStoryboardBlockOrigin,
  shotOwnReferenceUrls,
  storyboardRowReferenceSnapshot,
  STORYBOARD_IMAGE_CELL_HEIGHT,
  STORYBOARD_IMAGE_CELL_WIDTH,
  type ScriptShotSpec,
  type StoryboardRect,
} from './scriptStoryboard';
import {
  buildScriptRowSnapshots,
  computeStoryboardStaleness,
  scriptRowsOf,
  storyboardMemberSnapshot,
  type StoryboardMemberSnapshot,
} from './scriptStaleness';
import { STORYBOARD_EDGE_ROLE } from './scriptStoryboardMembers';
import { ensureScriptKeyframeImages } from './scriptKeyframeImages';
import {
  normalizeScriptDeliverySpec,
  resolveScriptDeliverySpec,
  scriptDeliverySpecsEqual,
} from './scriptShotVideos';

export { STORYBOARD_EDGE_ROLE } from './scriptStoryboardMembers';

/**
 * 「生成分镜」落盘：把脚本节点的分镜行派生成分镜图 + 分镜图组（对齐 LibTV）。
 *
 * 与 LibTV 官方 CLI `syncScriptStoryboardFromNode` 一致的次序：
 * ① 校验有分镜行 → ② 计算宫格与右侧落位 → ③ 建图片节点 → ④ 建分镜图组
 * → ⑤ 连线（脚本 → 组/成员）→ ⑥ 在脚本节点上记录分镜组 id。
 *
 * **出图**（`generateImages`）分两拍走，因为本产品的分镜组成员是 `hidden: true`、
 * 由组节点自己画缩略图，而**出图是图片节点挂载后看到 `canvas_auto_generate_once`
 * 才提交的** —— 隐藏成员永远不挂载、也就永远不会出图：
 * - 出图阶段：成员**先不并组**，以普通可见节点落在脚本节点右侧（在视口里 → 会
 *   挂载 → 会提交），用户能看着一张张出来；
 * - 全部落定后：`storyboardSettle` 把它们并回分镜图组，回到宫格画布。
 * 「只建节点」则直接并组，不需要出图，也就没有这个中间态。
 *
 * **重新生成**（脚本节点已关联分镜组时）：
 * - 行数与现有分镜图数量一致 → **原地重跑**未出图 / 失败的那几张（LibTV 的
 *   `storyboardRegenerateMessage` 就是这个语义），已出的图不动；重跑前先把组
 *   散开（`ungroupNode` 会把成员 `hidden: false` 放回原位），否则同上，隐藏成员
 *   连提交的机会都没有；
 * - 行数变了（脚本重生成过）→ 重建整组，避免行与图错位。
 */

export const DEFAULT_SCRIPT_IMAGE_GEN_CONFIG: ScriptImageGenConfig = {
  // LibTV 生成分镜的默认比例是 16:9，与画布分镜组的默认比例一致。
  aspectRatio: '16:9',
};

export interface GenerateScriptStoryboardParams {
  scriptNodeId: string;
  rows: FreezoneStoryScriptRow[];
  /**
   * 资产台账（第二刀）。有它时：参考图 = 角色 → 场景 → 道具（资产图）→ 参考帧兜底，
   * 且提示词前面会拼一段「资产图锚定」；没有它时行为与改动前逐字一致（只有角色图、
   * 无锚定块）。判过期的快照必须与这里用**同一份**台账，否则图刚出出来就报过期。
   */
  ledger?: ScriptAssetLedger;
  scriptTitle?: string | null;
  /** 脚本节点当前渲染尺寸（由节点传入，避免这里重复维护默认尺寸）。 */
  scriptSize: { width: number; height: number };
  config: ScriptImageGenConfig;
  /** 图片节点分辨率档位；默认 1K。 */
  imageSize?: ImageSize;
  /** 建完是否立刻出图（打 `canvas_auto_generate_once`）。默认 false＝只建节点。 */
  generateImages?: boolean;
}

export type GenerateScriptStoryboardResult =
  | {
      ok: true;
      /** created＝新组；rebuilt＝脚本行变了重建；rearmed＝原地重跑未出图的。 */
      mode: 'created' | 'rebuilt' | 'rearmed';
      groupId: string | null;
      nodeIds: string[];
      groupLabel: string;
      /** 本轮实际会去出图的张数。 */
      armed: number;
    }
  | { ok: false; reason: string };

/** 分镜图节点的数据：提示词 = 锚定块 + 分镜提示词；参考图 = 该镜全组；行标识绑行。 */
function imageNodeDataForShot(params: {
  name: string;
  prompt: string;
  model: string;
  aspectKey: string;
  deliverySpec: VideoDeliverySpec;
  imageSize: ImageSize;
  rowKey: string;
  /** 该镜自带的参考图**全组**（有序，见 {@link shotOwnReferenceUrls}）。 */
  referenceImageUrls: string[];
  /** 该镜引用的资产身份 / 版本 / 锁快照。 */
  assetRevisionSnapshot: string | null;
  creativeHandoff: ScriptShotSpec['creativeHandoff'];
  generateImages: boolean;
}): Partial<ImageGenNodeData> {
  return {
    label: params.name,
    displayName: params.name,
    prompt: params.prompt,
    model: params.model,
    size: params.imageSize,
    requestAspectRatio: params.deliverySpec.aspectRatio || params.aspectKey,
    deliverySpec: params.deliverySpec,
    count: 1,
    // 第 1 张仍是 referenceImageUrl（`@图片1` 的编号基线挂在它身上），整组另存
    // referenceImageUrls —— 提交时与上游图一起进 orderedReferenceUrls，
    // 多角色镜头不再只剩第一个角色（对齐 LibTV params.imageList 数组）。
    ...referenceGroupPatch(params.referenceImageUrls),
    // 行 ↔ 图 的关联键，等价于 LibTV 的 scriptRowHiddenUuid。
    [SCRIPT_SHOT_ID_NODE_FIELD]: params.rowKey,
    scriptRowKey: params.rowKey,
    // 派生时这一行的内容快照：脚本表格改过之后，靠它判这张图是否已过期
    // （见 scriptStaleness）。没有它就只能发现镜号变化，发现不了提示词变化。
    scriptRowPrompt: params.prompt,
    scriptRowReference: storyboardRowReferenceSnapshot(params.referenceImageUrls),
    scriptRowAssetSnapshot: params.assetRevisionSnapshot,
    scriptCreativeHandoff: params.creativeHandoff,
    // 既有机制：置真即由节点自身提交生成（跑完自动清掉）。
    canvas_auto_generate_once: params.generateImages,
  } as Partial<ImageGenNodeData>;
}

/**
 * 重出某一张分镜图时，把它的行快照（必要时连参考图组一起）对齐到当前脚本行。
 *
 * 快照必须当场对齐，否则那张图哪怕已经重跑，节点上留的还是旧快照，
 * 过期标记会一直亮着（用户看到的是「点了没用」）。
 *
 * 参考图组只在**节点上的参考图还归脚本所有**时才重写（`nodeOwnReferenceUrls` 与存的
 * `scriptRowReference` 快照一致 ⇒ 这些图是当初脚本行给的）。判据这样定是因为重跑也可能是
 * 想在用户手改过的参考图上重出：脚本没动、用户自己换了一张，重跑把人家那张顶掉属于静默
 * 毁数据。反之脚本换了角色图却不重写，这次重跑就仍拿旧角色图出图 —— 快照对齐了、图却按
 * 旧参考出，比不重跑更糟。
 *
 * **提示词里的锚定块同理，而且更微妙**：锚定块是我们替脚本烘进去的（「角色 张三 的参考图
 * 是 图片1」），资产图一多，旧块就成了错信息 —— 必须重写。但用户如果在分镜图节点上手改过
 * 提示词，那个块里夹着他的话，重写等于把手写内容一起冲掉。判据用
 * {@link promptIsScriptOwned}：剥掉锚定块后与行原文逐字相同 ⇒ 这句还是脚本给的，可以重写。
 */
function refreshRowSnapshotPatch(
  row: ScriptShotSpec,
  nodeData: Record<string, unknown> | undefined,
): Partial<ImageGenNodeData> {
  const ownReferenceUrls = shotOwnReferenceUrls(row);
  const nextSnapshot = storyboardRowReferenceSnapshot(ownReferenceUrls);
  const storedSnapshot =
    typeof nodeData?.scriptRowReference === 'string' ? nodeData.scriptRowReference : null;
  const nodeOwnSnapshot = storyboardRowReferenceSnapshot(nodeOwnReferenceUrls(nodeData));
  const referenceIsScriptOwned = nodeOwnSnapshot === storedSnapshot;
  const referenceChanged = storedSnapshot !== nextSnapshot && referenceIsScriptOwned;

  const currentNodePrompt = typeof nodeData?.prompt === 'string' ? nodeData.prompt : '';
  const storedRowPrompt =
    typeof nodeData?.scriptRowPrompt === 'string'
      ? stripScriptAssetAnchor(nodeData.scriptRowPrompt)
      : '';
  // 与行**原文**比（`row.basePrompt`），不是与带锚定块的那句比 —— 否则每次重建都会
  // 因为「锚定块位置/内容变了」判成「用户改过」，反而把该重写的块留在原地。
  const promptIsOurs = promptIsScriptOwned(currentNodePrompt, storedRowPrompt);
  const promptChanged = promptIsOurs && currentNodePrompt.trim() !== row.prompt.trim();

  return {
    ...(referenceChanged ? referenceGroupPatch(ownReferenceUrls) : {}),
    ...(promptChanged ? { prompt: row.prompt } : {}),
    [SCRIPT_SHOT_ID_NODE_FIELD]: row.rowKey,
    scriptRowKey: row.rowKey,
    scriptRowPrompt: row.prompt,
    scriptRowReference: nextSnapshot,
    scriptRowAssetSnapshot: row.assetRevisionSnapshot,
    scriptCreativeHandoff: row.creativeHandoff,
  };
}

/** 节点上现有的自带参考图组（新字段优先，回落到单张的 `referenceImageUrl`）。 */
function nodeOwnReferenceUrls(nodeData: Record<string, unknown> | undefined): string[] {
  const group = nodeData?.referenceImageUrls;
  if (Array.isArray(group)) {
    return group.filter((url): url is string => typeof url === 'string' && url.length > 0);
  }
  const single = nodeData?.referenceImageUrl;
  return typeof single === 'string' && single.length > 0 ? [single] : [];
}

/**
 * 参考图组的落盘补丁（第 1 张同时写 `referenceImageUrl`，其余进 `referenceImageUrls`）。
 *
 * 两个字段必须一起写：`referenceImageUrl` 是 `@图片1` 的编号基线、也是 UI 上「自身
 * 参考图」那一格显示的东西，漏写会让节点显示旧图而提交新图。
 */
function referenceGroupPatch(referenceImageUrls: string[]): Partial<ImageGenNodeData> {
  return {
    referenceImageUrl: referenceImageUrls[0] ?? null,
    referenceImageUrls,
  };
}

/** 分镜图节点：**图片**分组容器里的成员。 */
export function storyboardGroupMembers(groupNodeId: string) {
  const nodes = useCanvasStore.getState().nodes;
  return nodes.filter((node) => node.parentId === groupNodeId && isImageGenNode(node));
}

/**
 * 分镜图**自己产出**的图。
 *
 * 刻意不认 `referenceImageUrl`：分镜图节点上的参考图是脚本行的角色图/参考图，
 * 拿它当结果会让「还没出图」被判成已出图（重新生成不动手），批量下载也会把
 * 角色头像当成第 N 镜存下来。只有 imageUrl / previewImageUrl 才是这一镜的结果。
 */
export function storyboardMemberImageUrl(node: {
  data?: Record<string, unknown>;
}): string | null {
  const url = node.data?.imageUrl ?? node.data?.previewImageUrl;
  return typeof url === 'string' && url.length > 0 ? url : null;
}

/** 该分镜图是否需要出图：没有自己的图（或上次失败）就需要。 */
export function storyboardMemberNeedsImage(node: { data?: Record<string, unknown> }): boolean {
  if (!storyboardMemberImageUrl(node)) return true;
  return Boolean(node.data?.generationError);
}

/** 从节点集合里，按 id 顺序取出这批成员的派生快照。 */
function memberSnapshots(memberIds: string[]): StoryboardMemberSnapshot[] {
  const byId = new Map(useCanvasStore.getState().nodes.map((node) => [node.id, node] as const));
  return memberIds
    .map((nodeId) => byId.get(nodeId))
    .filter((node): node is NonNullable<typeof node> => Boolean(node))
    .map((node) => storyboardMemberSnapshot(node))
    .filter((snapshot): snapshot is StoryboardMemberSnapshot => snapshot != null);
}

/**
 * 这批分镜图里「点重新生成会真的动手」的成员：还没出图 / 上次失败的，
 * **加上脚本行内容已变而失效的**。
 *
 * 失效的也要算进来 —— 否则脚本表格里改完提示词再点「重新生成分镜图」，
 * 那几张被改过的图因为「已经有图了」而纹丝不动，正好是这条闸门要防的事。
 */
export function storyboardMemberIdsToRearm(
  memberIds: string[],
  rows: FreezoneStoryScriptRow[],
  ledger?: ScriptAssetLedger,
  deliverySpec?: VideoDeliverySpec | null,
  directorPlan?: FreezoneStoryDirectorPlan | null,
): string[] {
  const byId = new Map(useCanvasStore.getState().nodes.map((node) => [node.id, node] as const));
  const { staleNodeIdsSet } = computeStoryboardStaleness({
    members: memberSnapshots(memberIds),
    rows: buildScriptRowSnapshots(rows, ledger, directorPlan),
  });
  return memberIds.filter((nodeId) => {
    const node = byId.get(nodeId);
    if (!node) return false;
    if (
      deliverySpec &&
      !scriptDeliverySpecsEqual(
        normalizeScriptDeliverySpec(node.data?.deliverySpec),
        deliverySpec,
      )
    ) {
      return true;
    }
    if (staleNodeIdsSet.has(nodeId)) return true;
    return storyboardMemberNeedsImage(node);
  });
}

/**
 * 现有分镜图能否原地重跑（而不是按当前脚本重建整组）。
 *
 * 判据与 `generateScriptStoryboard` 的主分支必须完全一致：数量相同还不够，
 * 每张图都要带着当前仍存在的 rowKey。镜号整批换过时，数量虽然相同，但旧节点
 * 已经找不到对应新行；继续原地重跑只会写不上新快照，用户每点一次都仍显示过期。
 */
export function storyboardMembersCanRearm(
  memberIds: string[],
  rows: FreezoneStoryScriptRow[],
  ledger?: ScriptAssetLedger,
  deliverySpec?: VideoDeliverySpec | null,
  directorPlan?: FreezoneStoryDirectorPlan | null,
): boolean {
  if (memberIds.length === 0 || memberIds.length !== rows.length) return false;
  const byId = new Map(useCanvasStore.getState().nodes.map((node) => [node.id, node] as const));
  const currentRowKeys = new Set(
    buildScriptShotSpecs(rows, ledger, deliverySpec, directorPlan).map((shot) => shot.rowKey),
  );
  const existingByKey = new Map(
    memberIds
      .map((nodeId) => {
        const node = byId.get(nodeId);
        const rowKey = readScriptShotId(node?.data);
        return rowKey ? ([rowKey, nodeId] as const) : null;
      })
      .filter((entry): entry is readonly [string, string] => entry !== null),
  );
  return (
    existingByKey.size === memberIds.length &&
    [...existingByKey.keys()].every((rowKey) => currentRowKeys.has(rowKey)) &&
    [...existingByKey.values()].every((nodeId) => {
      if (!deliverySpec) return true;
      const node = byId.get(nodeId);
      return scriptDeliverySpecsEqual(
        normalizeScriptDeliverySpec(node?.data?.deliverySpec),
        deliverySpec,
      );
    })
  );
}

/**
 * 重跑某张分镜图时的补丁：清掉上次失败、重新排队，并把行快照对齐到**当前**的脚本行。
 *
 * 快照必须当场对齐，否则那张图哪怕已经重跑，节点上留的还是旧快照，
 * 过期标记会一直亮着（用户看到的是「点了没用」）。
 */
export function rearmStoryboardMemberPatch(
  nodeId: string,
  shots: ScriptShotSpec[],
): Partial<ImageGenNodeData> {
  const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === nodeId);
  const rowKey = readScriptShotId(node?.data);
  const shot = rowKey ? shots.find((candidate) => candidate.rowKey === rowKey) : undefined;
  return {
    ...rearmImageNodePatch(),
    ...(shot
      ? {
          ...refreshRowSnapshotPatch(shot, node?.data),
          ...(shot.deliverySpec
            ? {
                requestAspectRatio: shot.deliverySpec.aspectRatio,
                deliverySpec: shot.deliverySpec,
              }
            : {}),
        }
      : {}),
  };
}

export function generateScriptStoryboard(
  params: GenerateScriptStoryboardParams,
): GenerateScriptStoryboardResult {
  const store = useCanvasStore.getState();
  const scriptNode = store.nodes.find((node) => node.id === params.scriptNodeId);
  if (!scriptNode) {
    return { ok: false, reason: '脚本节点已不存在' };
  }
  if (scriptNode.data?.scriptDirectorPlanNeedsSync === true) return { ok: false, reason: DIRECTOR_PLAN_PENDING_REASON };
  const model = (params.config.model ?? '').trim();
  if (model.length === 0) {
    return { ok: false, reason: '请先选择分镜图模型' };
  }
  const aspectKey =
    (params.config.aspectRatio ?? DEFAULT_SCRIPT_IMAGE_GEN_CONFIG.aspectRatio ?? '16:9').trim();
  const deliverySpec =
    normalizeScriptDeliverySpec(params.config.deliverySpec) ??
    resolveScriptDeliverySpec(aspectKey);
  const imageSize: ImageSize = params.imageSize ?? '1K';
  const generateImages = params.generateImages === true;
  const shots = params.rows;
  const directorPlan = (scriptNode.data?.scriptResult as { director_plan?: FreezoneStoryDirectorPlan | null } | undefined)?.director_plan;
  // 台账只算一次，判过期 / 要重跑 / 写行快照三处共用 —— 见文件头「第三刀」说明。
  // React 入口传的是已订阅台账；直接调用 / 单测不传时必须现算同一份，
  // 否则生成的资产版本快照与后续 staleness 重算会差一版，刚生成就自判过期。
  const ledger =
    params.ledger
    ?? collectScriptAssetLedger(params.rows, scriptAssetImageNodes(params.scriptNodeId));

  const nodeMap = new Map(store.nodes.map((node) => [node.id, node] as const));
  const scriptAbsolute = resolveAbsolutePosition(scriptNode, nodeMap);
  const scriptRect: StoryboardRect = {
    x: scriptAbsolute.x,
    y: scriptAbsolute.y,
    width: params.scriptSize.width,
    height: params.scriptSize.height,
  };
  const groupLabel = buildScriptStoryboardPlan({
    rows: shots.slice(0, 1),
    ledger,
    deliverySpec,
    directorPlan,
    scriptTitle: params.scriptTitle,
    origin: { x: 0, y: 0 },
    cellWidth: STORYBOARD_IMAGE_CELL_WIDTH,
    cellHeight: STORYBOARD_IMAGE_CELL_HEIGHT,
  });
  if (!groupLabel.ok) {
    return { ok: false, reason: groupLabel.reason };
  }
  const resolvedGroupLabel = groupLabel.groupLabel;

  const previousGroupId =
    typeof scriptNode.data?.linkedImageGroupId === 'string'
      ? scriptNode.data.linkedImageGroupId
      : null;
  const existingGroup =
    previousGroupId && store.nodes.some((node) => node.id === previousGroupId)
      ? store.nodes.find((node) => node.id === previousGroupId)
      : null;
  // 没有组也可能是「上一轮出图散开着」：靠血缘边（role: storyboard）认出这批图。
  // 注意并组时 store 会把成员两端的边改指到组节点上（并留 __sbOrigTarget），
  // 所以这里只认「目标仍是图片节点」的那些边。
  const linkedShotIds = store.edges
    .filter((edge) => edge.source === params.scriptNodeId
      && edge.data?.role === STORYBOARD_EDGE_ROLE)
    .map((edge) => edge.target)
    .filter((target) => {
      const node = store.nodes.find((candidate) => candidate.id === target);
      return Boolean(node && isImageGenNode(node));
    });

  // ===== 原地重跑：行数与现有分镜图一致时，只重跑未出图 / 失败的那几张 =====
  const existingShotIds = existingGroup ? storyboardGroupMembers(existingGroup.id).map(
    (member) => member.id,
  ) : linkedShotIds;
  const currentShotSpecs = buildScriptShotSpecs(shots, ledger, deliverySpec, directorPlan);
  // 行数相同不等于仍是同一批行。镜号整批换过时（例如 1/2 → 11/12）也必须重建：
  // 原地重跑按旧 rowKey 找不到新行，既不更新快照也不更新身份，结果每次点都仍显示过期。
  const canRearm = storyboardMembersCanRearm(
    existingShotIds,
    shots,
    ledger,
    deliverySpec,
    directorPlan,
  );
  if (canRearm) {
    // 重跑对象＝未出图 / 失败 / 脚本行已变而失效的（见 storyboardMemberIdsToRearm）。
    const pending = storyboardMemberIdsToRearm(
      existingShotIds,
      shots,
      ledger,
      deliverySpec,
      directorPlan,
    );
    // 出图前必须让成员可见：隐藏成员不挂载 → 不提交（见文件头注释）。
    let groupForSettle = existingGroup?.id ?? null;
    if (generateImages && existingGroup && pending.length > 0) {
      useCanvasStore.getState().ungroupNode(existingGroup.id);
      groupForSettle = null;
    }
    useCanvasStore.getState().updateNodeData(params.scriptNodeId, {
      imageGenConfig: {
        ...DEFAULT_SCRIPT_IMAGE_GEN_CONFIG,
        ...params.config,
        model,
        aspectRatio: deliverySpec.aspectRatio,
        deliverySpec,
      },
      ...(groupForSettle ? { linkedImageGroupId: groupForSettle } : {}),
    });
    if (generateImages && pending.length > 0) {
      pending.forEach((nodeId) => {
        useCanvasStore.getState().updateNodeData(nodeId, {
          model,
          ...rearmStoryboardMemberPatch(nodeId, currentShotSpecs),
        });
      });
      watchStoryboardSettle({
        scriptNodeId: params.scriptNodeId,
        memberIds: existingShotIds,
        groupLabel: resolvedGroupLabel,
        aspectKey: deliverySpec.aspectRatio,
        generateKeyframes: true,
      });
    }
    const keyframeIds = generateImages && pending.length === 0
      ? ensureScriptKeyframeImages(params.scriptNodeId, true) : [];
    return {
      ok: true,
      mode: 'rearmed',
      groupId: keyframeIds.length > 0 ? null : groupForSettle,
      nodeIds: [...existingShotIds, ...keyframeIds],
      groupLabel: resolvedGroupLabel,
      armed: generateImages ? pending.length + keyframeIds.length : 0,
    };
  }
  if (existingGroup) {
    // 行数变了 → 重建：先删旧组（连带成员），再算落位。
    // 顺序很关键 —— 反过来的话旧分镜组会被当成障碍物，新组每次被顶到它下面，
    // 连着重生成就会一路往下漂（真机上漂到过 4600px 开外）。
    useCanvasStore.getState().deleteNodes([existingGroup.id]);
  } else if (linkedShotIds.length > 0) {
    // 散开着的那批（上一轮出图还没收拢 / 收拢前被改过行）：同样先清掉再重排。
    useCanvasStore.getState().deleteNodes(linkedShotIds);
  }

  const liveState = useCanvasStore.getState();
  const liveNodeMap = new Map(liveState.nodes.map((node) => [node.id, node] as const));
  const rectOf = (nodeId: string): StoryboardRect | null => {
    const node = liveNodeMap.get(nodeId);
    if (!node) return null;
    const absolute = resolveAbsolutePosition(node, liveNodeMap);
    return {
      x: absolute.x,
      y: absolute.y,
      width: node.measured?.width ?? STORYBOARD_IMAGE_CELL_WIDTH,
      height: node.measured?.height ?? STORYBOARD_IMAGE_CELL_HEIGHT,
    };
  };
  const occupied: StoryboardRect[] = liveState.nodes
    .map((node) => rectOf(node.id))
    .filter((rect): rect is StoryboardRect => Boolean(rect));
  // LibTV 的右移判据只看脚本节点连出去的下游：落点被下游压住就排到它们下面。
  const downstream: StoryboardRect[] = liveState.edges
    .filter((edge) => edge.source === params.scriptNodeId)
    .map((edge) => rectOf(edge.target))
    .filter((rect): rect is StoryboardRect => Boolean(rect));

  // 先用整块尺寸找右侧空位，再按宫格展开：单个节点不做碰撞挪动，否则挪动会打乱
  // 「从左到右、从上到下」的读取顺序（分镜组正是按位置重排成员）。
  const shotCount = shots.length;
  const cols = Math.ceil(Math.sqrt(Math.max(1, shotCount)));
  const gridRows = Math.ceil(Math.max(1, shotCount) / cols);
  const gridWidth = cols * STORYBOARD_IMAGE_CELL_WIDTH + (cols - 1) * 48;
  const gridHeight = gridRows * STORYBOARD_IMAGE_CELL_HEIGHT + (gridRows - 1) * 32;
  const blockOrigin = findStoryboardBlockOrigin({
    script: scriptRect,
    gridWidth,
    gridHeight,
    downstream,
    occupied,
  });

  const plan = buildScriptStoryboardPlan({
    rows: shots,
    ledger,
    deliverySpec,
    directorPlan,
    scriptTitle: params.scriptTitle,
    origin: blockOrigin,
    cellWidth: STORYBOARD_IMAGE_CELL_WIDTH,
    cellHeight: STORYBOARD_IMAGE_CELL_HEIGHT,
  });
  if (!plan.ok) {
    return { ok: false, reason: plan.reason };
  }

  const nodeIds: string[] = [];
  plan.shots.forEach((shot, index) => {
    const position = plan.positions[index] ?? blockOrigin;
    // 该镜自带的参考图**全组**（该行所有角色图）。此前只取 [0]，双人镜头的第二个
    // 人物整个丢失 —— LibTV 的 params.imageList 是数组，我们照它的语义补齐。
    const referenceImageUrls = shotOwnReferenceUrls(shot);
    const newNodeId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.imageGen,
      position,
      imageNodeDataForShot({
        name: shot.name,
        prompt: shot.prompt,
        model,
        aspectKey,
        deliverySpec,
        imageSize,
        rowKey: shot.rowKey,
        referenceImageUrls,
        assetRevisionSnapshot: shot.assetRevisionSnapshot,
        creativeHandoff: shot.creativeHandoff,
        generateImages,
      }),
    );
    if (newNodeId) nodeIds.push(newNodeId);
  });

  if (nodeIds.length === 0) {
    return { ok: false, reason: '分镜图节点创建失败' };
  }

  // 连线：脚本 → 每张分镜图。
  // LibTV 连的是「脚本 → 分镜图组」一条边，但本产品的分组容器不参与连线
  // （groupNode 的 connectivity 两个 handle 都是 false），因此按我们的血缘模型
  // 逐镜连边，脚本节点上再用 linkedImageGroupId 记录整组 ——
  // 顺带这批边也是「哪些图是本功能派生的」的唯一标记（出图散开期间组还不存在）。
  nodeIds.forEach((shotNodeId) => {
    useCanvasStore.getState().addEdgeWithData(
      params.scriptNodeId,
      shotNodeId,
      {
        edgeKind: 'mainline_data',
        propagates: true,
        role: STORYBOARD_EDGE_ROLE,
        label: '分镜图',
      },
      {
        id: `edge_${params.scriptNodeId}_to_${shotNodeId}_storyboard`,
        sourceHandle: 'source',
        targetHandle: 'target',
      },
    );
  });

  useCanvasStore.getState().updateNodeData(params.scriptNodeId, {
    imageGenConfig: {
      ...DEFAULT_SCRIPT_IMAGE_GEN_CONFIG,
      ...params.config,
      model,
      aspectRatio: deliverySpec.aspectRatio,
      deliverySpec,
    },
  });

  if (generateImages) {
    // 出图阶段先不并组（见文件头注释）：成员留在视口里当普通节点出图，出完再收。
    watchStoryboardSettle({
      scriptNodeId: params.scriptNodeId,
      memberIds: nodeIds,
      groupLabel: plan.groupLabel,
      aspectKey: deliverySpec.aspectRatio,
      generateKeyframes: true,
    });
    return {
      ok: true,
      mode: existingGroup || linkedShotIds.length > 0 ? 'rebuilt' : 'created',
      groupId: null,
      nodeIds,
      groupLabel: plan.groupLabel,
      armed: nodeIds.length,
    };
  }

  // 2 张以上才成组（store 的分镜组要求至少 2 个成员）；单镜时只留一个图片节点。
  let groupId: string | null = null;
  if (nodeIds.length >= 2) {
    groupId = useCanvasStore.getState().mergeStoryboardGroup(nodeIds);
    if (groupId) {
      useCanvasStore.getState().updateNodeData(groupId, {
        label: plan.groupLabel,
        displayName: plan.groupLabel,
      });
      useCanvasStore.getState().setStoryboardGroupConfig(groupId, {
        aspectKey: deliverySpec.aspectRatio,
      });
    }
  }
  useCanvasStore.getState().updateNodeData(params.scriptNodeId, {
    linkedImageGroupId: groupId,
  });

  return {
    ok: true,
    mode: existingGroup || linkedShotIds.length > 0 ? 'rebuilt' : 'created',
    groupId,
    nodeIds,
    groupLabel: plan.groupLabel,
    armed: 0,
  };
}

/**
 * 分镜图组「重新生成分镜图」：把组内**未出图 / 失败**的成员重新排队出图。
 * 返回实际排队的张数（0 表示都已出图）。
 *
 * 与脚本节点上的重新生成同一条路：先把组散开成可见节点（隐藏成员不挂载、不提交），
 * 排队出图，全部落定后再自动并回分镜图组。组 id 会变，所以脚本节点的
 * `linkedImageGroupId` 由收拢那一步回写。
 */
export function regenerateStoryboardGroupImages(groupNodeId: string): {
  ok: boolean;
  armed: number;
  stale?: number;
  reason?: string;
  focusNodeIds?: string[];
} {
  const store = useCanvasStore.getState();
  const group = store.nodes.find((node) => node.id === groupNodeId);
  if (!group) return { ok: false, armed: 0, reason: '分镜组已不存在' };
  const members = storyboardGroupMembers(groupNodeId);
  if (members.length === 0) return { ok: true, armed: 0 };

  const label =
    typeof group.data?.label === 'string' && group.data.label.length > 0
      ? group.data.label
      : '分镜图';
  const aspectKey =
    typeof group.data?.storyboardAspect === 'string' ? group.data.storyboardAspect : '16:9';
  const scriptNodeId = scriptNodeIdForGroup(groupNodeId);
  const scriptNode = scriptNodeId
    ? useCanvasStore.getState().nodes.find((node) => node.id === scriptNodeId)
    : undefined;
  if (scriptNode?.data?.scriptDirectorPlanNeedsSync === true) {
    return { ok: false, armed: 0, reason: DIRECTOR_PLAN_PENDING_REASON };
  }
  const scriptImageConfig =
    scriptNode?.data?.imageGenConfig &&
    typeof scriptNode.data.imageGenConfig === 'object' &&
    !Array.isArray(scriptNode.data.imageGenConfig)
      ? (scriptNode.data.imageGenConfig as ScriptImageGenConfig)
      : undefined;
  const deliverySpec =
    normalizeScriptDeliverySpec(scriptImageConfig?.deliverySpec) ??
    resolveScriptDeliverySpec(aspectKey);
  const memberIds = members.map((member) => member.id);
  // 脚本行变了之后，「重新生成」要连**失效的那些**一起重跑，不能只补没图的。
  const rows = scriptNodeId ? scriptRowsOf(scriptNodeId) : [];
  const directorPlan = scriptNodeId
    ? (scriptNode?.data?.scriptResult as { director_plan?: FreezoneStoryDirectorPlan | null } | undefined)?.director_plan
    : undefined;
  // 台账**只算一次**：判过期、判要重跑、重写行快照三处必须吃同一份，
  // 否则「资产图刚生成 → 参考图组变了」的判断会在三处各差一点点。
  // 资产图必须按脚本归属认领。省略 owner 会把其它脚本的同名角色图也算进来，
  // 组级重跑随后会误判参考图已变，并把别的脚本的图写进这一镜。
  const ledger = scriptNodeId
    ? collectScriptAssetLedger(rows, scriptAssetImageNodes(scriptNodeId))
    : undefined;
  const pending = scriptNodeId
    ? storyboardMemberIdsToRearm(memberIds, rows, ledger, deliverySpec, directorPlan)
    : members.filter(storyboardMemberNeedsImage).map((member) => member.id);
  if (pending.length === 0) {
    const keyframeIds = scriptNodeId ? ensureScriptKeyframeImages(scriptNodeId, true) : [];
    return { ok: true, armed: keyframeIds.length, stale: 0, focusNodeIds: keyframeIds };
  }

  const staleCount = scriptNodeId
    ? computeStoryboardStaleness({
        members: memberSnapshots(memberIds),
        rows: buildScriptRowSnapshots(rows, ledger, directorPlan),
      }).staleNodeIds.length
    : 0;

  // 先散开再排队：隐藏成员不挂载 → 不提交。
  useCanvasStore.getState().ungroupNode(groupNodeId);
  const shotSpecs = scriptNodeId ? buildScriptShotSpecs(rows, ledger, deliverySpec, directorPlan) : [];
  pending.forEach((memberId) => {
    useCanvasStore.getState().updateNodeData(
      memberId,
      scriptNodeId ? rearmStoryboardMemberPatch(memberId, shotSpecs) : rearmImageNodePatch(),
    );
  });
  watchStoryboardSettle({ scriptNodeId, memberIds, groupLabel: label, aspectKey, generateKeyframes: true });
  return { ok: true, armed: pending.length, stale: staleCount };
}
import { DIRECTOR_PLAN_PENDING_REASON } from './directorSequenceCoverage';
