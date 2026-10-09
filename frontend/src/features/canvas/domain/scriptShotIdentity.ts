// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';

/**
 * Stable script-row identity.
 *
 * `shot_id` is the production identity. `shot_order` is the current sequence and
 * `display_shot_no` is only what the user sees. Legacy rows are migrated
 * deterministically from the old scriptRowKey rules so an old canvas does not
 * receive a different identity after every refresh.
 */
export interface ScriptShotIdentityMigration {
  rows: FreezoneStoryScriptRow[];
  changed: boolean;
}

export const SCRIPT_SHOT_ID_NODE_FIELD = 'scriptShotId';

function cellText(value: unknown): string {
  if (value == null) return '';
  return typeof value === 'string' || typeof value === 'number' ? String(value).trim() : '';
}

/** The pre-T-047 row key. Kept verbatim for deterministic compatibility. */
export function legacyScriptRowKey(row: FreezoneStoryScriptRow, index: number): string {
  const shotNo = cellText(row.shot_no);
  if (shotNo.length > 0) return `shot:${shotNo}`;
  const keyframe = row.keyframe_index;
  if (typeof keyframe === 'number' && Number.isFinite(keyframe)) {
    return `kf:${keyframe}`;
  }
  return `idx:${index}`;
}

function uniqueIdentity(candidate: string, used: Set<string>): string {
  if (!used.has(candidate)) {
    used.add(candidate);
    return candidate;
  }
  let suffix = 2;
  while (used.has(`${candidate}#${suffix}`)) suffix += 1;
  const unique = `${candidate}#${suffix}`;
  used.add(unique);
  return unique;
}

/** New identities are opaque and never derived from the visible shot number. */
export function createScriptShotId(): string {
  const randomUuid = globalThis.crypto?.randomUUID?.();
  if (randomUuid) return `shot_${randomUuid.replace(/-/g, '')}`;
  const random = Math.random().toString(36).slice(2);
  return `shot_${Date.now().toString(36)}_${random}`;
}

/** Read the canonical identity from an image/video/timeline node. */
export function readScriptShotId(
  data: Record<string, unknown> | null | undefined,
): string | null {
  const canonical = data?.[SCRIPT_SHOT_ID_NODE_FIELD];
  if (typeof canonical === 'string' && canonical.trim().length > 0) {
    return canonical.trim();
  }
  for (const key of ['scriptRowKey', 'scriptShotRowKey']) {
    const legacy = data?.[key];
    if (typeof legacy === 'string' && legacy.trim().length > 0) {
      return legacy.trim();
    }
  }
  return null;
}

/**
 * Fill or normalize the three identity fields without reordering rows.
 *
 * Existing valid IDs are preserved. Missing IDs fall back to the exact legacy
 * row key, so a persisted old canvas keeps the same identity across reloads.
 */
export function ensureScriptShotIdentities(
  rows: readonly FreezoneStoryScriptRow[],
): ScriptShotIdentityMigration {
  const used = new Set<string>();
  let changed = false;
  const migrated = rows.map((row, index) => {
    const order = index + 1;
    const currentId = cellText(row.shot_id);
    const shotId = uniqueIdentity(currentId || legacyScriptRowKey(row, index), used);
    const rawShotNo = cellText(row.shot_no);
    const displayShotNo =
      rawShotNo || cellText(row.display_shot_no) || String(order);
    const currentOrder = row.shot_order;

    if (
      currentId === shotId &&
      currentOrder === order &&
      cellText(row.display_shot_no) === displayShotNo
    ) {
      return row;
    }

    changed = true;
    return {
      ...row,
      shot_id: shotId,
      shot_order: order,
      display_shot_no: displayShotNo,
    };
  });
  return { rows: changed ? migrated : [...rows], changed };
}

/** Normalize a full `{ title, rows }` result while preserving object identity when clean. */
export function normalizeScriptResultIdentities(result: unknown): unknown {
  if (!result || typeof result !== 'object' || Array.isArray(result)) return result;
  const value = result as { rows?: unknown };
  if (!Array.isArray(value.rows)) return result;
  const migration = ensureScriptShotIdentities(
    value.rows as FreezoneStoryScriptRow[],
  );
  if (!migration.changed) return result;
  return { ...(result as Record<string, unknown>), rows: migration.rows };
}

/** Normalize the optional script result carried by a script node's data object. */
export function normalizeScriptNodeIdentities(data: Record<string, unknown>): void {
  if ('scriptResult' in data) {
    data.scriptResult = normalizeScriptResultIdentities(data.scriptResult);
  }
}

/** Create a row for future insert operations; callers still own ordering/UI. */
export function createScriptShotRow(
  overrides: Partial<FreezoneStoryScriptRow> = {},
  shotOrder = 1,
): FreezoneStoryScriptRow {
  const display = cellText(overrides.display_shot_no)
    || cellText(overrides.shot_no)
    || String(shotOrder);
  return {
    shot_no: display,
    duration: '',
    visual_description: '',
    ...overrides,
    shot_id: createScriptShotId(),
    shot_order: shotOrder,
    display_shot_no: display,
  };
}

/** Copy a shot as a new production object; identity is never shared. */
export function copyScriptShotRow(
  row: FreezoneStoryScriptRow,
  shotOrder: number,
): FreezoneStoryScriptRow {
  return {
    ...row,
    shot_id: createScriptShotId(),
    shot_order: shotOrder,
    display_shot_no: String(shotOrder),
  };
}

/** Reordering changes sequence only; existing IDs remain untouched. */
export function resequenceScriptShotRows(
  rows: readonly FreezoneStoryScriptRow[],
): FreezoneStoryScriptRow[] {
  return rows.map((row, index) => (
    row.shot_order === index + 1
      ? row
      : { ...row, shot_order: index + 1 }
  ));
}

/**
 * Resequence after a structural edit (insert / copy / delete).
 *
 * Sequence always follows the new row order. A row whose visible number still
 * matches the sequence it had before the edit is treated as auto-numbered and
 * follows the sequence too; numbers the user or the model authored differently
 * are left alone. Identity is never touched.
 */
export function resequenceScriptShotRowsAfterEdit(
  previousRows: readonly FreezoneStoryScriptRow[],
  nextRows: readonly FreezoneStoryScriptRow[],
): FreezoneStoryScriptRow[] {
  const previousOrderById = new Map<string, number>();
  previousRows.forEach((row, index) => {
    const id = cellText(row.shot_id);
    if (id.length > 0) previousOrderById.set(id, index + 1);
  });

  let changed = false;
  const resequenced = nextRows.map((row, index) => {
    const order = index + 1;
    const id = cellText(row.shot_id);
    const previousOrder = id.length > 0 ? previousOrderById.get(id) : undefined;
    const label = cellText(row.display_shot_no) || cellText(row.shot_no);
    const autoNumbered = previousOrder != null && label === String(previousOrder);

    if (row.shot_order === order && (!autoNumbered || label === String(order))) {
      return row;
    }
    changed = true;
    if (!autoNumbered) return { ...row, shot_order: order };
    const nextLabel = String(order);
    return { ...row, shot_order: order, shot_no: nextLabel, display_shot_no: nextLabel };
  });

  return changed ? resequenced : [...nextRows];
}

/** Insert a blank shot after `rowIndex`; existing identities are preserved. */
export function insertScriptShotRowAfter(
  rows: readonly FreezoneStoryScriptRow[],
  rowIndex: number,
): FreezoneStoryScriptRow[] {
  const current = ensureScriptShotIdentities(rows).rows;
  if (rowIndex < 0 || rowIndex >= current.length) return current;
  const fresh = createScriptShotRow({}, rowIndex + 2);
  return resequenceScriptShotRowsAfterEdit(current, [
    ...current.slice(0, rowIndex + 1),
    fresh,
    ...current.slice(rowIndex + 1),
  ]);
}

/** Copy a shot as a new production object placed right after the source. */
export function duplicateScriptShotRowAt(
  rows: readonly FreezoneStoryScriptRow[],
  rowIndex: number,
): FreezoneStoryScriptRow[] {
  const current = ensureScriptShotIdentities(rows).rows;
  const source = current[rowIndex];
  if (!source) return current;
  const copy = copyScriptShotRow(source, rowIndex + 2);
  return resequenceScriptShotRowsAfterEdit(current, [
    ...current.slice(0, rowIndex + 1),
    copy,
    ...current.slice(rowIndex + 1),
  ]);
}

/** Delete one shot. Refuses to empty the table; other identities are untouched. */
export function deleteScriptShotRowAt(
  rows: readonly FreezoneStoryScriptRow[],
  rowIndex: number,
): FreezoneStoryScriptRow[] {
  const current = ensureScriptShotIdentities(rows).rows;
  if (current.length <= 1) return current;
  const next = current.filter((_, index) => index !== rowIndex);
  if (next.length === current.length) return current;
  return resequenceScriptShotRowsAfterEdit(current, next);
}

/** Move one shot up or down; sequence changes, identities do not. */
export function moveScriptShotRow(
  rows: readonly FreezoneStoryScriptRow[],
  rowIndex: number,
  delta: -1 | 1,
): FreezoneStoryScriptRow[] {
  const current = ensureScriptShotIdentities(rows).rows;
  const target = rowIndex + delta;
  if (target < 0 || target >= current.length) return current;
  const next = [...current];
  [next[rowIndex], next[target]] = [next[target], next[rowIndex]];
  return resequenceScriptShotRows(next);
}
