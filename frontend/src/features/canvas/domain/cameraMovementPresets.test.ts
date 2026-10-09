import { describe, expect, it } from "vitest";

import {
  CAMERA_MOVEMENT_PRESETS,
  findCameraMovementPreset,
} from "./cameraMovementPresets";

describe("camera movement preset identity", () => {
  it("resolves legacy fallback ids against the backend catalog", () => {
    const backendTemplates = CAMERA_MOVEMENT_PRESETS.map((preset) => ({
      ...preset,
      id:
        preset.id === "dolly-in"
          ? "dolly_in"
          : preset.id === "orbit"
            ? "orbit_around"
            : preset.id,
    }));

    expect(findCameraMovementPreset(backendTemplates, "dolly-in")?.id).toBe(
      "dolly_in",
    );
    expect(findCameraMovementPreset(backendTemplates, "orbit_around")?.label).toBe(
      "环绕拍摄",
    );
  });
});
