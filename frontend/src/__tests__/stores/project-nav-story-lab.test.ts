// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import {
  migrateLegacyProjectNavState,
  useProjectNavStore,
} from "@/stores/project-nav-store";

const LEGACY_WORKFLOW_KEY = "lastXiajiSectionByProject"; // identity-allow: pre-rename persisted key under test

describe("project navigation Story Lab memory", () => {
  beforeEach(() => {
    localStorage.clear();
    useProjectNavStore.getState().reset();
  });

  it("restores Story Lab as the latest workflow section", () => {
    useProjectNavStore.getState().rememberSection("demo", "storyLab");

    expect(useProjectNavStore.getState().lastSectionByProject.demo).toBe("storyLab");
    expect(useProjectNavStore.getState().lastWorkflowSectionByProject.demo).toBe("storyLab");
  });

  it("remembers the making workbench as a workflow section", () => {
    useProjectNavStore.getState().rememberSection("demo", "making");

    expect(useProjectNavStore.getState().lastSectionByProject.demo).toBe("making");
    expect(useProjectNavStore.getState().lastWorkflowSectionByProject.demo).toBe("making");
  });

  it("migrates the retired assistant page to the production command center", () => {
    expect(
      migrateLegacyProjectNavState({
        lastSectionByProject: { demo: "assistant" },
        lastWorkflowSectionByProject: { demo: "assistant" },
      }),
    ).toEqual({
      lastSectionByProject: { demo: "production" },
      lastWorkflowSectionByProject: { demo: "production" },
    });
  });

  it("reads the pre-rename workflow-section key from a persisted v2 store", () => {
    const migrated = migrateLegacyProjectNavState({
      lastSectionByProject: { demo: "making" },
      [LEGACY_WORKFLOW_KEY]: { demo: "making", other: "assistant", bogus: "elsewhere" },
    });

    // 旧键被读回：assistant 归到 production，不在白名单的区块丢弃。
    expect(migrated.lastWorkflowSectionByProject).toEqual({
      demo: "making",
      other: "production",
    });
  });

  it("prefers the current key when both are present", () => {
    const migrated = migrateLegacyProjectNavState({
      lastSectionByProject: { demo: "styles" },
      lastWorkflowSectionByProject: { demo: "styles" },
      [LEGACY_WORKFLOW_KEY]: { demo: "making" },
    });

    expect(migrated.lastWorkflowSectionByProject).toEqual({ demo: "styles" });
  });
});
