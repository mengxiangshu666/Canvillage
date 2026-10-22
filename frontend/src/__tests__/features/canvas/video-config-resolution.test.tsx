// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VideoConfigChip } from "@/features/canvas/nodes/VideoNodeControls";
import { balancedOptionGridStyle } from "@/features/canvas/ui/nodeControlStyles";

describe("video config resolution options", () => {
  it("shows the selected model's declared resolutions", () => {
    render(
      <VideoConfigChip
        aspectRatio="16:9"
        aspectRatioOptions={["16:9", "9:16"]}
        count={1}
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

  it("balances six aspect options into two complete rows", () => {
    render(
      <VideoConfigChip
        aspectRatio="16:9"
        aspectRatioOptions={["16:9", "9:16", "21:9", "4:3", "1:1", "3:4"]}
        count={1}
        quality="720P"
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

    const lastAspectButton = screen.getByRole("button", { name: "3:4" });
    expect(lastAspectButton.parentElement?.style.gridTemplateColumns).toBe(
      "repeat(4, minmax(0, 1fr))",
    );
  });

  it.each([
    [2, "repeat(2, minmax(0, 1fr))"],
    [5, "repeat(5, minmax(0, 1fr))"],
    [6, "repeat(3, minmax(0, 1fr))"],
    [7, "repeat(4, minmax(0, 1fr))"],
    [9, "repeat(3, minmax(0, 1fr))"],
    [10, "repeat(5, minmax(0, 1fr))"],
  ])("uses balanced columns for %i options", (count, expected) => {
    expect(balancedOptionGridStyle(count).gridTemplateColumns).toBe(expected);
  });
});
