// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { MyBuddyAction } from "@/features/companion/mybuddy-actions";

/**
 * petdex 宠物接入。
 *
 * petdex（https://petdex.dev）的宠物是「精灵图」：每只一张 spritesheet，按网格切帧，
 * 行 = 动画状态。2026-09-16 起这是**唯一**的搭子形象实现（此前的 Piko CSS 像素小人
 * 已连同其道具、节日与奖励系统一并删除）。
 *
 * 纯本地方案：宠物精灵图（webp）下载到 public/petdex/，目录由同源清单
 * public/petdex/pets.json 维护（{@link fetchLocalPets} 读取）——加宠物 = 放一张
 * <slug>.webp + 在 pets.json 加一条；用户也可经导入弹窗存进 IndexedDB。不碰任何
 * CDN / manifest（线上限流 + 资源浪费）。
 *
 * ⚠️ 授权：petdex 源码 MIT，但宠物素材归各提交者所有、各自挑选 license，pet.json
 * 里并不携带 license 字段。2026-09-16 起内置目录只收录自有角色（枝枝）；此前入库的
 * 第三方提交（银月 / 咕咕嘎嘎 / 流光小狼崽 / 噜噜）已连同精灵图一并移出仓库。
 * 再加宠物前必须确认授权，未确认的不入库。
 */

// 精灵图网格约定（8 列 × 9 行，每帧 192×208）。8 列是「格子上限」，每个状态实际帧数
// 不同（见 PETDEX_STATES），按各自帧数循环、跳过该行多余的空格。
export const PETDEX_GRID_COLS = 8;
export const PETDEX_GRID_ROWS = 9;
export const PETDEX_FRAME_MS = 150; // 每帧时长，保证各状态播放速度一致

export interface PetdexState {
  /** 精灵图里的行号（0 起）。 */
  row: number;
  /** 该状态实际帧数（该行从第 0 列起的有效帧数）。 */
  frames: number;
}

// petdex 标准 9 状态布局（来源：petdex 宠物详情页，多个精灵图实测一致）。每行帧数不同。
export const PETDEX_STATES = {
  idle: { row: 0, frames: 6 },
  runRight: { row: 1, frames: 8 },
  runLeft: { row: 2, frames: 8 },
  waving: { row: 3, frames: 4 },
  jumping: { row: 4, frames: 5 },
  failed: { row: 5, frames: 8 },
  waiting: { row: 6, frames: 6 },
  running: { row: 7, frames: 6 },
  review: { row: 8, frames: 6 },
} as const satisfies Record<string, PetdexState>;

export type PetdexStateName = keyof typeof PETDEX_STATES;

/** 9 个状态的有序列表（名字 + 展示标签）——用于「状态模拟」下拉 & 点击宠物循环预览。 */
export const PETDEX_STATE_OPTIONS: { name: PetdexStateName; label: string }[] = [
  { name: "idle", label: "Idle" },
  { name: "runRight", label: "Run Right" },
  { name: "runLeft", label: "Run Left" },
  { name: "waving", label: "Waving" },
  { name: "jumping", label: "Jumping" },
  { name: "failed", label: "Failed" },
  { name: "waiting", label: "Waiting" },
  { name: "running", label: "Running" },
  { name: "review", label: "Review" },
];

/**
 * 把 controller 的动作（已由任务中心状态映射而来：运行中→typing、成功→flag、
 * 异常→repair、问候→peek …）再映射到 petdex 的 9 个标准状态，实现「任务状态 ↔ 宠物
 * 动作」同步。没有对应语义的闲置/节日动作统一归 idle。
 */
export function petdexStateForAction(action: MyBuddyAction): PetdexState {
  switch (action) {
    case "typing":
      return PETDEX_STATES.running; // 任务进行中 → Running
    case "flag":
      return PETDEX_STATES.waving; // 任务成功 → Waving
    case "repair":
      return PETDEX_STATES.failed; // 任务失败 → Failed
    default:
      return PETDEX_STATES.idle; // 常规状态（待机/闲置/节日/问候）→ Idle
  }
}

// ─── 本地宠物目录 ───────────────────────────────────────────────────────────
/** 一只可选用的宠物形象（内置目录条目 / 用户导入条目共用此形状）。 */
export interface PetdexCatalogEntry {
  slug: string;
  displayName: string;
  /** 同源精灵图地址，一般是 /petdex/<slug>.webp。 */
  spritesheetUrl: string;
  submittedBy?: string;
  /** 网格非默认 8×9 时，在 pets.json 里覆盖（行序/帧数走 petdex 标准布局）。 */
  cols?: number;
  rows?: number;
  /** 来自用户导入（IndexedDB）。spritesheetUrl 是会话级 blob URL，刷新后需按 slug 重解析。 */
  imported?: boolean;
}

const LOCAL_PETS_URL = "/petdex/pets.json";

/**
 * 内置默认搭子：pets.json 里的第一只（也是当前唯一一只）。首启、以及用户选中的那只
 * 已下架时都回落到它。
 */
export const DEFAULT_BUILTIN_PET_SLUG = "zhizhi";

/**
 * 解析当前要渲染的宠物形象。
 *
 * 内置宠物以目录（pets.json）为准取条目，而不是直接用持久化下来的那份——被下架/改名
 * 的 slug 因此不会留下一个 404 的空形象，而是回落到目录第一只；目录尚未加载时返回
 * null（不渲染）。导入宠物按 slug 取会话级 blob URL，取不到同样回落。
 */
export function resolveActivePet({
  kind,
  selected,
  importedBySlug,
  catalog,
}: {
  kind: string;
  selected: PetdexCatalogEntry | null;
  importedBySlug: ReadonlyMap<string, PetdexCatalogEntry>;
  catalog: readonly PetdexCatalogEntry[];
}): PetdexCatalogEntry | null {
  if (selected?.imported) {
    return importedBySlug.get(selected.slug) ?? catalog[0] ?? null;
  }
  const slug = selected?.slug ?? kind;
  return catalog.find((pet) => pet.slug === slug) ?? catalog[0] ?? null;
}

/**
 * 读取本地宠物目录（同源、稳定、不碰网络）。pets.json 是内置宠物的**唯一**来源；
 * 清单缺失/损坏（如生产未部署该原型素材）则返回空，画廊只剩「我的宠物」(IndexedDB)。
 */
export async function fetchLocalPets(signal?: AbortSignal): Promise<PetdexCatalogEntry[]> {
  try {
    const response = await fetch(LOCAL_PETS_URL, { signal, cache: "no-store" });
    if (response.ok) {
      const data = (await response.json()) as { pets?: Array<Record<string, unknown>> };
      return (data.pets ?? [])
        .filter(
          (p): p is Record<string, unknown> =>
            typeof p?.slug === "string" && typeof p?.spritesheetUrl === "string",
        )
        .map((p) => ({
          slug: p.slug as string,
          displayName: (p.displayName as string) || (p.slug as string),
          spritesheetUrl: p.spritesheetUrl as string,
          submittedBy: typeof p.submittedBy === "string" ? p.submittedBy : undefined,
          cols: typeof p.cols === "number" ? p.cols : undefined,
          rows: typeof p.rows === "number" ? p.rows : undefined,
        }));
    }
  } catch {
    // 清单缺失 / 解析失败 → 空目录（仅保留用户导入）
  }
  return [];
}
