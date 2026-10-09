// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  clearHomeCanvasHandoff,
  peekHomeCanvasHandoff,
  setHomeCanvasHandoff,
  takeHomeCanvasHandoff,
} from "@/lib/home-handoff";

describe("home canvas handoff", () => {
  it("carries reference content and selected skills into the matching canvas", () => {
    const attachment = { id: "ref1", fileName: "reference.txt", content: "角色设定", type: "file" };
    setHomeCanvasHandoff({ projectId: "new_project", draft: "参考这些素材", starterWorkflowId: null, attachments: [attachment], skillIds: ["director"], autoSend: true });
    const handoff = takeHomeCanvasHandoff("new_project");
    expect(handoff?.attachments).toEqual([attachment]);
    expect(handoff?.skillIds).toEqual(["director"]);
    expect(handoff?.autoSend).toBe(true);
    expect(peekHomeCanvasHandoff("new_project")).toBeNull();
  });
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("round-trips a draft for the matching project", () => {
    setHomeCanvasHandoff({
      projectId: "project_1",
      draft: "做一个雨夜追逐短片",
      starterWorkflowId: "original-vertical-short-film",
    });

    expect(peekHomeCanvasHandoff("project_1")).toEqual({
      projectId: "project_1",
      draft: "做一个雨夜追逐短片",
      starterWorkflowId: "original-vertical-short-film",
    });
  });

  it("ignores a handoff queued for another project", () => {
    setHomeCanvasHandoff({
      projectId: "project_1",
      draft: "只属于 project_1",
      starterWorkflowId: null,
    });

    expect(peekHomeCanvasHandoff("project_2")).toBeNull();
    expect(peekHomeCanvasHandoff("project_1")).not.toBeNull();
  });

  it("consumes the handoff once", () => {
    setHomeCanvasHandoff({
      projectId: "project_1",
      draft: "一次性交接",
      starterWorkflowId: null,
    });

    expect(takeHomeCanvasHandoff("project_1")?.draft).toBe("一次性交接");
    expect(peekHomeCanvasHandoff("project_1")).toBeNull();
  });

  it("drops empty handoffs and clears on demand", () => {
    setHomeCanvasHandoff({
      projectId: "project_1",
      draft: "   ",
      starterWorkflowId: null,
    });
    expect(peekHomeCanvasHandoff("project_1")).toBeNull();

    setHomeCanvasHandoff({
      projectId: "project_1",
      draft: "保留",
      starterWorkflowId: null,
    });
    clearHomeCanvasHandoff();
    expect(peekHomeCanvasHandoff("project_1")).toBeNull();
  });
});
