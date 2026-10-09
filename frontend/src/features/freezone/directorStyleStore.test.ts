import { afterEach, describe, expect, it } from "vitest";

import { DEFAULT_DIRECTOR_CONTROLS } from "./directorPromptEngine";
import { currentDirectorControls, useDirectorStyleStore } from "./directorStyleStore";

describe("directorStyleStore canvas scope", () => {
  afterEach(() => {
    useDirectorStyleStore.setState({
      ...DEFAULT_DIRECTOR_CONTROLS,
      activeScope: "__default__",
      scopedControls: {},
    });
    localStorage.clear();
  });

  it("does not leak Director Style controls across projects or canvases", () => {
    const store = useDirectorStyleStore.getState();
    store.activateScope("project-a", "canvas-a");
    useDirectorStyleStore.getState().update({ styleId: "film-noir", identityLock: false });

    useDirectorStyleStore.getState().activateScope("project-a", "canvas-b");
    expect(currentDirectorControls()).toEqual(DEFAULT_DIRECTOR_CONTROLS);
    useDirectorStyleStore.getState().update({ styleId: "ink-wash" });

    useDirectorStyleStore.getState().activateScope("project-a", "canvas-a");
    expect(currentDirectorControls()).toMatchObject({ styleId: "film-noir", identityLock: false });
    expect(currentDirectorControls("project-a", "canvas-b").styleId).toBe("ink-wash");
    expect(currentDirectorControls("project-b", "canvas-a")).toEqual(DEFAULT_DIRECTOR_CONTROLS);
  });
});
