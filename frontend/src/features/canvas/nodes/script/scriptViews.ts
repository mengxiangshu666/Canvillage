// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import {
  ensureScriptShotIdentities,
  legacyScriptRowKey,
} from '@/features/canvas/domain/scriptShotIdentity';
import { SCRIPT_CHARACTER_SLOT_COUNT } from './scriptFields';

/**
 * 脚本节点视图。
 *
 * **别再说「三视图对齐 LibTV」—— 站不住。** LibTV 有两套**各自只有两个**视图的默认集，
 * 分别属于两代节点（`0o6g_m-brn0s0.js`）：
 *
 * ```js
 * getDefaultScriptV2Views() = [ {id:"default",type:"table"}, {id:"asset",type:"asset"} ]
 * getDefaultScriptViews()   = [ {id:"default",type:"table"}, {id:"creative",type:"creative"} ]
 * ```
 *
 * script-v2 是「表格 + 资产」，**没有创意**；老 script 是「表格 + 创意」，**没有资产**。
 * 我们这边是两个都留了、合并成三个，属于**自己的产品决定**（三选一的切换器仍是我们
 * 自觉比 LibTV 更好用的地方）。
 *
 * **两个视图在 LibTV 侧都有真实渲染体**（先前这里写「没有可对照的实证」，是错的，
 * 2026-09-13 复查 `15epcn_e-6pl6.js` 推翻）：
 * - `creative`：视图 switch 里就有 `case"creative":return jsx(nm,…)`，网格 + 卡片 `nu`；
 * - `asset`：**资产看板**组件 `or`（配套卡片 `r1` / 分区 `on` / 识别横幅 `oe` /
 *   风格条 `ot` / 底部状态栏 + 批量生成 / 三个弹层 `aK`、`a_`、`aH`）都在，只是它挂在
 *   script-v2 的**流水线步骤**上（`case"prepare-assets":return jsx(or,…)`），不挂在视图
 *   switch 上 —— 全 chunk 搜 `case"asset"` 零命中，先前正是照这一点下的否定结论。
 *
 * 所以「对齐」二字对这两个视图都说得通，但要分清**抄的是哪一半**：我们现在只抄了版面
 * 骨架（角色 chip / 三行截断描述 / 镜次清单），没抄资产看板的操作面 —— 新增卡片、
 * 待生成占位、低置信度徽标、合规徽标、右键菜单（生成 / 清图 / 跳节点 / 删除 / 存个人库）、
 * 底部状态栏与「批量生成资产」。要补的是这些，不是再改一遍卡片外观。
 *
 * （先前这里的注释引用的是 `scriptAssetViewLabel` 作为「LibTV 三视图」的来源，
 * 那个 key 只出现在 `getDefaultScriptV2Views` 里，等于把两代节点的视图集当成了一套。）
 */
export type ScriptViewId = 'table' | 'creative' | 'asset' | 'video';

export interface ScriptViewDef {
  id: ScriptViewId;
  label: string;
}

export const SCRIPT_VIEWS: ScriptViewDef[] = [
  { id: 'table', label: '脚本视图' },
  { id: 'creative', label: '创意视图' },
  { id: 'asset', label: '资产视图' },
  { id: 'video', label: '视频素材' },
];

export const DEFAULT_SCRIPT_VIEW_ID: ScriptViewId = 'table';

const SCRIPT_VIEW_IDS = new Set<string>(SCRIPT_VIEWS.map((view) => view.id));

/** 读 `viewMode` / `activeViewId` 这类外部输入时统一收敛，未知值回落表格视图。 */
export function resolveScriptViewId(value: unknown): ScriptViewId {
  return typeof value === 'string' && SCRIPT_VIEW_IDS.has(value)
    ? (value as ScriptViewId)
    : DEFAULT_SCRIPT_VIEW_ID;
}

export function scriptViewLabel(viewId: ScriptViewId): string {
  return SCRIPT_VIEWS.find((view) => view.id === viewId)?.label ?? viewId;
}

/** 单元格取文本（非字符串/数字一律当空）。资产台账与参考图装配共用这一份口径。 */
export function cellText(row: FreezoneStoryScriptRow, key: string): string {
  const raw = row[key];
  if (raw == null) return '';
  return typeof raw === 'string' || typeof raw === 'number' ? String(raw).trim() : '';
}

function cellImageUrl(row: FreezoneStoryScriptRow, key: string): string | null {
  const value = cellText(row, key);
  return value.length > 0 ? value : null;
}

export function scriptCharacterStateText(row: FreezoneStoryScriptRow, phase: 'start' | 'end'): string {
  const states = row[`character_state_${phase}`];
  if (!states || typeof states !== 'object' || Array.isArray(states)) return '';
  return Object.entries(states).filter(([, value]) => typeof value === 'string' && value.trim())
    .map(([name, value]) => `${name}：${String(value).trim()}`).join('；');
}

/**
 * 多值标签列（场景标签 / 道具标签）拆成逐个标签。
 *
 * 「无」是后端 prompt 明确要求的「本镜没有」写法（见 `text_node.py` 的字段规范），
 * 它不是标签 —— 不排掉，资产视图里会长出一张叫「无」的卡片。
 */
const NO_TAG_VALUES = new Set(['无', '没有', 'none', 'n/a', '-', '—']);

export function isScriptNoValue(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return normalized.length === 0 || NO_TAG_VALUES.has(normalized);
}

/**
 * 「本镜没有说话的人」的占位写法，与后端 `script_contract.NO_DIALOGUE_VALUES` 同一口径。
 *
 * 比 NO_TAG_VALUES 多几条口语写法：写成「无台词」却没被认出来，这一镜会被路由成
 * 外部配音，模型照着把「无台词」两个字念出来。
 */
const NO_DIALOGUE_VALUES = new Set([
  '',
  '无',
  '没有',
  '无台词',
  '无对白',
  '没有台词',
  '没有对白',
  'none',
  'n/a',
]);

export function isScriptNoDialogue(value: string): boolean {
  return NO_DIALOGUE_VALUES.has(value.trim().toLowerCase());
}

export function splitScriptTags(value: string): string[] {
  return value
    .split(/[、,，;；/|]+/)
    .map((tag) => tag.trim())
    .filter((tag) => !isScriptNoValue(tag));
}

/**
 * 读取与多值标签平行的 asset id / locks / dependencies 列。
 *
 * 旧脚本行没有这些列时返回空串，全部回落到 role + 名字的兼容身份。
 * 数组输入按标签下标取值；字符串输入优先按 JSON 数组解析，失败后按同一套
 * 标签分隔符切分，避免新字段再发明第二种分隔语法。
 */
export function parallelCellTextAt(
  row: FreezoneStoryScriptRow,
  key: string,
  index: number,
): string {
  const raw = row[key];
  if (Array.isArray(raw)) return cellText({ [key]: raw[index] }, key);
  const text = cellText(row, key);
  if (text.length === 0) return '';
  if (text.startsWith('[')) {
    try {
      const parsed = JSON.parse(text) as unknown;
      if (Array.isArray(parsed)) return cellText({ [key]: parsed[index] }, key);
    } catch {
      // Not JSON; fall through to the same separator rules as tags.
    }
  }
  return splitScriptTags(text)[index] ?? '';
}

/** 行的稳定身份；旧行无 `shot_id` 时回落到 T-047 之前的兼容键。 */
export function scriptRowKey(row: FreezoneStoryScriptRow, index: number): string {
  return cellText(row, 'shot_id') || legacyScriptRowKey(row, index);
}

/**
 * 整表行标识：优先持久化的 `shot_id`；旧行按兼容键确定性迁移，重复时补 `#n`。
 */
export function buildScriptRowKeys(rows: FreezoneStoryScriptRow[]): string[] {
  return ensureScriptShotIdentities(rows).rows.map(
    (row) => String(row.shot_id ?? '').trim(),
  );
}

/** 展示用镜号：缺列时回落下标 + 1（与 LibTV 的 `第 N 镜` 兜底一致）。 */
export function scriptRowShotNumber(row: FreezoneStoryScriptRow, index: number): string {
  const shotNo = cellText(row, 'display_shot_no') || cellText(row, 'shot_no');
  return shotNo.length > 0 ? shotNo : String(index + 1);
}

export interface ScriptRowCharacter {
  slot: number;
  name: string;
  description: string;
  imageUrl: string | null;
}

/** 该行实际填写了的角色槽（名字 / 描述 / 图 任一非空即算）。 */
export function rowCharacters(row: FreezoneStoryScriptRow): ScriptRowCharacter[] {
  const characters: ScriptRowCharacter[] = [];
  for (let slot = 1; slot <= SCRIPT_CHARACTER_SLOT_COUNT; slot += 1) {
    const rawName = cellText(row, `character_${slot}`);
    const rawDescription = cellText(row, `character_description_${slot}`);
    const name = isScriptNoValue(rawName) ? '' : rawName;
    const description = isScriptNoValue(rawDescription) ? '' : rawDescription;
    const imageUrl = cellImageUrl(row, `character_image_${slot}`);
    if (name.length === 0 && description.length === 0 && !imageUrl) continue;
    characters.push({ slot, name, description, imageUrl });
  }
  return characters;
}

/** 各行占用角色槽位数的最大值，CSV 用它决定要展开几组角色列（LibTV 同款动态列）。 */
export function maxOccupiedCharacterSlots(rows: FreezoneStoryScriptRow[]): number {
  return rows.reduce((max, row) => {
    const slots = rowCharacters(row).map((character) => character.slot);
    return slots.length > 0 ? Math.max(max, ...slots) : max;
  }, 0);
}

/** 该行所有角色图 URL（生成分镜时作为参考图，对应 LibTV 的 `imageList`）。 */
export function rowCharacterImageUrls(row: FreezoneStoryScriptRow): string[] {
  return rowCharacters(row)
    .map((character) => character.imageUrl)
    .filter((url): url is string => Boolean(url));
}

/**
 * 分镜图提示词：优先「分镜提示词」，缺失回落「画面描述」。
 * 与 LibTV 一致（`params.prompt = imageGenerationPrompt || plotDescription`）。
 */
export function rowImagePrompt(row: FreezoneStoryScriptRow): string {
  const prompt = cellText(row, 'shot_prompt');
  if (!isScriptNoValue(prompt)) return prompt;
  const description = cellText(row, 'visual_description');
  return isScriptNoValue(description) ? '' : description;
}

/** 该镜的参考帧图（表格里的「参考」列）。 */
export function rowReferenceImageUrl(row: FreezoneStoryScriptRow): string | null {
  return cellImageUrl(row, 'reference');
}

export interface ScriptAssetEntry {
  key: string;
  name: string;
  description: string;
  imageUrl: string | null;
  /** 显式资产身份；缺席时台账回落到 role + 名字兼容键。 */
  assetId: string;
  /** 生成该资产时必须锁定的语义字段。 */
  identityLocks: string[];
  /** 该资产依赖的其它稳定资产身份。 */
  dependencies: string[];
  /** 会影响资产定义的内容输入，不含生成结果 URL。 */
  sourceImageUrl: string | null;
  /** 出现过的镜号（保持行序，去重）。 */
  shotNumbers: string[];
}

/** 资产视图 · 角色：按角色名聚合跨镜出场。 */
export function collectScriptCharacters(rows: FreezoneStoryScriptRow[]): ScriptAssetEntry[] {
  const entries = new Map<string, ScriptAssetEntry>();
  rows.forEach((row, index) => {
    const shotNumber = scriptRowShotNumber(row, index);
    rowCharacters(row).forEach((character) => {
      const name = character.name || `角色${character.slot}`;
      const existing = entries.get(name);
      if (existing) {
        if (!existing.imageUrl && character.imageUrl) existing.imageUrl = character.imageUrl;
        if (!existing.sourceImageUrl && character.imageUrl) existing.sourceImageUrl = character.imageUrl;
        if (!existing.assetId) {
          existing.assetId =
            cellText(row, `character_asset_id_${character.slot}`)
            || cellText(row, `character_id_${character.slot}`);
        }
        if (existing.identityLocks.length === 0) {
          existing.identityLocks = splitScriptTags(
            cellText(row, `character_identity_locks_${character.slot}`),
          );
        }
        if (existing.dependencies.length === 0) {
          existing.dependencies = splitScriptTags(
            cellText(row, `character_dependencies_${character.slot}`),
          );
        }
        if (existing.description.length === 0 && character.description.length > 0) {
          existing.description = character.description;
        }
        if (!existing.shotNumbers.includes(shotNumber)) existing.shotNumbers.push(shotNumber);
        return;
      }
      entries.set(name, {
        key: name,
        name,
        description: character.description,
        imageUrl: character.imageUrl,
        assetId:
          cellText(row, `character_asset_id_${character.slot}`)
          || cellText(row, `character_id_${character.slot}`),
        identityLocks: splitScriptTags(
          cellText(row, `character_identity_locks_${character.slot}`),
        ),
        dependencies: splitScriptTags(
          cellText(row, `character_dependencies_${character.slot}`),
        ),
        sourceImageUrl: character.imageUrl,
        shotNumbers: [shotNumber],
      });
    });
  });
  return [...entries.values()];
}

/** 资产视图 · 场景：按「场景标签」聚合（LibTV 资产锚点里的场景族）。 */
function namedAssetDescription(row: FreezoneStoryScriptRow, field: 'scene_descriptions' | 'prop_descriptions', name: string): string {
  const definitions = row[field];
  if (!definitions || typeof definitions !== 'object' || Array.isArray(definitions)) return '';
  return typeof definitions[name] === 'string' ? definitions[name].trim() : '';
}

export function collectScriptScenes(rows: FreezoneStoryScriptRow[]): ScriptAssetEntry[] {
  const entries = new Map<string, ScriptAssetEntry>();
  rows.forEach((row, index) => {
    // ponytail: keep the asset ledger spatial. Detail-level labels stay in the shot prompt;
    // upgrade to a structured scene taxonomy when the script schema gains one.
    const tags = splitScriptTags(cellText(row, 'scene_tags')).filter(isReusableSceneTag);
    if (tags.length === 0) return;
    const shotNumber = scriptRowShotNumber(row, index);
    const reference = rowReferenceImageUrl(row);
    tags.forEach((tag, tagIndex) => {
      const description = namedAssetDescription(row, 'scene_descriptions', tag);
      const existing = entries.get(tag);
      if (existing) {
        if (!existing.description && description) existing.description = description;
        if (!existing.imageUrl && reference) existing.imageUrl = reference;
        if (!existing.assetId) {
          existing.assetId = parallelCellTextAt(row, 'scene_asset_ids', tagIndex);
        }
        if (existing.identityLocks.length === 0) {
          existing.identityLocks = splitScriptTags(cellText(row, 'scene_identity_locks'));
        }
        if (existing.dependencies.length === 0) {
          existing.dependencies = splitScriptTags(cellText(row, 'scene_dependencies'));
        }
        if (!existing.shotNumbers.includes(shotNumber)) existing.shotNumbers.push(shotNumber);
        return;
      }
      entries.set(tag, {
        key: tag,
        name: tag,
        description,
        imageUrl: reference,
        assetId: parallelCellTextAt(row, 'scene_asset_ids', tagIndex),
        identityLocks: splitScriptTags(cellText(row, 'scene_identity_locks')),
        dependencies: splitScriptTags(cellText(row, 'scene_dependencies')),
        sourceImageUrl: null,
        shotNumbers: [shotNumber],
      });
    });
  });
  return [...entries.values()];
}

const NON_ASSET_SCENE_TERMS = [
  '光带', '柔光', '轮廓光', '尘', '绒絮', '气流', '倒影', '留白', '负空间',
  '材质', '纹理', '微距', '焦外', '迎光', '包边', '打磨面', '经纬网格',
  '浅层水痕', '水珠', '背景色',
];

/** 镜头细节不是可复用场景资产，避免资产弹层被一镜一景撑爆。 */
export function isReusableSceneTag(value: string): boolean {
  const normalized = value.trim();
  return normalized.length > 0 && !NON_ASSET_SCENE_TERMS.some((term) => normalized.includes(term));
}

/**
 * 资产视图 · 道具：按「道具标签」聚合（LibTV 资产台账的第三族 `props`）。
 *
 * 与场景的关键区别是**图从哪来**：场景能借该行的参考帧当预览（场景就在画面里），
 * 道具不能 —— 参考帧是整镜的构图，拿它当「这把刀长什么样」会误导人。后端目前也不回填
 * 道具图（`prop_tags` 只是标签），所以道具卡一律走「无图」占位，等真正的道具图链路接上
 * 再填。这不是缺陷，是刻意留白：宁愿说「还没有」，也不要给一张错图。
 */
export function collectScriptProps(rows: FreezoneStoryScriptRow[]): ScriptAssetEntry[] {
  const entries = new Map<string, ScriptAssetEntry>();
  rows.forEach((row, index) => {
    const tags = splitScriptTags(cellText(row, 'prop_tags'));
    if (tags.length === 0) return;
    const shotNumber = scriptRowShotNumber(row, index);
    tags.forEach((tag, tagIndex) => {
      const description = namedAssetDescription(row, 'prop_descriptions', tag);
      const existing = entries.get(tag);
      if (existing) {
        if (!existing.description && description) existing.description = description;
        if (!existing.assetId) {
          existing.assetId = parallelCellTextAt(row, 'prop_asset_ids', tagIndex);
        }
        if (existing.identityLocks.length === 0) {
          existing.identityLocks = splitScriptTags(cellText(row, 'prop_identity_locks'));
        }
        if (existing.dependencies.length === 0) {
          existing.dependencies = splitScriptTags(cellText(row, 'prop_dependencies'));
        }
        if (!existing.shotNumbers.includes(shotNumber)) existing.shotNumbers.push(shotNumber);
        return;
      }
      entries.set(tag, {
        key: tag,
        name: tag,
        description,
        imageUrl: null,
        assetId: parallelCellTextAt(row, 'prop_asset_ids', tagIndex),
        identityLocks: splitScriptTags(cellText(row, 'prop_identity_locks')),
        dependencies: splitScriptTags(cellText(row, 'prop_dependencies')),
        sourceImageUrl: null,
        shotNumbers: [shotNumber],
      });
    });
  });
  return [...entries.values()];
}
