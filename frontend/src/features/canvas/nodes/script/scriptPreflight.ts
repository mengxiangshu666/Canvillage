// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  assetContributesAsReference,
  collectScriptAssetLedger,
  describeMissingAssets,
  scriptAssetId,
  scriptAssetImageNodes,
  type ScriptAsset,
  type ScriptAssetLedger,
} from './scriptAssets';
import {
  buildScriptRowSnapshots,
  computeStoryboardStaleness,
  type StoryboardMemberSnapshot,
  type StoryboardStaleReason,
} from './scriptStaleness';
import { buildScriptShotSpecs } from './scriptStoryboard';
import { storyboardImageNodesForScript } from './scriptStoryboardMembers';
import { rowCharacters } from './scriptViews';

/**
 * 开拍前清点（逐镜）。
 *
 * **为什么要有它**：`scriptStaleness` 回答的是「哪几张已出的分镜图过期了」，粒度到节点
 * 为止；`scriptStats` 回答的是「整张表一共缺多少项」，粒度到整表为止。开拍前真正要回答
 * 的问题是**逐镜**的：「第 7 镜到底能不能出、出了会不会崩、要花几张的钱」。这两处都答不了。
 *
 * LibTV 侧对应两层：每个镜头行带自己的 prompt 同步状态（`imagePromptState` /
 * `videoPromptState`，取值 `none/generating/synced/stale`，配 `textHash` / `payloadHash`
 * 判内容是否真的变了），流水线里再有一个 `compose-prompts` 步骤做开拍前的合成与确认。
 * 我们这边两者都没有 —— 这条清点就是补这一层：**逐镜状态**（已有派生图 / 待出图 /
 * 已过期 / 已同步）加**逐镜缺项**（缺图片提示词 / 缺角色图），两件事分开记。
 *
 * 边界（刻意不做的部分）：
 * - **不做视频侧的逐镜状态**。这一条先前写的理由是「脚本节点不派生视频节点」——
 *   **已经不成立了**：脚本节点现在经「逐镜出视频」派生每条镜头视频，再经「成片」合成
 *   （见 `scriptShotVideos.ts` / `scriptShotCompose.ts`）。不在这里加视频状态的理由换成了
 *   真相源问题：视频那侧的状态**全在画布上读得出来**（`planScriptShotVideos` 按分镜图
 *   有没有出图、视频节点有没有出片现算），在这里再存一份就是第二个真相源。
 * - **不改 `scriptStats`**。那一份是整表读数，口径与这里不同（例如它把「时长解析不出」
 *   也算缺项），合并会让两边的既有单测同时失真。
 * - **纯函数 + 一个读 store 的壳**，与 `scriptStaleness` 同一套写法，便于单测。
 */

/** 一镜的分镜图同步状态。 */
export type ScriptShotSyncState =
  /** 还没派生过分镜图（脚本改过行数、或压根没点过「生成分镜」）。 */
  | 'missing'
  /** 派生过，但还没出自己的图 / 上次失败。 */
  | 'pending'
  /** 已出图，但脚本行改过，图与当前行对不上。 */
  | 'stale'
  /** 已出图且与当前脚本行一致。 */
  | 'synced';

/**
 * 一镜缺的开拍条件。
 *
 * - `image-prompt-missing`：**硬阻塞**。图片提示词与画面描述都空 —— 派生出来的分镜图
 *   节点没有任何可提交的内容，图片节点会以「请先填写提示词」为由拒绝提交并一直挂着。
 * - `character-image-missing`：**软降级**。填了角色名却没有角色图，这一镜能出图，
 *   但生成时拿不到该角色的参考图 —— 出来的人不像那个人。
 * - `reference-overflow`：**软降级（静默丢数据）**。参考图张数超过后端上限，多出来的会被
 *   直接丢掉，而提示词里的锚定块还写着「……的参考图是 图片7」—— 用户以为带进去了。
 * - `reference-token-missing`：**软降级（指向不存在的图）**。提示词里写了 `@图片3`
 *   但这一镜只有 2 张参考图，编号落在空处 —— 视频侧有同款闸门（`prompt_reference_missing`），
 *   图片侧此前完全没有。
 */
export type ScriptShotDefect =
  | 'image-prompt-missing'
  | 'character-image-missing'
  | 'reference-overflow'
  | 'reference-token-missing';

/**
 * 一次能带上去的参考图上限。
 *
 * **这是后端的真值，不是这里定的**：`image_generator.py` 的
 * `request.reference_images[:9]` 与 `nanobanana_grid.py` 的 `reference_images[:9]`
 * 都在这里截断，且不报警、不打日志。前台照它拦一次，是为了把这次静默截断变成一个
 * 用户能看见的缺项 —— 两边都改才是修好；只改后台不会告诉任何人。
 */
export const SCRIPT_REFERENCE_IMAGE_CAP = 9;

/** 提示词里出现的 `@图片N` / `图片N` 编号（N 从 1 起）。 */
function referencedImageNumbers(prompt: string): number[] {
  const found = new Set<number>();
  for (const match of prompt.matchAll(/@?\s*图片\s*(\d+)/g)) {
    const value = Number(match[1]);
    if (Number.isInteger(value) && value >= 1) found.add(value);
  }
  return [...found].sort((left, right) => left - right);
}

/**
 * 资产层的缺口清点（第四刀）。
 *
 * **为什么单列一层**：逐镜清点回答的是「这一镜缺什么」，但「张三有没有图」是**跨镜**的
 * 事实 —— 同一个角色在 30 镜里出现，逐镜清单会把它报 30 遍，用户看到的是 30 个缺口，
 * 实际只要补 1 张图。LibTV 侧对应 `prepare-assets` 那一步：它在开拍前把资产过一遍，
 * 缺的集中补齐（`onBatchGenerateAssets`），而不是逐镜去补。
 *
 * 判定口径与 `scriptAssets.assetContributesAsReference` 严格一致：**场景 / 道具**必须
 * 有我们自己生成的资产图才算「不缺口」（行里的图是整镜参考帧，进不了参考图数组）；
 * **角色**有脚本回填的角色图就算够用，但没有自己那张时仍列进 `pendingGeneration`
 * 供批量出图（用户可能就是想统一换成本产品生成的设定图）。
 */
export interface ScriptAssetPreflight {
  /**
   * 不能作为分镜参考图的资产。
   *
   * 不能直接复用台账的 `missing`：场景可以从整镜参考帧借一个预览，
   * 因而不在 `missing` 里；但那张图同时被 `assetContributesAsReference` 明确排除，
   * 生成分镜时并不会进入参考图数组。预检必须按下游真实可用性算，不能按“有没有预览”算。
   */
  referenceMissing: ScriptAsset[];
  /** 还没有**我们自己生成**的资产图的资产（批量出图的默认勾选集）。 */
  pendingGeneration: ScriptAsset[];
  /** 会真的被送进分镜图参考图的资产数（角色有行图也算）。 */
  contributionCount: number;
  /** 缺口的逐族人话摘要，如「2 个角色 / 1 个场景」；没有缺口时为空串。 */
  missingSummary: string;
  /** 有资产缺图（不是硬阻塞：缺参考图是降级，不是出不了图）。 */
  hasGap: boolean;
}

/** 纯清点：从台账推出资产层缺口。 */
export function computeScriptAssetPreflight(ledger: ScriptAssetLedger): ScriptAssetPreflight {
  const referenceMissing = ledger.all.filter((asset) => !assetContributesAsReference(asset));
  return {
    referenceMissing,
    pendingGeneration: ledger.pendingGeneration,
    contributionCount: ledger.all.length - referenceMissing.length,
    missingSummary: describeMissingAssets(referenceMissing),
    hasGap: referenceMissing.length > 0,
  };
}

export interface ScriptShotPreflightEntry {
  rowKey: string;
  shotNumber: string;
  state: ScriptShotSyncState;
  defects: ScriptShotDefect[];
  /** 该镜分镜图过期逐节点原因（`state === 'stale'` 时有值）。 */
  reasons: StoryboardStaleReason[];
  /** 连出图都提交不了（缺图片提示词）。 */
  blocked: boolean;
}

export interface ScriptPreflight {
  /** 逐镜清点，保持脚本行序。 */
  entries: ScriptShotPreflightEntry[];
  byRowKey: Map<string, ScriptShotPreflightEntry>;
  counts: Record<ScriptShotSyncState, number>;
  /**
   * 建了节点就会真的自提交出图的张数 —— 已有同步图的（不动）与硬阻塞的（提交不了）
   * 都不算。价格估算与「重新生成 n 张」的 n 都用这一个数。
   */
  generatableShotCount: number;
  /** 硬阻塞的镜号（展示用，保持行序）。 */
  blockedShotNumbers: string[];
  /** 软降级的镜号。 */
  degradedShotNumbers: string[];
  /** 与脚本行对不上的镜号。 */
  staleShotNumbers: string[];
  /** 还没出图的镜号。 */
  pendingShotNumbers: string[];
  /** 参考图超出后端上限的镜号（会被静默丢掉几张）。 */
  overflowShotNumbers: string[];
  /** 提示词指向了不存在的参考图编号的镜号。 */
  tokenMissingShotNumbers: string[];
  hasBlockers: boolean;
}

/** 判定一镜状态要找的分镜图事实。`null` 表示这一行还没有派生节点。 */
export interface ScriptPreflightMember {
  nodeId: string;
  rowKey: string;
  snapshot: StoryboardMemberSnapshot;
  /** 这一张是不是已经出了自己的图。 */
  hasImage: boolean;
  /** 上次出图是不是失败了。 */
  hasError: boolean;
}

/**
 * 把一镜归到四个状态之一。
 *
 * 判定次序是刻意的：**先看有没有节点**（没有就是 missing，谈不上过期），**再看这一镜
 * 还需不需要出图**（上次失败也算要出 —— 与「点重新生成会真的动手」的
 * `storyboardMemberNeedsImage` 同一口径，两边不一致的话清点会说「不用管」而重跑会真跑），
 * 最后才比快照。反过来写会把「还没出图」的镜报成「已过期」（老横幅就是这么含糊的）。
 */
function resolveShotState(params: {
  member: ScriptPreflightMember | undefined;
  staleReasons: StoryboardStaleReason[];
}): ScriptShotSyncState {
  const { member, staleReasons } = params;
  if (!member) return 'missing';
  if (!member.hasImage) return 'pending';
  // 出过图但脚本改过 —— 先报 stale 再报失败：横幅按 stale 的门槛亮，
  // 顺序反过来的话「脚本改过又重跑失败」那一张会让横幅与逐镜清单对不上。
  if (staleReasons.length > 0) return 'stale';
  // 上一次重跑失败（图还是旧的）：仍要再跑一次，所以算 pending 而不是 synced，
  // 否则它会从「会出几张」里漏掉，点数少报。
  if (member.hasError) return 'pending';
  return 'synced';
}

/**
 * 一镜缺的开拍条件（与 `scriptStats` 的缺项口径分开：这里按「会不会拦住出图」分）。
 *
 * 角色图的判据要连台账一起看：生成过资产图的角色**已经有了**这个角色的图，
 * 此时行里没有 `character_image_N` 不再算缺（否则补完资产图那句话还在，用户会以为没补上）。
 * 与 `buildScriptShotRefEntries` 的取值口径必须一致 —— 它才是真正决定送不送参考图的那处。
 */
function rowDefects(
  row: FreezoneStoryScriptRow,
  shot: { prompt: string; referenceUrls: string[] },
  ledger?: ScriptAssetLedger,
): ScriptShotDefect[] {
  const defects: ScriptShotDefect[] = [];
  if (shot.prompt.trim().length === 0) defects.push('image-prompt-missing');
  const namedWithoutImage = rowCharacters(row).some((character) => {
    if (character.name.length === 0 || character.imageUrl) return false;
    const asset = ledger?.byRoleName.get(scriptAssetId('character', character.name));
    return !asset?.referenceImageUrl;
  });
  if (namedWithoutImage) defects.push('character-image-missing');

  // 后端的静默截断（前两条是「少给」，这一条是「给了但被丢掉」）。
  if (shot.referenceUrls.length > SCRIPT_REFERENCE_IMAGE_CAP) {
    defects.push('reference-overflow');
  }
  // 提示词指向了不存在的编号：这一镜只有 N 张图，却写着 图片(N+k)。
  // 只报**越界**的引用；正文里正常提到「图片1」而确实有图片1 时不算缺项。
  const referenced = referencedImageNumbers(shot.prompt);
  const maxReferenced = referenced.length > 0 ? referenced[referenced.length - 1] : 0;
  if (maxReferenced > shot.referenceUrls.length) defects.push('reference-token-missing');
  return defects;
}

/**
 * 纯清点：给定分镜图成员与当前脚本行，算出逐镜状态与缺项。
 *
 * 配对用行键查表（与 {@link computeStoryboardStaleness} 同一口径）：分镜组会按位置重排，
 * 下标顺序在用户拖动过成员之后就不再可信。
 */
export function computeScriptPreflight(params: {
  members: ScriptPreflightMember[];
  rows: FreezoneStoryScriptRow[];
  /**
   * 资产台账。**必须与「生成分镜」用的是同一份**：`buildScriptShotSpecs` 拿它拼锚定块，
   * 而「缺图片提示词」判的是**最终那句**提示词 —— 只带锚定块的行（画面描述与分镜提示词
   * 都空、但有资产图）是能出图的，不传台账会把这几镜错判成硬阻塞。
   */
  ledger?: ScriptAssetLedger;
  directorPlan?: FreezoneStoryDirectorPlan | null;
}): ScriptPreflight {
  const { members, rows } = params;
  const memberByRowKey = new Map<string, ScriptPreflightMember>();
  for (const member of members) memberByRowKey.set(member.rowKey, member);

  const staleness = computeStoryboardStaleness({
    members: members.map((member) => member.snapshot),
    // 必须与「生成分镜」写快照时用同一份台账：锚定块与场景/道具参考图都来自台账，
    // 不传的话「资产图刚生成」在这里判不出过期，而生成那边已经把新快照写进去了。
    rows: buildScriptRowSnapshots(rows, params.ledger, params.directorPlan),
  });

  const counts: Record<ScriptShotSyncState, number> = {
    missing: 0,
    pending: 0,
    stale: 0,
    synced: 0,
  };
  const entries: ScriptShotPreflightEntry[] = [];
  let generatableShotCount = 0;
  const blockedShotNumbers: string[] = [];
  const degradedShotNumbers: string[] = [];
  const staleShotNumbers: string[] = [];
  const pendingShotNumbers: string[] = [];
  const overflowShotNumbers: string[] = [];
  const tokenMissingShotNumbers: string[] = [];

  buildScriptShotSpecs(rows, params.ledger, undefined, params.directorPlan).forEach((shot, index) => {
    const member = memberByRowKey.get(shot.rowKey);
    const reasons = member ? (staleness.reasons.get(member.nodeId) ?? []) : [];
    const state = resolveShotState({ member, staleReasons: reasons });
    const defects = rowDefects(rows[index], shot, params.ledger);
    const blocked = defects.includes('image-prompt-missing');

    counts[state] += 1;
    if (!blocked && state !== 'synced') generatableShotCount += 1;
    if (blocked) blockedShotNumbers.push(shot.shotNumber);
    if (defects.includes('character-image-missing')) degradedShotNumbers.push(shot.shotNumber);
    if (defects.includes('reference-overflow')) overflowShotNumbers.push(shot.shotNumber);
    if (defects.includes('reference-token-missing')) tokenMissingShotNumbers.push(shot.shotNumber);
    if (state === 'stale') staleShotNumbers.push(shot.shotNumber);
    if (state === 'pending') pendingShotNumbers.push(shot.shotNumber);

    entries.push({
      rowKey: shot.rowKey,
      shotNumber: shot.shotNumber,
      state,
      defects,
      reasons,
      blocked,
    });
  });

  return {
    entries,
    byRowKey: new Map(entries.map((entry) => [entry.rowKey, entry] as const)),
    counts,
    generatableShotCount,
    blockedShotNumbers,
    degradedShotNumbers,
    staleShotNumbers,
    pendingShotNumbers,
    overflowShotNumbers,
    tokenMissingShotNumbers,
    hasBlockers: blockedShotNumbers.length > 0,
  };
}

/** 这一张分镜图有没有出自己的图（与 `storyboardMemberImageUrl` 同口径，不认参考图）。 */
function memberHasImage(node: { data?: Record<string, unknown> }): boolean {
  const url = node.data?.imageUrl ?? node.data?.previewImageUrl;
  return typeof url === 'string' && url.length > 0;
}

/** 从一组分镜图节点取清点事实。 */
function preflightMembersFromNodes(nodes: readonly CanvasNode[]): ScriptPreflightMember[] {
  const members: ScriptPreflightMember[] = [];
  const ordered = [...nodes].sort(
    (left, right) => left.position.y - right.position.y || left.position.x - right.position.x,
  );
  for (const node of ordered) {
    const data = (node.data ?? {}) as Record<string, unknown>;
    const rowKey = readScriptShotId(data);
    // 没有行键 = 不是这条路派生的分镜图（用户手动并进组里的普通图片节点），不参与清点。
    if (!rowKey) continue;
    const readOptional = (key: string): string | null | undefined => {
      const value = data[key];
      if (value === undefined) return undefined;
      return typeof value === 'string' ? value : null;
    };
    members.push({
      nodeId: node.id,
      rowKey,
      snapshot: {
        nodeId: node.id,
        rowKey,
        prompt: readOptional('scriptRowPrompt'),
        reference: readOptional('scriptRowReference'),
        assetRevision: readOptional('scriptRowAssetSnapshot'),
      },
      hasImage: memberHasImage(node),
      hasError: Boolean(data.generationError),
    });
  }
  return members;
}

/** 读脚本节点的分镜行（与 ScriptNode 用的是同一份 `data.scriptResult.rows`）。 */
function scriptRowsOf(scriptNodeId: string): FreezoneStoryScriptRow[] {
  const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === scriptNodeId);
  const result = node?.data?.scriptResult as { rows?: unknown } | undefined;
  return Array.isArray(result?.rows) ? (result.rows as FreezoneStoryScriptRow[]) : [];
}

/**
 * 某个脚本节点此刻的开拍清点。
 *
 * 还没有关联分镜组时成员为空集 —— 此时每一镜都是 `missing`，清点结果正好就是
 * 「这些行还能不能出图、缺什么」，正是首次生成前要看的。
 */
export function scriptPreflightForScript(
  scriptNodeId: string,
  ledger?: ScriptAssetLedger,
): ScriptPreflight {
  const directorPlan = (useCanvasStore.getState().nodes.find((node) => node.id === scriptNodeId)?.data?.scriptResult as { director_plan?: FreezoneStoryDirectorPlan | null } | undefined)?.director_plan;
  return computeScriptPreflight({
    members: preflightMembersFromNodes(storyboardImageNodesForScript(scriptNodeId)),
    rows: scriptRowsOf(scriptNodeId),
    ledger,
    directorPlan,
  });
}

/**
 * 某个脚本节点此刻的**资产层**缺口清点（第四刀）。
 *
 * `ledger` 由调用方传时以它为准：React 侧已经订阅了资产图落定（`useScriptAssetLedger`），
 * 本函数从 store 现读没有订阅，不传的话「刚生成一张资产图」不会让它重算。
 */
export function scriptAssetPreflightForScript(
  scriptNodeId: string,
  ledger?: ScriptAssetLedger,
): ScriptAssetPreflight {
  return computeScriptAssetPreflight(
    ledger ?? collectScriptAssetLedger(scriptRowsOf(scriptNodeId), scriptAssetImageNodes(scriptNodeId)),
  );
}

/** 镜号列表压成一句人话：连着报三个以上就折成「第 3、7、12 镜等 n 镜」。 */
export function formatShotNumbers(shotNumbers: string[], maxInline = 4): string {
  if (shotNumbers.length === 0) return '';
  const head = shotNumbers.slice(0, maxInline).map((no) => `第 ${no} 镜`);
  const rest = shotNumbers.length - head.length;
  return rest > 0 ? `${head.join('、')} 等 ${shotNumbers.length} 镜` : head.join('、');
}

/**
 * 弹层里的「这一批是哪几镜」：有镜号就列镜号，没镜号（调用方没传清点）就退回张数。
 *
 * 两处口径必须能同时出现在同一句话里（「将对未出图或失败的 X 重新生成」），
 * 所以张数由调用方给 —— 它可能与镜号数不一致（例如老节点没写行键，镜号列不出来
 * 但张数仍然算得准）。
 */
export function describeShotScope(shotNumbers: string[], fallbackCount: number): string {
  const list = formatShotNumbers(shotNumbers);
  if (list.length === 0) return `${fallbackCount} 张分镜图`;
  return shotNumbers.length >= fallbackCount
    ? `${list}（共 ${fallbackCount} 张）`
    : `${list}等（共 ${fallbackCount} 张）`;
}

/** 逐镜缺项翻成人话（弹层与 tooltip 共用一处口径）。 */
export function describeShotDefects(defects: ScriptShotDefect[]): string {
  const parts: string[] = [];
  if (defects.includes('image-prompt-missing')) parts.push('缺图片提示词（分镜提示词与画面描述都空）');
  if (defects.includes('character-image-missing')) parts.push('角色没有角色图');
  if (defects.includes('reference-overflow')) {
    parts.push(`参考图超过 ${SCRIPT_REFERENCE_IMAGE_CAP} 张，多出来的会被丢掉`);
  }
  if (defects.includes('reference-token-missing')) parts.push('提示词引用了不存在的图片编号');
  return parts.join('；');
}

/** 状态翻成人话（逐镜 tooltip 用）。 */
export function describeShotState(state: ScriptShotSyncState): string {
  switch (state) {
    case 'missing':
      return '还没有分镜图';
    case 'pending':
      return '还没出图';
    case 'stale':
      return '脚本已变更，图需重出';
    case 'synced':
      return '已出图且与脚本一致';
  }
}
