// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * Build a deployment fingerprint that changes on every invocation. Git's
 * `--dirty` suffix only says that a tree is modified; it does not identify
 * which modification was built, so it must never be the uniqueness source.
 */
export function composeBuildId(
  described: string | null,
  builtAtMs: number = Date.now(),
): string {
  const builtAt = new Date(builtAtMs);
  const timestamp = builtAt.toISOString().replace(/[-:.]/g, "");
  const source = described?.trim() || "nogit";
  return `${timestamp}-${source}-${builtAtMs.toString(36)}`;
}
