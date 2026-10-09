// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  // Keep the test discovery root stable when Vitest is launched from the
  // repository root (for example by a shared CI wrapper). Without this,
  // sibling third-party snapshots, caches, and private artifacts are treated
  // as additional test trees and the quality signal becomes misleading.
  root: __dirname,
  // Mirror the compile-time `__APP_VERSION__` that `vite.config.ts` injects,
  // so code importing `@/lib/app-version` works under vitest too. In tests we
  // don't care about the real value — a stable placeholder is plenty.
  define: {
    __APP_VERSION__: JSON.stringify("test"),
    __BUILD_ID__: JSON.stringify("test-build"),
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
    globals: true,
    // The full canvas suite imports multiple heavyweight browser/media stacks.
    // Default fork-per-core fan-out exhausts Node's heap on normal developer
    // machines, turning unrelated tests into timeouts. A bounded thread pool
    // keeps parallel feedback while making local and CI runs deterministic.
    pool: "threads",
    maxWorkers: 2,
    // Don't re-collect tests from nested git worktrees. Claude Code's agent
    // lifecycle leaves locked .claude/worktrees/* directories that mirror the
    // repo; without this exclude, vitest runs every test file ~N times and
    // blows up the total count.
    exclude: [
      "**/node_modules/**",
      "**/dist/**",
      "**/.worktrees/**",
      "**/.claude/**",
      "**/third_party/**",
      "**/.pnpm-store/**",
      "**/项目资产/**",
      "**/workspace/**",
      "**/_task_backups/**",
      "**/_deploy_backups/**",
      "**/_integration_backups/**",
    ],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
