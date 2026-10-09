// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import { sha256Hex } from './scriptAssets';

function stableJson(value: unknown): string {
  if (value === null) return 'null';
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .filter((key) => record[key] !== undefined)
      .map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

const ROW_FINGERPRINT_METADATA_KEYS = new Set([
  'shot_id',
  'shot_order',
  'shot_no',
  'display_shot_no',
  'keyframe_index',
]);

/**
 * Same canonical JSON + SHA-256 contract as
 * `script_contract.script_row_fingerprint`.
 *
 * Only production content enters the fingerprint. Identity is checked by the
 * row key and display numbering must never trigger a paid rerender.
 */
export function scriptRowFingerprint(row: FreezoneStoryScriptRow, index = 0): string {
  const production = Object.fromEntries(
    Object.entries(row).filter(
      ([key, value]) => value !== undefined && !ROW_FINGERPRINT_METADATA_KEYS.has(key),
    ),
  );
  return sha256Hex(stableJson({ order: index + 1, row: production }));
}

/** Includes identities and the director plan to protect edits made during a rewrite. */
export function scriptGenerationInputFingerprint(data: Record<string, unknown>): string {
  return sha256Hex(stableJson({ result: data.scriptResult, title: data.scriptTitle,
    pending: data.scriptDirectorPlanNeedsSync === true }));
}
