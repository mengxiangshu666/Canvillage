// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  VideoFrameCaptureMenu,
  VIDEO_FRAME_CAPTURE_MODES,
} from "./VideoFrameCaptureMenu";

const translations: Record<string, string> = {
  "node.videoNode.frame.captureFirst": "截取首帧",
  "node.videoNode.frame.captureLast": "截取尾帧",
  "node.videoNode.frame.captureCurrentItem": "截取当前帧",
};

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => translations[key] ?? key,
  }),
}));

describe("VideoFrameCaptureMenu", () => {
  it("按首帧 / 尾帧 / 当前帧的顺序列出三个截帧项", () => {
    render(<VideoFrameCaptureMenu onCapture={vi.fn()} />);

    const labels = screen
      .getAllByRole("button")
      .map((button) => button.textContent);
    expect(labels).toEqual(["截取首帧", "截取尾帧", "截取当前帧"]);
    expect(VIDEO_FRAME_CAPTURE_MODES).toEqual(["first", "last", "current"]);
  });

  it("点击截取当前帧回传 current 模式", () => {
    const onCapture = vi.fn();
    render(<VideoFrameCaptureMenu onCapture={onCapture} />);

    fireEvent.click(screen.getByRole("button", { name: "截取当前帧" }));

    expect(onCapture).toHaveBeenCalledTimes(1);
    expect(onCapture).toHaveBeenCalledWith("current");
  });

  it("首帧与尾帧各自回传对应模式", () => {
    const onCapture = vi.fn();
    render(<VideoFrameCaptureMenu onCapture={onCapture} />);

    fireEvent.click(screen.getByRole("button", { name: "截取首帧" }));
    fireEvent.click(screen.getByRole("button", { name: "截取尾帧" }));

    expect(onCapture.mock.calls).toEqual([["first"], ["last"]]);
  });

  it("截帧进行中时菜单项不可点", () => {
    const onCapture = vi.fn();
    render(<VideoFrameCaptureMenu onCapture={onCapture} disabled />);

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(3);
    for (const button of buttons) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(onCapture).not.toHaveBeenCalled();
  });
});
