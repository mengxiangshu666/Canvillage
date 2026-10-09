// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * `/freezone/video/models` 里两组「原样搬运给界面」的字段：能力快照版本 + 出线合同来源。
 *
 * 放在一起是因为它们在 `ops.ts` 的同一个位置、被同一次调用取出来，而 `ops.ts` 是登记在案的
 * 巨型文件（门禁要求只能缩小），连多一行 import 都要省。前端只做原样搬运，不重新推断来源：
 * 渠道条目是唯一真值，渠道自带的合同随渠道增删，源码里的那 8 份数据包只是「未验证种子」，
 * 两处都没有才是通用兜底。
 */
export type FreezoneVideoCapabilitySource =
  | "profile"
  | "upstream"
  | "runtime"
  | "unknown";

export interface VideoCapabilityRevisionFields {
  /** Stable identity for the exact capability snapshot used to build this picker. */
  capabilityRevision?: string;
  /** Evidence source for the capability snapshot, e.g. `upstream` or `profile`. */
  capabilitySource?: FreezoneVideoCapabilitySource;
}

export interface VideoWireContractFields {
  wireContractSource?: "channel" | "seed" | "default";
  wireContractSourceLabel?: string;
  wireContractProfileId?: string;
  wireContractEvidence?: string;
  wireContractVerified?: boolean;
  wireContractConflictsWithSeed?: string[];
  wireContractError?: string;
}

export interface FreezoneVideoModelFields
  extends VideoCapabilityRevisionFields,
    VideoWireContractFields {}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return undefined;
}

function firstStringArray(...values: unknown[]): string[] {
  for (const value of values) {
    if (Array.isArray(value)) {
      return value
        .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
        .map((item) => item.trim());
    }
  }
  return [];
}

function videoCapabilityRevisionFields(
  entry: Record<string, unknown>,
): VideoCapabilityRevisionFields {
  const capabilityRevision = firstString(
    entry.capabilityRevision,
    entry.capability_revision,
  );
  const sourceRaw = firstString(
    entry.capabilitySource,
    entry.capability_source,
  )?.toLowerCase();
  const capabilitySource = ["profile", "upstream", "runtime", "unknown"].includes(
    sourceRaw ?? "",
  )
    ? (sourceRaw as FreezoneVideoCapabilitySource)
    : undefined;
  return {
    ...(capabilityRevision ? { capabilityRevision } : {}),
    ...(capabilitySource ? { capabilitySource } : {}),
  };
}

function videoWireContractFields(
  entry: Record<string, unknown>,
): VideoWireContractFields {
  const sourceRaw = firstString(entry.wireContractSource, entry.wire_contract_source);
  const wireContractSource = ["channel", "seed", "default"].includes(sourceRaw ?? "")
    ? (sourceRaw as VideoWireContractFields["wireContractSource"])
    : undefined;
  const conflicts = firstStringArray(
    entry.wireContractConflictsWithSeed,
    entry.wire_contract_conflicts_with_seed,
  );
  return {
    ...(wireContractSource ? { wireContractSource } : {}),
    ...(firstString(entry.wireContractSourceLabel, entry.wire_contract_source_label)
      ? {
          wireContractSourceLabel: firstString(
            entry.wireContractSourceLabel,
            entry.wire_contract_source_label,
          ),
        }
      : {}),
    ...(firstString(entry.wireContractProfileId, entry.wire_contract_profile_id)
      ? {
          wireContractProfileId: firstString(
            entry.wireContractProfileId,
            entry.wire_contract_profile_id,
          ),
        }
      : {}),
    ...(firstString(entry.wireContractEvidence, entry.wire_contract_evidence)
      ? {
          wireContractEvidence: firstString(
            entry.wireContractEvidence,
            entry.wire_contract_evidence,
          ),
        }
      : {}),
    ...(entry.wireContractVerified === true || entry.wire_contract_verified === true
      ? { wireContractVerified: true }
      : {}),
    ...(conflicts.length > 0 ? { wireContractConflictsWithSeed: conflicts } : {}),
    ...(firstString(entry.wireContractError, entry.wire_contract_error)
      ? { wireContractError: firstString(entry.wireContractError, entry.wire_contract_error) }
      : {}),
  };
}

/** 一次取齐两组字段；`ops.ts` 只调这一个入口。 */
export function freezoneVideoModelFields(
  entry: Record<string, unknown>,
): FreezoneVideoModelFields {
  return {
    ...videoCapabilityRevisionFields(entry),
    ...videoWireContractFields(entry),
  };
}
