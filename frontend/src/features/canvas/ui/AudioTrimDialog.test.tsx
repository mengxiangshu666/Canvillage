// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AudioTrimDialog } from "./AudioTrimDialog";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// jsdom 既不解码音频也不跑 rAF：这里把「当前播放时间」和「帧回调」都换成可手动推进的桩，
// 才能断言播放头是否真的跟着 currentTime 走。
vi.mock("@/features/canvas/compose/audioPeaks", () => ({
  PEAK_BUCKETS_PER_SEC: 120,
  getCachedAudioPeaks: () => null,
  loadAudioPeaks: () => Promise.resolve(null),
}));

const DURATION_MS = 15_000;
let mediaTime = 0;
let frames: FrameRequestCallback[] = [];

function flushFrame() {
  const pending = frames;
  frames = [];
  act(() => {
    pending.forEach((callback) => callback(0));
  });
}

function renderDialog() {
  return render(
    <AudioTrimDialog
      open
      src="/project-assets/demo/audio/voice.mp3"
      durationMs={DURATION_MS}
      onOpenChange={() => {}}
      onConfirm={() => {}}
    />,
  );
}

function previewButton() {
  return screen.getByRole("button", { name: "node.audio.trim.preview" });
}

beforeEach(() => {
  mediaTime = 0;
  frames = [];
  Object.defineProperty(HTMLMediaElement.prototype, "currentTime", {
    configurable: true,
    get() {
      return mediaTime;
    },
    set(value: number) {
      mediaTime = value;
    },
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.push(callback);
    return frames.length;
  });
  vi.stubGlobal("cancelAnimationFrame", (handle: number) => {
    frames[handle - 1] = () => {};
  });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AudioTrimDialog 试听播放头", () => {
  it("moves the playhead with currentTime while previewing", async () => {
    renderDialog();
    expect(screen.queryByTestId("audio-trim-playhead")).toBeNull();

    await act(async () => {
      fireEvent.click(previewButton());
    });

    mediaTime = 3;
    flushFrame();
    expect(screen.getByTestId("audio-trim-playhead")).toHaveStyle({ left: "20%" });

    mediaTime = 7.5;
    flushFrame();
    expect(screen.getByTestId("audio-trim-playhead")).toHaveStyle({ left: "50%" });
    expect(screen.getByText("00:07.5")).toBeInTheDocument();
  });

  it("hides the playhead once preview runs past the selection end", async () => {
    renderDialog();
    await act(async () => {
      fireEvent.click(previewButton());
    });

    mediaTime = 16;
    flushFrame();

    expect(screen.queryByTestId("audio-trim-playhead")).toBeNull();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  });

  it("hides the playhead when the clip ends before the selection does", async () => {
    renderDialog();
    await act(async () => {
      fireEvent.click(previewButton());
    });

    mediaTime = 12;
    flushFrame();
    expect(screen.getByTestId("audio-trim-playhead")).toHaveStyle({ left: "80%" });

    // 文件真实时长短于记录时长：还没到选区终点就播完了。
    fireEvent.ended(document.querySelector("audio") as HTMLAudioElement);

    expect(screen.queryByTestId("audio-trim-playhead")).toBeNull();
  });

  it("positions the playhead by clicking the waveform", async () => {
    renderDialog();
    // 峰值解码的 Promise 在挂载后立刻兑现，先让它落地再操作，避免 act 警告。
    await act(async () => {});
    const track = screen.getByTestId("audio-trim-track");
    vi.spyOn(track, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      width: 1000,
      height: 96,
      right: 1000,
      bottom: 96,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.pointerDown(track, { clientX: 250 });

    expect(screen.getByTestId("audio-trim-playhead")).toHaveStyle({ left: "25%" });
    expect(mediaTime).toBeCloseTo(3.75);
  });

  it("resumes preview from the playhead instead of the selection start", async () => {
    renderDialog();
    const track = screen.getByTestId("audio-trim-track");
    vi.spyOn(track, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      width: 1000,
      height: 96,
      right: 1000,
      bottom: 96,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.pointerDown(track, { clientX: 600 });
    await act(async () => {
      fireEvent.click(previewButton());
    });

    expect(mediaTime).toBeCloseTo(9);
    flushFrame();
    expect(screen.getByTestId("audio-trim-playhead")).toHaveStyle({ left: "60%" });
  });
});
