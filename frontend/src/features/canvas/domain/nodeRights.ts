// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 生成物的权属标记：署名（copyrightChain）与保护策略（protectionType）。
 *
 * 对齐 LibTV 节点的同名字段，但只保留村长需要的语义——「谁产出的」和「产出物怎么被
 * 保护」。两者都是节点 data 上的旁路元数据：不参与生成请求、不进 prompt，只在展示、
 * 交接和导出时透出，因此可以随时补写而无需重跑生成。
 */

/** 目前只认水印一种保护；未来扩展新值时在这里加，收敛逻辑保持不变。 */
export type NodeProtectionType = "watermark";

export interface NodeCopyrightChain {
  /** 署名名称。空串视为未署名，节点上不显示署名角标。 */
  name: string;
  /** 署名主体 ID（用户 / 团队）。未知时为空串，不阻塞署名展示。 */
  uuid: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

/**
 * 收敛任意来源的署名对象。没有名称就没有可展示的署名，返回 `null` 而不是补一个空壳，
 * 免得节点上出现「署名：（空）」。
 */
export function normalizeCopyrightChain(value: unknown): NodeCopyrightChain | null {
  const record = asRecord(value);
  if (!record) return null;
  const name = nonEmptyString(record.name);
  if (!name) return null;
  return { name, uuid: nonEmptyString(record.uuid) ?? "" };
}

export function normalizeProtectionType(value: unknown): NodeProtectionType | null {
  return typeof value === "string" && value.trim().toLowerCase() === "watermark"
    ? "watermark"
    : null;
}

/**
 * 生成成功时落库的权属补丁。取不到值的字段直接省略，不写 `null`——节点 data 走整包
 * PUT，写进 `null` 会覆盖用户先前手动填的署名。
 */
export function buildRightsPatch(input: {
  copyrightChain?: unknown;
  protectionType?: unknown;
}): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  const chain = normalizeCopyrightChain(input.copyrightChain);
  const protection = normalizeProtectionType(input.protectionType);
  if (chain) patch.copyrightChain = chain;
  if (protection) patch.protectionType = protection;
  return patch;
}

/** 节点是否带任何权属标记；决定要不要渲染角标。 */
export function hasNodeRights(data: Record<string, unknown> | null | undefined): boolean {
  if (!data) return false;
  return (
    normalizeCopyrightChain(data.copyrightChain) !== null ||
    normalizeProtectionType(data.protectionType) !== null
  );
}

/**
 * 默认署名：本机记住上一次填过的名字，下次给新节点填署名时预填。
 *
 * 村长没有「画布作者」这个现成字段（`created_by` 只在服务端画布记录上，前端运行时
 * 拿不到），所以默认值只能来自用户自己。存在 localStorage 而不是画布 metadata，
 * 是因为它是「这个人习惯署什么名」，换画布也该沿用。
 *
 * 读写都吞异常：无痕模式/禁用存储时降级成「没有默认署名」，不影响署名本身可用。
 */
const DEFAULT_COPYRIGHT_STORAGE_KEY = "village:default-copyright";

export function readDefaultCopyrightName(): string {
  try {
    return normalizeCopyrightChain(
      JSON.parse(window.localStorage.getItem(DEFAULT_COPYRIGHT_STORAGE_KEY) ?? "null"),
    )?.name ?? "";
  } catch {
    return "";
  }
}

export function writeDefaultCopyrightName(name: string): void {
  const chain = normalizeCopyrightChain({ name });
  try {
    if (chain) {
      window.localStorage.setItem(DEFAULT_COPYRIGHT_STORAGE_KEY, JSON.stringify(chain));
    } else {
      window.localStorage.removeItem(DEFAULT_COPYRIGHT_STORAGE_KEY);
    }
  } catch {
    // 存储不可用不影响本次署名落库。
  }
}

/**
 * 生成成功时自动补署名。只在「节点还没有署名」且「本机已经设过默认署名」时才补——
 * 用户从没署过名就什么都不做，一旦署过一次，之后新产出的媒体自动继承，手动改过的
 * 节点不会被覆盖。
 */
export function buildGeneratedRightsPatch(
  existingData: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  if (normalizeCopyrightChain(existingData?.copyrightChain)) {
    return {};
  }
  const name = readDefaultCopyrightName();
  if (!name) return {};
  return { copyrightChain: { name, uuid: "" } };
}
