// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { cn } from "@/lib/utils";
import type { DirectVideoModelConfig } from "@/lib/queries/model-gateway";

export type VideoWireContractBadge = {
  tone: "verified" | "unverified" | "seed" | "fallback";
  label: string;
  detail: string;
  conflicts: string[];
  error: string;
};

const BADGE_CLASS: Record<VideoWireContractBadge["tone"], string> = {
  verified: "border-emerald-300/30 bg-emerald-300/10 text-emerald-200",
  unverified: "border-amber-300/30 bg-amber-300/10 text-amber-200",
  seed: "border-white/15 bg-white/[0.055] text-muted-foreground",
  fallback: "border-white/10 bg-white/[0.035] text-muted-foreground",
};

function evidenceDetail(evidence: string): string {
  if (evidence === "submit") return "真实提交验证";
  if (evidence === "operator") return "人工指定";
  if (evidence === "probe") return "只探测过模型目录，不算验证";
  return evidence;
}

/**
 * 出线合同来源徽标。
 *
 * 渠道条目是唯一真值：渠道自带的合同随渠道增删；源码里的 8 个包已经降级成
 * 「未验证种子」；两处都不占时才是通用兜底。这里只把后端给的来源翻译成人看
 * 得懂的徽标，不在前端重新推断来源。
 */
export function videoWireContractBadge(
  saved: DirectVideoModelConfig | undefined,
): VideoWireContractBadge | null {
  if (!saved) return null;
  const conflicts = saved.wireContractConflictsWithSeed ?? [];
  const error = (saved.wireContractError ?? "").trim();
  if (error) {
    return { tone: "unverified", label: "合同读不动", detail: "", conflicts, error };
  }
  const source = saved.wireContractSource;
  if (!source) return null;
  const profileId = (saved.wireContractProfileId ?? "").trim();
  const seedProfileId = (saved.wireContractSeedProfileId ?? "").trim();
  if (source === "channel") {
    const verified = saved.wireContractVerified === true;
    return {
      tone: verified ? "verified" : "unverified",
      label: verified ? "渠道合同 · 已验证" : "渠道合同 · 未验证",
      detail: [profileId, evidenceDetail((saved.wireContractEvidence ?? "").trim())]
        .filter(Boolean)
        .join(" · "),
      conflicts,
      error: "",
    };
  }
  if (source === "seed") {
    return {
      tone: "seed",
      label: "源码种子 · 未验证",
      detail: seedProfileId || profileId,
      conflicts,
      error: "",
    };
  }
  return {
    tone: "fallback",
    label: "通用兜底",
    detail: seedProfileId || profileId,
    conflicts,
    error: "",
  };
}

/**
 * 视频模型卡片头的一排徽标：协议 + 出线合同来源，附冲突字段与读不动原因。
 * 放在 `settings/models/` 而不是面板本体里，是为了不让巨型文件继续长大。
 */
export function VideoModelHeaderChips({
  protocolLabel,
  saved,
}: {
  protocolLabel: string;
  saved?: DirectVideoModelConfig;
}) {
  const badge = videoWireContractBadge(saved);
  return (
    <>
      <span className="rounded border border-cyan-300/35 bg-cyan-300/10 px-2 py-0.5 text-[10px] font-medium text-cyan-200">
        协议：{protocolLabel}
      </span>
      {badge ? (
        <span
          className={cn(
            "rounded border px-2 py-0.5 text-[10px] font-medium",
            BADGE_CLASS[badge.tone],
          )}
          title={badge.detail || undefined}
        >
          合同：{badge.label}
        </span>
      ) : null}
      {badge && (badge.error || badge.conflicts.length > 0 || badge.detail) ? (
        <div className="w-full basis-full space-y-0.5 text-[10px] leading-relaxed">
          {badge.error ? <p className="text-rose-300">合同有问题：{badge.error}</p> : null}
          {badge.conflicts.length > 0 ? (
            <p className="text-amber-300">
              实际出线用渠道合同；与同名源码种子不一致的字段：
              {badge.conflicts.join("、")}
            </p>
          ) : null}
          {badge.detail && !badge.error ? (
            <p className="text-muted-foreground">合同依据：{badge.detail}</p>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
