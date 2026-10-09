// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 模型高级参数（`extraParams`）→ 请求字段 `advanced_settings` 的唯一边界。
 *
 * 节点的参数面板把模型合同声明的每个高级参数都写进同一张 `extraParams` 表，
 * 而请求里 `quality` 已是一等字段，所以这里把它摘出去，空值也一并丢掉；
 * 后端再按所选模型的合同过滤并归一化，未知键不会透传上游。
 */
export function buildImageAdvancedSettings(
  extraParams: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  if (!extraParams) return null;
  const settings: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(extraParams)) {
    if (key === 'quality') continue;
    if (value === undefined || value === null || value === '') continue;
    settings[key] = value;
  }
  return Object.keys(settings).length > 0 ? settings : null;
}
