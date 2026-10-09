// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  isProjectModeEntryActive,
  PROJECT_SECTION_ROUTES,
  projectModeFromPath,
  projectSectionFromPath,
  resolveProjectWorkflowEntryRoute,
} from "@/components/layout/project-navigation-routes";

describe("project navigation routes", () => {
  it("uses freezone as the project dashboard entry", () => {
    expect(PROJECT_SECTION_ROUTES.freezone).toBe("/projects/$project/freezone");
  });

  it("classifies the canvas separately from every workflow section", () => {
    expect(projectModeFromPath("/projects/demo/freezone")).toBe("canvas");
    expect(projectModeFromPath("/projects/demo/production")).toBe("workflow");
    expect(projectModeFromPath("/projects/demo/story-lab")).toBe("workflow");
    expect(projectModeFromPath("/projects/demo/ingest")).toBe("workflow");
    expect(projectModeFromPath("/projects/demo/making")).toBe("workflow");
    expect(projectModeFromPath("/projects/demo/tasks")).toBe("workflow");
  });

  it("exposes the production command center as a remembered project section", () => {
    expect(PROJECT_SECTION_ROUTES.production).toBe("/projects/$project/production");
    expect(projectSectionFromPath("/projects/demo/production")).toBe("production");
  });

  it("exposes Story Lab as a first-class project section", () => {
    expect(PROJECT_SECTION_ROUTES.storyLab).toBe("/projects/$project/story-lab");
    expect(projectSectionFromPath("/projects/demo/story-lab")).toBe("storyLab");
  });

  it("exposes the making workbench as the workflow delivery section", () => {
    expect(PROJECT_SECTION_ROUTES.making).toBe("/projects/$project/making");
    expect(projectSectionFromPath("/projects/demo/making")).toBe("making");
  });

  it("routes the Village Canvas workflow entry to the production command center", () => {
    expect(resolveProjectWorkflowEntryRoute(true)).toBe(
      PROJECT_SECTION_ROUTES.production,
    );
    expect(resolveProjectWorkflowEntryRoute(true, "storyLab")).toBe(
      PROJECT_SECTION_ROUTES.production,
    );
    expect(
      isProjectModeEntryActive("/projects/demo/production", "workflow", true),
    ).toBe(true);
    expect(
      isProjectModeEntryActive("/projects/demo/story-lab", "workflow", true),
    ).toBe(true);
    expect(
      isProjectModeEntryActive("/projects/demo/characters", "workflow", true),
    ).toBe(true);
    expect(isProjectModeEntryActive("/projects/demo/assistant", "workflow", true)).toBe(true);
  });

  it("keeps remembered workflow sections for the compatibility shell", () => {
    expect(resolveProjectWorkflowEntryRoute(false, "storyLab")).toBe(
      PROJECT_SECTION_ROUTES.storyLab,
    );
    expect(
      isProjectModeEntryActive("/projects/demo/production", "workflow", false),
    ).toBe(true);
  });

  it("preserves the tasks section when switching projects", () => {
    expect(projectSectionFromPath("/projects/demo/tasks")).toBe("tasks");
  });

  it("does not silently classify unknown project sections as freezone", () => {
    expect(projectSectionFromPath("/projects/demo/unknown")).toBeNull();
  });
});
