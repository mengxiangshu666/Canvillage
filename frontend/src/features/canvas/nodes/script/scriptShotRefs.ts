// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import {
  SCRIPT_ASSET_ROLE_LABEL,
  assetContributesAsReference,
  scriptAssetId,
  type ScriptAsset,
  type ScriptAssetLedger,
  type ScriptAssetRole,
} from './scriptAssets';
import { buildScriptAssetAnchor, bakeScriptAssetAnchor } from './scriptAssetAnchor';
import { cellText, rowCharacters, splitScriptTags } from './scriptViews';

/**
 * 单镜参考图条目：一条 = 一个会占掉 `图片N` 槽位的东西。
 *
 * **这是全仓唯一一处「参考图顺序」的真相源。** 此前顺序散在三处（派生节点时拼
 * `referenceImageUrls`、判过期时拼快照、重跑时再拼一次），锚定块的编号还必须与它
 * 完全一致 —— 四处各拼一遍，迟早错位。现在都从这里取。
 *
 * `assetId` 为空的条目是**没有身份**的参考（行「参考」列的关键帧），它照样占槽位，
 * 但不进锚定块 —— 锚定表的每一行都必须指向一个说得出名字的东西。
 */
export interface ScriptShotRefEntry {
  assetId: string | null;
  role: ScriptAssetRole;
  roleLabel: string;
  name: string;
  /** 冻结时的资产版本；无身份参考帧为 null。 */
  assetRevision: number | null;
  /** 冻结时的资产定义摘要；无身份参考帧为 null。 */
  assetContentHash: string | null;
  /** 冻结时锁住的语义字段。 */
  identityLocks: string[];
  /** 冻结时该资产依赖的其它资产身份。 */
  dependencies: string[];
  imageUrl: string;
}

/**
 * 该行参考图的有序条目。
 *
 * 顺序 = 角色（按槽位）→ 场景 → 道具 → 参考帧兜底。
 *
 * **角色**的图优先用台账里那张（我们自己生成的资产图），没有就回落到行里后端回填的
 * `character_image_N` —— 两者都是「这个角色长什么样」，生成过就用生成的那张。
 * **场景 / 道具**行里没有图字段，只认台账里我们自己生成的资产图；场景卡显示的「图」
 * 是借该行参考帧，把它当场景参考图送进生成会把上一镜的画面喂给这一镜
 * （见 `scriptAssets.assetContributesAsReference`）。
 *
 * **参考帧只在没有任何资产图时才上场**（对齐 `scriptViews.shotOwnReferenceUrls` 的既有
 * 口径）。这不是保守：分镜图节点自身的 `@图片N` 编号基线挂在第 1 张上，多塞一张会让
 * 已有节点里写好的 `@图片1` 全部错位到另一张图。
 */
export function buildScriptShotRefEntries(
  row: FreezoneStoryScriptRow,
  ledger: ScriptAssetLedger | undefined,
): ScriptShotRefEntry[] {
  const entries: ScriptShotRefEntry[] = [];
  const seenUrls = new Set<string>();
  const rejectedUrls = new Set(ledger?.all
    .filter(asset => asset.visualReview?.status === 'blocked')
    .map(asset => asset.visualReview!.imageUrl) ?? []);
  const pushEntry = (entry: Omit<ScriptShotRefEntry, 'imageUrl'>, imageUrl: string) => {
    // 两个参考共用同一张图时后一个不占槽位 —— 与 `orderedReferenceUrlsWithOwnFirst`
    // 的 Set 去重同一口径，否则锚定块的编号会和实际上传的数组差一位。
    if (seenUrls.has(imageUrl) || rejectedUrls.has(imageUrl)) return;
    seenUrls.add(imageUrl);
    entries.push({ ...entry, imageUrl });
  };
  /** 台账里该资产那张图（场景 / 道具拿到它就等于「生成过资产图」）。 */
  const ledgerImageOf = (role: ScriptAssetRole, name: string): ScriptAsset | undefined => {
    if (name.length === 0) return undefined;
    const asset = ledger?.byRoleName.get(scriptAssetId(role, name));
    return asset?.referenceImageUrl && assetContributesAsReference(asset) ? asset : undefined;
  };

  for (const character of rowCharacters(row)) {
    const name = character.name || `角色${character.slot}`;
    if (ledger?.byRoleName.get(scriptAssetId('character', name))?.visualReview?.status === 'blocked') continue;
    const generated = ledgerImageOf('character', name);
    // 生成过资产图就用生成的那张；否则用行里回填的角色图（脚本自己给的，与生成无关）。
    const imageUrl = generated?.referenceImageUrl ?? character.imageUrl;
    if (!imageUrl) continue;
    pushEntry(
      {
        assetId: generated?.id ?? scriptAssetId('character', name),
        role: 'character',
        roleLabel: SCRIPT_ASSET_ROLE_LABEL.character,
        name: character.name,
        assetRevision: generated?.revision ?? null,
        assetContentHash: generated?.contentHash ?? null,
        identityLocks: generated?.identityLocks ?? [],
        dependencies: generated?.dependencies ?? [],
      },
      imageUrl,
    );
  }
  const pushLedgerOnly = (role: 'scene' | 'prop', name: string) => {
    const asset = ledgerImageOf(role, name);
    if (!asset?.referenceImageUrl) return;
    pushEntry(
      {
        assetId: asset.id,
        role,
        roleLabel: SCRIPT_ASSET_ROLE_LABEL[role],
        name: asset.name,
        assetRevision: asset.revision,
        assetContentHash: asset.contentHash,
        identityLocks: asset.identityLocks,
        dependencies: asset.dependencies,
      },
      asset.referenceImageUrl,
    );
  };
  for (const tag of splitScriptTags(cellText(row, 'scene_tags'))) pushLedgerOnly('scene', tag);
  for (const tag of splitScriptTags(cellText(row, 'prop_tags'))) pushLedgerOnly('prop', tag);

  const frame = cellText(row, 'reference');
  if (entries.length === 0 && frame.length > 0) {
    pushEntry(
      {
        assetId: null,
        role: 'scene',
        roleLabel: SCRIPT_ASSET_ROLE_LABEL.scene,
        name: '',
        assetRevision: null,
        assetContentHash: null,
        identityLocks: [],
        dependencies: [],
      },
      frame,
    );
  }
  return entries;
}

/** 该行要带出去的参考图 URL（有序，与 {@link buildScriptShotRefEntries} 同源）。 */
export function scriptShotReferenceUrls(entries: readonly ScriptShotRefEntry[]): string[] {
  return entries.map((entry) => entry.imageUrl);
}

/**
 * 冻结进分镜节点的资产身份串。
 *
 * 只包含有稳定身份的资产；参考帧为 null。顺序与参考图编号一致，
 * 版本变化时不需要重新解释 URL，就能只让真正引用该资产的镜头失效。
 */
export function scriptShotAssetRevisionSnapshot(
  entries: readonly ScriptShotRefEntry[],
): string | null {
  const lines = entries
    .filter((entry): entry is ScriptShotRefEntry & { assetId: string } => Boolean(entry.assetId))
    .map((entry) =>
      [
        entry.assetId,
        entry.assetRevision ?? 0,
        entry.assetContentHash ?? '',
        entry.identityLocks.join(','),
        entry.dependencies.join(','),
      ].join('@'),
    );
  return lines.length > 0 ? lines.join('\n') : null;
}

/**
 * 该行最终的图片提示词 = 锚定块（如有）+ 脚本原句。
 *
 * **空原句 + 有资产图时不再是空串**：会得到一段只有锚定块的提示词。那不是「没内容」——
 * 它明确告诉模型「照这几张参考图里的人/景/物画」，比提交空串有用；而两者都空时
 * 仍然是空串，让上层的「缺图片提示词」闸门照常拦住。
 */
export function scriptShotPromptWithAnchor(
  entries: readonly ScriptShotRefEntry[],
  basePrompt: string,
): string {
  return bakeScriptAssetAnchor(basePrompt, buildScriptAssetAnchor(entries));
}
