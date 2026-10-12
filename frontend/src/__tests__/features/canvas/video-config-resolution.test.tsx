// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VideoConfigChip } from "@/features/canvas/nodes/VideoNodeControls";

describe("video config resolution options", () => {
  it("shows the selected model's declared resolutions", () => {
    render(
      <VideoConfigChip
        aspectRatio="16:9"
        aspectRatioOptions={["16:9", "9:16"]}
        quality="768P"
        advertisedQualityOptions={["480P", "720P", "768P"]}
        rejectedQualityOptions={new Set()}
        durationSec={5}
        durationBounds={{ min: 4, max: 30 }}
        durationParameterEnabled
        sceneOptimizeOptions={[]}
        generateAudio={false}
        nativeAudio="optional"
        onGenerateAudioChange={vi.fn()}
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /16:9/ }));

    expect(screen.getByRole("button", { name: "480P" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "720P" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "768P" })).toBeInTheDocument();
  });
});
