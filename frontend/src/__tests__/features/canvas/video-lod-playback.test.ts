// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function read(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf8");
}

describe("VideoNode LOD playback contract", () => {
  it("uses the synchronous active-media registry before swapping to a still", () => {
    const source = read("src/features/canvas/nodes/VideoNode.tsx");

    expect(source).toContain("isNodeMediaActive,");
    expect(source).toContain(
      "videoSource && lowDetailZoom && !isNodeMediaActive(id)",
    );
    expect(source).not.toContain(
      "videoSource && lowDetailZoom && !isVideoPlaying",
    );
  });

  it("keeps the player source stable across LOD changes", () => {
    const source = read("src/features/canvas/nodes/VideoNode.tsx");
    const previewHook = read("src/features/canvas/hooks/useVideoNodePreview.ts");

    expect(source).toContain("src={videoPosterSource ?? undefined}");
    expect(source).toContain("preload=\"metadata\"");
    expect(previewHook).toContain("const videoPosterSource = useMemo");
    expect(previewHook).toContain("[videoSource]");
  });

  it("preserves native playback and playhead ownership in the mounted video", () => {
    const source = read("src/features/canvas/nodes/VideoNode.tsx");
    const playbackOverlays = read(
      "src/features/canvas/nodes/VideoPlaybackOverlays.tsx",
    );

    expect(source).toContain("const videoRef = useRef<HTMLVideoElement | null>(null)");
    expect(source).toContain("setNodeMediaActive(id, active, videoEl.currentTime)");
    expect(source).toContain("setNodeMediaActive(id, false)");
    expect(source).toContain("getNodeMediaCurrentTime(id)");
    expect(source).toContain("videoEl.play().then(sync)");
    expect(source).toContain('videoEl.addEventListener("loadedmetadata", resume');
    expect(playbackOverlays).toContain("videoEl.currentTime = next");
  });
});
