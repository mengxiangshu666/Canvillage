// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { type ImageGenNodeData } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { storyboardMemberNeedsImage } from './generateScriptStoryboard';
import { ensureScriptKeyframeImages } from './scriptKeyframeImages';

/**
 * 分镜图「出图期间散开、出完再收回分镜图组」。
 *
 * 为什么必须这样：本产品把分镜组成员设成 `hidden: true`，由组节点自己画缩略图；
 * 而画布开了 `onlyRenderVisibleElements`（视口外的节点不挂载）。出图是**图片节点
 * 自己挂载后**看到 `canvas_auto_generate_once` 才提交的 —— 隐藏成员永远不挂载，
 * 于是「生成分镜」在建完组之后一张都不会真的去出图（这是真机实测出来的：组和成员
 * 都建好了、标记也打上了，但成员压根没进 DOM，出图请求从未发出）。
 *
 * 所以出图阶段先把成员**散开成普通可见节点**（它们就在视口里 → 会挂载 → 会提交），
 * 等每一张都落定（出图成功或失败）之后，再收回分镜图组，回到用户看到的那套
 * 宫格画布。散开期间用户能看着一张张出来，比闷着不动更诚实。
 */

export interface StoryboardSettlePlan {
  /** 脚本节点（用于收拢后回写 linkedImageGroupId）；非脚本派生的分镜组传 null。 */
  scriptNodeId: string | null;
  /**
   * 非脚本源节点（目前只有视频故事节点）：收拢后把组 id 回写到它的
   * `linkedShotGroupId`。与 scriptNodeId 是两条独立的回写路径，一个源节点
   * 只会有其中一条。
   */
  ownerNodeId?: string | null;
  memberIds: string[];
  groupLabel: string;
  aspectKey: string;
  generateKeyframes?: boolean;
}

/** 已登记的收拢计划：按「这一批成员」去重，重复登记不叠加订阅。 */
const plans = new Map<string, StoryboardSettlePlan>();
let unsubscribe: (() => void) | null = null;
let safetyTimer: ReturnType<typeof setTimeout> | null = null;

/** 兜底时间：成员长时间没有落定（上游卡住 / 用户放着不管）也别永远散着。 */
const SETTLE_SAFETY_MS = 20 * 60 * 1000;

function planKey(memberIds: string[]): string {
  return [...memberIds].sort().join('|');
}

/** 每一张都有了自己的图，或者带着失败信息落定 —— 这批就算跑完了。 */
export function storyboardBatchSettled(memberIds: string[]): boolean {
  const nodes = useCanvasStore.getState().nodes;
  const members = memberIds
    .map((nodeId) => nodes.find((node) => node.id === nodeId))
    .filter((node): node is NonNullable<typeof node> => Boolean(node));
  if (members.length === 0) return false;
  return members.every(member => !member.data.isGenerating && !member.data.canvas_auto_generate_once
    && (Boolean(member.data.generationError) || !storyboardMemberNeedsImage(member)));
}

/**
 * 收拢：把散开的成员并回分镜图组，并把组 id 写回脚本节点。
 * 不够 2 个成员时不成组（与生成时一致），只把组 id 清掉。
 */
export function collapseStoryboardMembers(plan: StoryboardSettlePlan): string | null {
  const store = useCanvasStore.getState();
  const aliveIds = plan.memberIds.filter((nodeId) =>
    store.nodes.some((node) => node.id === nodeId),
  );
  if (aliveIds.length !== plan.memberIds.length) {
    // 有成员被删了：保持现状，别把半截的组收起来。
    return null;
  }
  let groupId: string | null = null;
  if (aliveIds.length >= 2) {
    // 已经成组（例如出图前它就是组、没被散开）就不用再并一次。
    const existing = store.nodes.find(
      (node) => node.type === 'groupNode' && aliveIds.every((nodeId) => {
        const member = store.nodes.find((candidate) => candidate.id === nodeId);
        return member?.parentId === node.id;
      }),
    );
    groupId = existing?.id ?? useCanvasStore.getState().mergeStoryboardGroup(aliveIds);
  } else {
    groupId = aliveIds[0] ?? null;
  }
  if (!groupId) return null;
  // 单镜时 groupId 就是那个图片节点，不该当成组去改名。
  if (aliveIds.length >= 2) {
    useCanvasStore.getState().updateNodeData(groupId, {
      label: plan.groupLabel,
      displayName: plan.groupLabel,
    });
    useCanvasStore.getState().setStoryboardGroupConfig(groupId, { aspectKey: plan.aspectKey });
  }
  if (plan.scriptNodeId) {
    useCanvasStore.getState().updateNodeData(plan.scriptNodeId, {
      linkedImageGroupId: aliveIds.length >= 2 ? groupId : null,
    });
  }
  if (plan.ownerNodeId) {
    useCanvasStore.getState().updateNodeData(plan.ownerNodeId, {
      linkedShotGroupId: aliveIds.length >= 2 ? groupId : null,
    });
  }
  return groupId;
}

/** 落定就收拢；调用方（订阅、测试）可以先判定再调用。 */
export function settleStoryboardIfReady(): boolean {
  let settled = false;
  for (const [key, plan] of [...plans.entries()]) {
    if (!storyboardBatchSettled(plan.memberIds)) continue;
    plans.delete(key);
    collapseStoryboardMembers(plan);
    if (plan.scriptNodeId && plan.generateKeyframes) {
      const queued = ensureScriptKeyframeImages(plan.scriptNodeId, true);
      if (queued.length) useCanvasStore.getState().requestFocusNodes(queued);
    }
    settled = true;
  }
  if (plans.size === 0) stopWatching();
  return settled;
}

function stopWatching() {
  unsubscribe?.();
  unsubscribe = null;
  if (safetyTimer) {
    clearTimeout(safetyTimer);
    safetyTimer = null;
  }
}

/**
 * 登记收拢计划：出图期间成员保持散开，全部落定后自动并回分镜图组。
 * 同一批成员重复登记只会留一份。
 */
export function watchStoryboardSettle(plan: StoryboardSettlePlan): void {
  plans.set(planKey(plan.memberIds), plan);
  if (!unsubscribe) {
    // 出图进度写在节点 data 上，订阅整棵 nodes 才能看到「一张张落定」。
    unsubscribe = useCanvasStore.subscribe(() => {
      settleStoryboardIfReady();
    });
  }
  if (!safetyTimer) {
    safetyTimer = setTimeout(() => {
      stopWatching();
    }, SETTLE_SAFETY_MS);
  }
}

/** 测试与异常路径用：放弃所有还没收拢的计划。 */
export function resetStoryboardSettle(): void {
  plans.clear();
  stopWatching();
}

/** 给已存在的分镜图组找回「它是哪个脚本节点派生的」，用于收拢后回写组 id。 */
export function scriptNodeIdForGroup(groupNodeId: string): string | null {
  const store = useCanvasStore.getState();
  return (
    store.nodes.find(
      (node) => node.type === 'scriptNode' && node.data?.linkedImageGroupId === groupNodeId,
    )?.id ?? null
  );
}

/** 出图时给图片节点的补丁：清掉上次失败、重新排队。 */
export function rearmImageNodePatch(): Partial<ImageGenNodeData> {
  return { canvas_auto_generate_once: true, generationError: null };
}
