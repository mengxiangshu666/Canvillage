// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import { isImageGenNode, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import { useCanvasStore } from '@/stores/canvasStore';
import type { ScriptAssetLedger } from './scriptAssets';
import { collectScriptAssetLedger, scriptAssetImageNodes } from './scriptAssets';
import {
  buildScriptShotSpecs,
  shotOwnReferenceUrls,
  storyboardRowReferenceSnapshot,
} from './scriptStoryboard';
import { storyboardImageNodesForScript } from './scriptStoryboardMembers';

/**
 * 脚本 → 分镜图 的级联失效判定（对齐 LibTV 的硬规则）。
 *
 * LibTV 官方节点规范把这条写成规则：「脚本已变更须重生成 storyboard 再批量出视频」
 * （见 `NODE_FUNCTIONS_MATRIX.md` §7.2）。我们的画布一直只有写、没有读：
 * 派生分镜图时把行键 `scriptRowKey` 写在节点上，但全仓没有任何地方比对过它，
 * 于是脚本表格里改完提示词，屏幕上还是旧图、也没有任何提示。
 *
 * 判定口径（三条互相独立，任一命中即过期）：
 * - **行不在了**：分镜图的行键在当前脚本行里找不到 —— 镜号被改过、行被删过；
 * - **提示词变了**：该行现在的图片提示词与派生时留下的快照不一致；
 * - **参考图变了**：该行首张参考图（角色图 / 参考帧）与快照不一致 —— 换角色图同样
 *   会让已出的图不再是「这一镜该有的样子」。
 *
 * 老画布上的分镜图没有快照字段（T-013 之前派生），只做第一项判定：行键当时也写了，
 * 所以镜号变化仍能判出来，但不会因为「没快照」而集体误报过期。
 */

/** 一行脚本在判定时用得到的事实。 */
export interface ScriptRowSnapshot {
  rowKey: string;
  /** 该项的图片提示词（`shot_prompt || visual_description`）。 */
  prompt: string;
  /** 该行参考图**全组**按序拼成的快照串（角色图优先，空则参考帧）；空组为 null。 */
  reference: string | null;
  /** 该行引用资产的 id / revision / hash / locks 快照；只有无身份参考帧时为 null。 */
  assetRevision: string | null;
}

/** 一张分镜图上留下的派生快照；`undefined` 表示老节点没有这个字段。 */
export interface StoryboardMemberSnapshot {
  nodeId: string;
  rowKey: string | null;
  prompt: string | null | undefined;
  reference: string | null | undefined;
  assetRevision?: string | null | undefined;
}

/**
 * 单张分镜图过期的原因。
 *
 * 只有**逐节点**可判的三条。曾经还有第四个 `'row-count-changed'`，但它是「组张数 ≠
 * 脚本行数」这个**组级**事实、按成员判不出来，`computeStoryboardStaleness` 从来没产出过
 * 它（描述函数里那一支永远走不到）。组级事实走同级的 `rowCountChanged` 布尔量单独说，
 * 不混进逐节点原因里。
 */
export type StoryboardStaleReason =
  | 'row-missing'
  | 'prompt-changed'
  | 'reference-changed'
  | 'asset-revision-changed';

export interface StoryboardStaleness {
  /** 过期的分镜图节点 id（保持传入顺序）。 */
  staleNodeIds: string[];
  /** 给 O(1) 查询用的同一集合。 */
  staleNodeIdsSet: Set<string>;
  /** 逐节点原因，UI 提示用。 */
  reasons: Map<string, StoryboardStaleReason[]>;
  /** 脚本行数与分镜图张数不一致 —— 这个只能回脚本节点重建，单组重出治不了。 */
  rowCountChanged: boolean;
}

export const EMPTY_STALENESS: StoryboardStaleness = {
  staleNodeIds: [],
  staleNodeIdsSet: new Set(),
  reasons: new Map(),
  rowCountChanged: false,
};

/** 当前脚本行 → 判定快照。行键与「生成分镜」用的完全同一套（含 `#n` 去重）。 */
export function buildScriptRowSnapshots(
  rows: FreezoneStoryScriptRow[],
  ledger?: ScriptAssetLedger,
  directorPlan?: FreezoneStoryDirectorPlan | null,
): ScriptRowSnapshot[] {
  // 没传订阅台账时也要用同一份纯推导台账：否则「生成分镜」写了资产版本快照，
  // 判过期却按空资产重算，刚生成就会自己判 stale。React / 重跑入口仍传带画布认领的 ledger。
  const activeLedger = ledger ?? collectScriptAssetLedger(rows);
  return buildScriptShotSpecs(rows, activeLedger, undefined, directorPlan).map((shot) => ({
    rowKey: shot.rowKey,
    prompt: shot.prompt,
    // 与「生成分镜」写节点时同一口径：该行全部参考图（角色 → 场景 → 道具 → 参考帧
    // 兜底），整组按序拼接成快照串。
    reference: storyboardRowReferenceSnapshot(shotOwnReferenceUrls(shot)),
    assetRevision: shot.assetRevisionSnapshot,
  }));
}

/**
 * 纯判定：给定分镜图快照与当前脚本行快照，算出哪些图过期。
 *
 * 配对用**行键查表**而不是下标：分镜组按位置重排成员，下标顺序与行序在用户拖动过
 * 成员之后就不再可信；行键是派生时按行写死的，才是真正的身份。
 */
export function computeStoryboardStaleness(params: {
  members: StoryboardMemberSnapshot[];
  rows: ScriptRowSnapshot[];
}): StoryboardStaleness {
  const { members, rows } = params;
  const rowByKey = new Map(rows.map((row) => [row.rowKey, row] as const));
  const staleNodeIds: string[] = [];
  const reasons = new Map<string, StoryboardStaleReason[]>();

  for (const member of members) {
    const memberReasons: StoryboardStaleReason[] = [];
    const row = member.rowKey ? rowByKey.get(member.rowKey) : undefined;
    if (member.rowKey && !row) {
      memberReasons.push('row-missing');
    } else if (row) {
      // 只有留过快照的才比内容：老节点（undefined）不因为缺快照被误判。
      if (member.prompt !== undefined && member.prompt !== row.prompt) {
        memberReasons.push('prompt-changed');
      }
      if (member.reference !== undefined && member.reference !== row.reference) {
        memberReasons.push('reference-changed');
      }
      if (member.assetRevision !== undefined && member.assetRevision !== row.assetRevision) {
        memberReasons.push('asset-revision-changed');
      }
    }
    if (memberReasons.length === 0) continue;
    staleNodeIds.push(member.nodeId);
    reasons.set(member.nodeId, memberReasons);
  }

  return {
    staleNodeIds,
    staleNodeIdsSet: new Set(staleNodeIds),
    reasons,
    rowCountChanged: members.length > 0 && members.length !== rows.length,
  };
}

/** 从节点 data 里读出该分镜图的派生快照。非分镜图（无行键）返回 null。 */
export function storyboardMemberSnapshot(
  node: Pick<CanvasNode, 'id' | 'data'>,
): StoryboardMemberSnapshot | null {
  const data = (node.data ?? {}) as Record<string, unknown>;
  const rowKey = readScriptShotId(data);
  if (!rowKey) return null;
  const readOptional = (key: string): string | null | undefined => {
    const value = data[key];
    if (value === undefined) return undefined;
    return typeof value === 'string' ? value : null;
  };
  return {
    nodeId: node.id,
    rowKey,
    prompt: readOptional('scriptRowPrompt'),
    reference: readOptional('scriptRowReference'),
    assetRevision: readOptional('scriptRowAssetSnapshot'),
  };
}

function memberSnapshotsOfNodes(nodes: readonly CanvasNode[]): StoryboardMemberSnapshot[] {
  return [...nodes]
    .sort((left, right) => left.position.y - right.position.y || left.position.x - right.position.x)
    .map((node) => storyboardMemberSnapshot(node))
    .filter((snapshot): snapshot is StoryboardMemberSnapshot => snapshot != null);
}

/** 从一个分组容器里收集成员快照（按 y/x 位置排序 —— 与分组本身的重排顺序一致）。 */
function memberSnapshotsInGroup(groupNodeId: string): StoryboardMemberSnapshot[] {
  return memberSnapshotsOfNodes(
    useCanvasStore
      .getState()
      .nodes.filter((node) => node.parentId === groupNodeId && isImageGenNode(node)),
  );
}

/** 读脚本节点的分镜行（与 ScriptNode 用的是同一份 `data.scriptResult.rows`）。 */
export function scriptRowsOf(scriptNodeId: string): FreezoneStoryScriptRow[] {
  const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === scriptNodeId);
  const result = node?.data?.scriptResult as { rows?: unknown } | undefined;
  return Array.isArray(result?.rows) ? (result.rows as FreezoneStoryScriptRow[]) : [];
}

function scriptDirectorPlanOf(scriptNodeId: string): FreezoneStoryDirectorPlan | null | undefined {
  const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === scriptNodeId);
  return (node?.data?.scriptResult as { director_plan?: FreezoneStoryDirectorPlan | null } | undefined)?.director_plan;
}

/**
 * 读脚本节点的资产台账。
 *
 * 台账是从行 + 画布上的资产图节点算出来的，所以判过期前必须重算一遍：**新生成一张
 * 资产图会让相关分镜图立刻过期**（参考图组变了），这正是「资产图接上以后」应有的行为。
 * 老画布没有任何资产图节点，算出来与传统口径逐字一致。
 */
export function scriptAssetLedgerOf(scriptNodeId: string): ScriptAssetLedger {
  return collectScriptAssetLedger(scriptRowsOf(scriptNodeId), scriptAssetImageNodes(scriptNodeId));
}

/**
 * 脚本节点的分镜图是否已过期。无分镜组 / 无行时返回空判定。
 *
 * `ledger` 由调用方传时以它为准：React 侧用 `useScriptAssetLedger` 订阅了资产图落定，
 * 而本函数是从 store 现读的（没有订阅），不传的话「刚生成一张资产图」不会让这张表重算，
 * 过期标记会晚一步才亮。
 */
export function storyboardStalenessForScript(
  scriptNodeId: string,
  ledger?: ScriptAssetLedger,
): StoryboardStaleness {
  const members = memberSnapshotsOfNodes(storyboardImageNodesForScript(scriptNodeId));
  if (members.length === 0) return EMPTY_STALENESS;
  return computeStoryboardStaleness({
    members,
    rows: buildScriptRowSnapshots(scriptRowsOf(scriptNodeId), ledger ?? scriptAssetLedgerOf(scriptNodeId), scriptDirectorPlanOf(scriptNodeId)),
  });
}

/** 分镜图组自身的过期判定（工具栏用；组 id 反查脚本节点拿行）。 */
export function storyboardStalenessForGroup(
  groupNodeId: string,
  scriptNodeId: string | null,
): StoryboardStaleness {
  if (!scriptNodeId) return EMPTY_STALENESS;
  return computeStoryboardStaleness({
    members: memberSnapshotsInGroup(groupNodeId),
    rows: buildScriptRowSnapshots(scriptRowsOf(scriptNodeId), scriptAssetLedgerOf(scriptNodeId), scriptDirectorPlanOf(scriptNodeId)),
  });
}

/** 把逐节点原因翻成一句给人看的话（脚本节点 / 分镜组两处共用）。 */
export function describeStaleReasons(reasons: StoryboardStaleReason[]): string {
  const parts: string[] = [];
  if (reasons.includes('row-missing')) parts.push('镜号已变或该行已被删除');
  if (reasons.includes('prompt-changed')) parts.push('提示词已改');
  if (reasons.includes('reference-changed')) parts.push('参考图已换');
  if (reasons.includes('asset-revision-changed')) parts.push('引用资产版本已变');
  return parts.join('、');
}
