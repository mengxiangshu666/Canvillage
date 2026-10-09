// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 视频模型的出线合同来自哪里 —— 节点选择器上那行小字。
 *
 * 渠道条目是唯一真值：渠道自带的合同随渠道增删，源码里的数据包只是「未验证
 * 种子」，两处都不占才是通用兜底。这里只翻译后端给的来源，不在前端重新推断；
 * 后端没给来源就返回空串，界面什么都不画。
 */
export function wireContractChip(model: {
  wireContractSource?: "channel" | "seed" | "default";
  wireContractVerified?: boolean;
  wireContractError?: string;
}): string {
  if (model.wireContractError) return "合同读不动";
  if (model.wireContractSource === "channel") {
    return model.wireContractVerified ? "渠道合同·已验证" : "渠道合同·未验证";
  }
  if (model.wireContractSource === "seed") return "未验证种子";
  if (model.wireContractSource === "default") return "通用兜底";
  return "";
}
