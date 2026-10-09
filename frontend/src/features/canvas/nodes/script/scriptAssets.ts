// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { currentAssetReview, type ScriptAssetReviewReceipt } from './scriptAssetReviewReceipt';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  collectScriptCharacters,
  collectScriptProps,
  collectScriptScenes,
  scriptRowShotNumber,
  type ScriptAssetEntry,
} from './scriptViews';

/**
 * 脚本资产台账（对齐 LibTV `assets: {characters, scenes, props}`）。
 *
 * **为什么要有它**：此前 `scriptViews.ts` 的三个 `collect*` 每次渲染从分镜行的
 * **文本标签**现算一遍，算完就扔 —— 没有 id、没有状态、没有地方挂一张生成出来的图。
 * 于是「用脚本生成人物/场景/物品牌」在结构上不可能：就算出了一张图，也没人记得住它。
 *
 * LibTV 的台账是**存下来的**（每个资产有 `id` / `status` / `thumbnailUrl` / `linkedNodeId`）。
 * 我们做不到照抄，原因是**真相源**不同：我们的资产是分镜行的标签推导出来的，表一改资产就变，
 * 存一份副本会立刻和表不一致。所以这里分两半：
 *
 * - **身份**由表推导（`id` 是 `role + 归一化名` 的纯函数，同一名字每次重算都是同一个 id）；
 * - **出图结果**不存副本，而是**从画布上认领**：资产图节点上带 `scriptAssetId`，
 *   台账按 id 把它认回来（等价于 LibTV 的 `linkedNodeId` + `thumbnailUrl`，只是我们
 *   不另存一份缩略图 URL，直接读节点的 `imageUrl`）。
 *
 * 这样「谁是谁」始终只有一份，且老画布无需迁移 —— 没有资产图的节点，台账照常推导。
 *
 * 三族的图从哪来（`imageSource`）刻意分开记，因为它们的可信度不一样：
 * - `character`：行里 `character_image_N` 是后端回填的**真角色图**；
 * - `scene`：行里 `reference` 是**整镜参考帧** —— 场景就在画面里，借它做预览成立，
 *   但它不是「这个场景的概念图」，所以只作降级显示，不当已就绪；
 * - `prop`：行里**没有**道具图字段，没生成过就是真没有。
 */

export type ScriptAssetRole = 'character' | 'scene' | 'prop';

/**
 * 资产当前那张图的来路。
 * - `row`：来自分镜行（角色图 / 场景借的参考帧）；
 * - `generated`：来自画布上认领到的资产图节点（我们自己生成的）；
 * - `none`：没有任何图。
 */
export type ScriptAssetImageSource = 'row' | 'generated' | 'none';

/** 资产图上一次生成的同步状态。 */
export type ScriptAssetGeneratedState =
  | 'missing'
  | 'ready'
  | 'stale'
  | 'generating'
  | 'failed';

export type ScriptAssetQaState = 'unverified' | 'passed' | 'blocked';

/**
 * 画布上资产图节点留下的认领证据。
 *
 * `revision / contentHash / identityLocks / dependencies` 是资产定义的身份快照，
 * 不是生成产物的 sha256。真实产物摘要仍由后端生成回执写入，不能拿定义哈希冒充。
 */
export interface ScriptAssetImageClaim {
  nodeId: string;
  ownerId?: string;
  imageUrl: string | null;
  revision: number | null;
  contentHash: string | null;
  identityLocks: string[];
  dependencies: string[];
  isGenerating: boolean;
  hasError: boolean;
  qaStatus?: ScriptAssetQaState;
  visualReview?: ScriptAssetReviewReceipt | null;
  /** 生成模板声明的目标视图；这不是视觉验收结果。 */
  plannedViews?: string[];
  /** 只有后端/人工验收明确写入的视图才算可用视图。 */
  views?: string[];
}

export type ScriptAssetClaimSource =
  | ReadonlyMap<string, string | ScriptAssetImageClaim>
  | Record<string, string | ScriptAssetImageClaim>;

export interface ScriptAsset {
  /**
   * 稳定 id：`${role}:${归一化名}`。归一化只做 trim + casefold —— 不做同义词合并，
   * 因为「阿雀」和「阿鹊」是不是同一个角色只有人知道，擅自合并会把两个角色压成一个。
   */
  id: string;
  role: ScriptAssetRole;
  /** 族的显示名（角色 / 场景 / 道具），错误文案与界面标题共用。 */
  roleLabel: string;
  name: string;
  description: string;
  /** 正版本号；无历史认领时为 1，内容变化时至少加一。 */
  revision: number;
  /** 资产定义内容的确定性摘要（64 位十六进制）。 */
  contentHash: string;
  /** 生成时必须锁定的语义字段。 */
  identityLocks: string[];
  /** 该资产依赖的其它稳定资产身份。 */
  dependencies: string[];
  /** 当前可送进生成链的参考图；旧 revision 的生成图不会出现在这里。 */
  referenceImageUrl: string | null;
  /** 资产图预览 URL；revision 已过期时仍保留，用于告诉用户旧图长什么样。 */
  imageUrl: string | null;
  imageSource: ScriptAssetImageSource;
  /** 资产图节点的同步状态；无生成节点时为 missing。 */
  generatedImageState: ScriptAssetGeneratedState;
  /** 机器可验证的资产交付状态：不代替人工视觉审片。 */
  qaStatus: ScriptAssetQaState;
  generatedNodeId?: string;
  generatedOwnerId?: string;
  visualReview?: ScriptAssetReviewReceipt | null;
  /** 由镜头实际暴露出的视图需求，以及当前资产图明确声明的视图。 */
  requiredViews?: string[];
  /** 资产图提示词计划包含的视图，不能当作已画出的证据。 */
  plannedViews?: string[];
  availableViews?: string[];
  missingViews?: string[];
  /** 出现过的镜号（保持行序、去重）。 */
  shotNumbers: string[];
}

export interface ScriptAssetLedger {
  characters: ScriptAsset[];
  scenes: ScriptAsset[];
  props: ScriptAsset[];
  /** 三族按「角色 → 场景 → 道具」串起来 —— 参考图编号用这个顺序，必须只有一份。 */
  all: ScriptAsset[];
  byId: Map<string, ScriptAsset>;
  /** 兼容查找：`role + 归一化名` 仍可用于旧调用方，显式 asset_id 优先只体现在 byId。 */
  byRoleName: Map<string, ScriptAsset>;
  /** 一张图都没有的资产（开拍前的缺口清单）。 */
  missing: ScriptAsset[];
  /**
   * 还没**我们自己生成过**资产图的资产 —— 批量出图的默认勾选集。
   *
   * 与 {@link missing} 的区别是它把「借来的图」也算缺口：场景卡显示的图是整镜参考帧
   * （`imageSource === 'row'`），它不能当场景参考图送进生成，所以那张图在「能不能进
   * 下游」这件事上等于没有。角色有脚本回填的角色图（`row`），当参考图是够的，但它同样
   * 不是一张属于这个角色的资产图 —— 要不要重出由用户在弹层里勾。
   */
  pendingGeneration: ScriptAsset[];
  counts: Record<ScriptAssetRole, number>;
}

export const SCRIPT_ASSET_ROLE_LABEL: Record<ScriptAssetRole, string> = {
  character: '角色',
  scene: '场景',
  prop: '道具',
};

/** 族的固定顺序 —— 参考图编号（`{{Image N}}`）与锚定块都依赖它恒为角色→场景→道具。 */
export const SCRIPT_ASSET_ROLE_ORDER: readonly ScriptAssetRole[] = [
  'character',
  'scene',
  'prop',
];

/**
 * 资产 id：优先脚本行给出的显式身份，缺省才回落到 `role + 名字` 兼容键。
 *
 * 兼容键的归一化只做 trim + casefold。**不要**在这里塞进描述或镜号 —— 那些行里会变，
 * 一变 id 就变，已生成的资产图就认不回来了（认领靠 id 相等）。
 */
export function scriptAssetId(role: ScriptAssetRole, name: string): string {
  return `${role}:${name.trim().toLowerCase()}`;
}

function normalizeTokens(values: readonly string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const value of values) {
    const token = value.trim();
    if (token.length === 0 || seen.has(token)) continue;
    seen.add(token);
    normalized.push(token);
  }
  return normalized;
}

/** 角色按官方演员 taxonomy 的稳定身份面锁定；场景 / 道具只锁整个内容定义。 */
export function defaultScriptAssetIdentityLocks(
  role: ScriptAssetRole,
  explicit: readonly string[] = [],
): string[] {
  const configured = normalizeTokens(explicit);
  if (configured.length > 0) return configured;
  return role === 'character'
    ? ['face', 'costume', 'age', 'temperament']
    : ['content_hash'];
}

export function canonicalJson(value: unknown): string {
  if (value == null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(',')}}`;
}

/**
 * Small synchronous SHA-256 for asset-definition snapshots.
 *
 * It matches the backend digest shape without pretending the result is a media
 * artifact digest. The bytes hashed here are only the canonical asset definition.
 */
export function sha256Hex(input: string): string {
  const bytes = new TextEncoder().encode(input);
  const bitLength = bytes.length * 8;
  const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
  const padded = new Uint8Array(paddedLength);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x1_0000_0000), false);
  view.setUint32(paddedLength - 4, bitLength >>> 0, false);

  const hash = new Uint32Array([
    0x6a09e667,
    0xbb67ae85,
    0x3c6ef372,
    0xa54ff53a,
    0x510e527f,
    0x9b05688c,
    0x1f83d9ab,
    0x5be0cd19,
  ]);
  const words = new Uint32Array(64);
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      words[index] = view.getUint32(offset + index * 4, false);
    }
    for (let index = 16; index < 64; index += 1) {
      const x = words[index - 15];
      const y = words[index - 2];
      const sigma0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
      const sigma1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
      words[index] = (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0;
    }

    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index += 1) {
      const sum1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const choose = (e & f) ^ (~e & g);
      const temp1 = (h + sum1 + choose + SHA256_K[index] + words[index]) >>> 0;
      const sum0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (sum0 + majority) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }
    hash[0] = (hash[0] + a) >>> 0;
    hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0;
    hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0;
    hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0;
    hash[7] = (hash[7] + h) >>> 0;
  }

  return [...hash].map((value) => value.toString(16).padStart(8, '0')).join('');
}

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export function scriptAssetContentHash(params: {
  id: string;
  role: ScriptAssetRole;
  name: string;
  description: string;
  sourceImageUrl: string | null;
  identityLocks: readonly string[];
  dependencies: readonly string[];
}): string {
  return sha256Hex(
    canonicalJson({
      schema: 'village.script-asset-definition.v1',
      asset_id: params.id,
      role: params.role,
      name: params.name.trim(),
      description: params.description.trim(),
      source_image_url: params.sourceImageUrl ?? '',
      identity_locks: normalizeTokens(params.identityLocks),
      dependencies: normalizeTokens(params.dependencies),
    }),
  );
}

function claimFor(value: string | ScriptAssetImageClaim | undefined): ScriptAssetImageClaim | null {
  if (value == null) return null;
  if (typeof value === 'string') {
    return {
      nodeId: '',
      imageUrl: value,
      revision: null,
      contentHash: null,
      identityLocks: [],
      dependencies: [],
      isGenerating: false,
      hasError: false,
      // Legacy string claims only carried a URL; preserve their old usable behavior.
      qaStatus: 'passed',
    };
  }
  return value;
}

function claimMapOf(source: ScriptAssetClaimSource | undefined): Map<string, ScriptAssetImageClaim> {
  const found = new Map<string, ScriptAssetImageClaim>();
  if (!source) return found;
  const entries = source instanceof Map ? source.entries() : Object.entries(source);
  for (const [id, value] of entries) {
    const claim = claimFor(value);
    if (claim) found.set(id, claim);
  }
  return found;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? normalizeTokens(value.filter((item): item is string => typeof item === 'string'))
    : [];
}

function assetClaimFromData(nodeId: string, data: Record<string, unknown>): ScriptAssetImageClaim {
  const visualReview = currentAssetReview(data);
  const url = data.imageUrl ?? data.previewImageUrl;
  const revisionValue = data.scriptAssetRevision;
  const revision =
    typeof revisionValue === 'number' && Number.isInteger(revisionValue) && revisionValue >= 1
      ? revisionValue
      : null;
  return {
    nodeId,
    ownerId: typeof data.scriptAssetOwnerId === 'string' ? data.scriptAssetOwnerId : '',
    imageUrl: typeof url === 'string' && url.length > 0 ? url : null,
    revision,
    contentHash:
      typeof data.scriptAssetContentHash === 'string' && data.scriptAssetContentHash.length > 0
        ? data.scriptAssetContentHash
        : null,
    identityLocks: stringArray(data.scriptAssetIdentityLocks),
    dependencies: stringArray(data.scriptAssetDependencies),
    isGenerating: data.isGenerating === true || data.canvas_auto_generate_once === true,
    hasError: Boolean(data.generationError),
    qaStatus: qaStatusFromData(data),
    visualReview,
    plannedViews: stringArray(data.scriptAssetPlannedViews ?? data.scriptAssetViews),
    views: visualReview?.status === 'passed' ? visualReview.views : [],
  };
}

function qaStatusFromData(data: Record<string, unknown>): ScriptAssetQaState {
  if (data.generationError || data.isGenerating === true || data.canvas_auto_generate_once === true) {
    return 'blocked';
  }
  const imageUrl = data.imageUrl ?? data.previewImageUrl;
  const revision =
    typeof data.scriptAssetRevision === 'number' ? data.scriptAssetRevision : null;
  const contentHash = data.scriptAssetContentHash;
  const locks = data.scriptAssetIdentityLocks;
  if (typeof imageUrl !== 'string' || imageUrl.length === 0) return 'unverified';
  if (revision == null || !Number.isInteger(revision) || revision < 1) return 'blocked';
  if (typeof contentHash !== 'string' || contentHash.length === 0) return 'blocked';
  if (!Array.isArray(locks) || locks.length === 0) return 'blocked';
  return 'passed';
}

/**
 * 画布上的资产图节点认领表：`scriptAssetId` → 资产身份 + 图状态。
 *
 * `ownerId` 非空时只认**这个脚本节点**派生的资产图：画布上两个脚本节点都写了「阿雀」
 * 时，资产 id 会撞（兼容 id 只由 role + 名字决定），不按归属过滤的话 B 脚本会把 A 脚本的
 * 阿雀图认成自己的。
 */
export function scriptAssetImageNodes(ownerId: string | null = null, nodes: readonly { id?: string; data?: unknown }[] = useCanvasStore.getState().nodes): Map<string, ScriptAssetImageClaim> {
  return new Map(assetImageClaimPairs(nodes, ownerId));
}

/** 认领表的一行序列化。用字符串是为了直接喂 `useShallow` 做浅比较。 */
const CLAIM_SEP = '\u0000';

function assetImageClaimPairs(
  nodes: readonly { id?: string; data?: unknown }[],
  ownerId: string | null,
): [string, ScriptAssetImageClaim][] {
  const found: [string, ScriptAssetImageClaim][] = [];
  for (const node of nodes) {
    const data = (node.data ?? {}) as Record<string, unknown>;
    const assetId = typeof data.scriptAssetId === 'string' ? data.scriptAssetId : '';
    if (!assetId) continue;
    if (ownerId && data.scriptAssetOwnerId !== ownerId) continue;
    found.push([assetId, assetClaimFromData(node.id ?? '', data)]);
  }
  found.sort((left, right) => (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0));
  return found;
}

/**
 * 订阅画布上已认领的资产图（React 侧用）。
 *
 * 只订阅身份状态串，节点拖动 / 无关字段变化不会让调用方重渲染 —— 与 `useUpstreamNodes`
 * 同一个理由（画布上任何节点的每帧变化都会重建 nodes 数组）。资产图出图、失败或 revision
 * 变化时，台账与「哪些分镜图过期了」随之刷新。
 */
export function useScriptAssetImageClaims(
  ownerId: string | null,
): Map<string, ScriptAssetImageClaim> {
  const pairs = useCanvasStore(
    useShallow((state) =>
      assetImageClaimPairs(state.nodes, ownerId).map(
        ([id, claim]) => `${id}${CLAIM_SEP}${canonicalJson(claim)}`,
      ),
    ),
  );
  return useMemo(() => {
    const found = new Map<string, ScriptAssetImageClaim>();
    for (const pair of pairs) {
      const cut = pair.indexOf(CLAIM_SEP);
      if (cut <= 0) continue;
      try {
        found.set(pair.slice(0, cut), JSON.parse(pair.slice(cut + 1)) as ScriptAssetImageClaim);
      } catch {
        // Ignore malformed transient state; the next store update rebuilds the pair.
      }
    }
    return found;
  }, [pairs]);
}

/** 订阅某个脚本节点的资产台账（行变了或资产图出图落定都要重算）。 */
export function useScriptAssetLedger(
  scriptNodeId: string | null,
  rows: readonly FreezoneStoryScriptRow[],
): ScriptAssetLedger {
  const claims = useScriptAssetImageClaims(scriptNodeId);
  return useMemo(() => collectScriptAssetLedger(rows, claims), [rows, claims]);
}

/** 从聚合结果造一个台账条目（补 id / role / 图来路）。 */
function toAsset(
  role: ScriptAssetRole,
  collection: ScriptAssetEntry,
  generated: Map<string, ScriptAssetImageClaim>,
  /** 行里那张图算不算「这个资产真有图」—— 道具恒假（行里没有道具图字段）。 */
  rowImageCounts: boolean,
  requiredViews: readonly string[] = [],
): ScriptAsset {
  const id = collection.assetId.trim() || scriptAssetId(role, collection.name);
  const claim = generated.get(id) ?? null;
  const generatedUrl = claim?.imageUrl ?? null;
  const rowUrl = rowImageCounts ? collection.imageUrl : null;
  const imageUrl = generatedUrl ?? rowUrl;
  const imageSource: ScriptAssetImageSource = generatedUrl
    ? 'generated'
    : rowUrl
      ? 'row'
      : 'none';
  const identityLocks = defaultScriptAssetIdentityLocks(role, collection.identityLocks);
  const dependencies = normalizeTokens(collection.dependencies);
  const contentHash = scriptAssetContentHash({
    id,
    role,
    name: collection.name,
    description: collection.description,
    sourceImageUrl: role === 'character' ? collection.sourceImageUrl : null,
    identityLocks,
    dependencies,
  });
  const revision =
    claim?.contentHash == null || claim.contentHash === contentHash
      ? Math.max(1, claim?.revision ?? 1)
      : Math.max(1, (claim.revision ?? 0) + 1);
  const generatedImageState: ScriptAssetGeneratedState = (() => {
    if (!claim) return 'missing';
    if (claim.isGenerating) return 'generating';
    if (claim.hasError) return 'failed';
    if (claim.contentHash && claim.contentHash !== contentHash) return 'stale';
    return claim.imageUrl ? 'ready' : 'missing';
  })();
  const qaStatus: ScriptAssetQaState = claim?.qaStatus ?? 'unverified';
  const visualReview = generatedImageState === 'ready' && claim?.contentHash === contentHash ? claim.visualReview ?? null : null;
  const availableViews =
    claim?.qaStatus === 'passed'
    && generatedImageState === 'ready'
    && claim.contentHash === contentHash
      ? claim.views ?? []
      : [];
  const plannedViews = claim?.plannedViews ?? [];
  const referenceImageUrl =
    generatedImageState === 'ready'
      ? generatedUrl
      : role === 'character' && rowUrl
        ? rowUrl
        : null;
  return {
    id,
    role,
    roleLabel: SCRIPT_ASSET_ROLE_LABEL[role],
    name: collection.name,
    description: collection.description,
    revision,
    contentHash,
    identityLocks,
    dependencies,
    referenceImageUrl,
    imageUrl,
    imageSource,
    generatedImageState,
    qaStatus,
    generatedNodeId: claim?.nodeId,
    generatedOwnerId: claim?.ownerId,
    visualReview,
    requiredViews: [...requiredViews],
    plannedViews,
    availableViews,
    missingViews: requiredViews.filter((view) => !availableViews.includes(view)),
    shotNumbers: collection.shotNumbers,
  };
}

const VIEW_MARKERS: Record<string, readonly string[]> = {
  back: ['背影', '背面', '背对', '背向', 'back view'],
  side: ['侧面', '侧脸', '侧身', '侧向', 'profile', 'side view'],
  top: ['俯拍', '俯视', '顶视', '鸟瞰', 'overhead'],
  low: ['仰拍', '仰视', '低机位', '低角度', 'low angle'],
  expression: ['哭', '笑', '怒', '恐惧', '惊讶', '崩溃', '表情'],
  full_body: ['全身', '远景', '全景', '走过', '奔跑', '跳跃', 'full body'],
};

function requiredViewsFor(
  role: ScriptAssetRole,
  asset: ScriptAssetEntry,
  rows: readonly FreezoneStoryScriptRow[],
): string[] {
  const shotSet = new Set(asset.shotNumbers);
  // 只读镜头字段和对应资产字段。把整行 Object.values 拼起来会把对白、声音、
  // 其它角色和流水线元数据也算进来，产生跨资产的假缺口。
  const text = rows
    .filter((row, index) => shotSet.has(scriptRowShotNumber(row, index)))
    .map((row) => {
      const values: unknown[] = [row.reference_requirements];
      if (role === 'scene') values.push(row.shot, row.visual_description, row.lighting_mood);
      if (role === 'prop') {
        values.push(row.visual_description, row.prop_state_start, row.prop_state_end, row.prop_state_change);
      }
      if (role === 'character') {
        values.push(row.character_action);
        for (const slot of [1, 2, 3, 4]) {
          const name = row[`character_${slot}`];
          if (typeof name === 'string' && name.trim() === asset.name.trim()) {
            values.push(row[`character_description_${slot}`]);
          }
        }
      }
      return values.filter((value): value is string => typeof value === 'string').join(' ');
    })
    .join(' ')
    .toLowerCase();
  const hits = new Set(Object.entries(VIEW_MARKERS)
    .filter(([, markers]) => markers.some((marker) => text.includes(marker.toLowerCase())))
    .map(([view]) => view));
  if (role === 'character') {
    for (const view of ['wide', 'geometry', 'hero', 'multi_view']) hits.delete(view);
    hits.add('front');
    if (hits.has('back') || hits.has('side') || hits.has('full_body') || hits.has('top')) hits.add('full_body');
  } else if (role === 'scene') {
    for (const view of ['back', 'side', 'expression', 'full_body', 'hero', 'multi_view']) hits.delete(view);
    hits.add('wide');
    if (hits.has('top') || hits.has('low')) hits.add('geometry');
  } else {
    for (const view of ['side', 'expression', 'full_body', 'wide', 'geometry']) hits.delete(view);
    hits.add('hero');
    if (hits.has('top') || hits.has('back')) hits.add('multi_view');
  }
  return [...hits].sort();
}

/**
 * 从分镜行推导三族资产台账，并从画布认领已生成的资产图。
 *
 * `assetImageNodes` 允许注入（单测用）；不传就读真实画布。
 */
export function collectScriptAssetLedger(
  rows: readonly FreezoneStoryScriptRow[],
  generated: ScriptAssetClaimSource = scriptAssetImageNodes(),
): ScriptAssetLedger {
  const claims = claimMapOf(generated);
  const rowList = [...rows];
  const characters = collectScriptCharacters(rowList).map((entry) =>
    toAsset('character', entry, claims, true, requiredViewsFor('character', entry, rowList)),
  );
  const scenes = collectScriptScenes(rowList).map((entry) =>
    toAsset('scene', entry, claims, true, requiredViewsFor('scene', entry, rowList)),
  );
  // 道具：行里的 imageUrl 恒为 null（见 scriptViews.collectScriptProps），
  // 传 false 只是把这个既定事实写成显式参数，不靠上游巧合。
  const props = collectScriptProps(rowList).map((entry) =>
    toAsset('prop', entry, claims, false, requiredViewsFor('prop', entry, rowList)),
  );

  const byRole: Record<ScriptAssetRole, ScriptAsset[]> = { character: characters, scene: scenes, prop: props };
  const all = SCRIPT_ASSET_ROLE_ORDER.flatMap((role) => byRole[role]);
  const byId = new Map(all.map((asset) => [asset.id, asset]));
  const byRoleName = new Map<string, ScriptAsset>();
  for (const asset of all) {
    const legacyId = scriptAssetId(asset.role, asset.name);
    if (!byRoleName.has(legacyId)) byRoleName.set(legacyId, asset);
  }
  return {
    characters,
    scenes,
    props,
    all,
    byId,
    byRoleName,
    missing: all.filter((asset) => asset.imageSource === 'none'),
    pendingGeneration: all.filter((asset) => asset.generatedImageState !== 'ready'),
    counts: {
      character: characters.length,
      scene: scenes.length,
      prop: props.length,
    },
  };
}

/**
 * 这个资产该不该作为**参考图**进分镜图。
 *
 * 三族的口径不同，不能一刀切：
 * - **角色**：行里 `character_image_N` 是后端回填的真角色图，有图就进（`row` 也算）；
 * - **场景 / 道具**：行里没有它们的图字段。场景卡显示的「图」是借该行参考帧
 *   （`scriptViews.collectScriptScenes` 的既有行为）—— 那是整镜构图，把它当场景参考图
 *   送进生成会**把上一镜的画面喂给这一镜**；道具更是一直没有。所以这两族只认
 *   `imageSource === 'generated'`（我们自己生成出来的资产图）。
 */
export function assetContributesAsReference(asset: ScriptAsset): boolean {
  if (asset.visualReview?.status === 'blocked') return false;
  if (asset.referenceImageUrl != null) {
    return asset.imageSource === 'row' || asset.qaStatus == null || asset.qaStatus === 'passed';
  }
  if (asset.imageSource === 'row') return asset.role === 'character' && asset.imageUrl != null;
  return (
    asset.imageSource === 'generated'
    && (asset.qaStatus == null || asset.qaStatus === 'passed')
    && (asset.generatedImageState === 'ready' || asset.generatedImageState === 'missing')
    && asset.imageUrl != null
  );
}

/**
 * 按 id 取一组资产（保留传入顺序）。
 *
 * 顺序即编号：调用方传进来的顺序就是参考图顺序，锚定块的 `图片N` 下标由它决定。
 */
export function pickScriptAssets(
  ledger: ScriptAssetLedger,
  ids: readonly string[],
): ScriptAsset[] {
  const picked: ScriptAsset[] = [];
  for (const id of ids) {
    const asset = ledger.byId.get(id);
    if (asset) picked.push(asset);
  }
  return picked;
}

/** 「还差几张图」的人话摘要，台账视图与开拍前弹层共用。 */
export function describeMissingAssets(assets: readonly ScriptAsset[]): string {
  if (assets.length === 0) return '';
  const byRole = new Map<ScriptAssetRole, string[]>();
  for (const asset of assets) {
    const list = byRole.get(asset.role) ?? [];
    list.push(asset.name);
    byRole.set(asset.role, list);
  }
  return SCRIPT_ASSET_ROLE_ORDER.filter((role) => byRole.has(role))
    .map((role) => `${byRole.get(role)?.length ?? 0} 个${SCRIPT_ASSET_ROLE_LABEL[role]}`)
    .join(' / ');
}
