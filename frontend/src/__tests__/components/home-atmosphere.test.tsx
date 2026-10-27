// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { HomeAtmosphere } from "@/components/home/HomeAtmosphere";

function pointerMove(clientX: number, clientY: number) {
  window.dispatchEvent(
    new MouseEvent("pointermove", { clientX, clientY, bubbles: true }),
  );
}

async function flushFrame() {
  await act(async () => {
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
}

describe("HomeAtmosphere", () => {
  afterEach(cleanup);

  it("lights the field around the pointer and clears it when the pointer leaves", async () => {
    const { container } = render(<HomeAtmosphere />);
    const host = container.querySelector<HTMLElement>(".village-home-atmosphere");
    expect(host).not.toBeNull();
    if (!host) return;

    expect(host.style.getPropertyValue("--home-pointer-active")).not.toBe("1");

    pointerMove(120, 80);
    await flushFrame();

    expect(host.style.getPropertyValue("--home-pointer-active")).toBe("1");
    expect(host.style.getPropertyValue("--home-pointer-x")).toBe("120px");
    expect(host.style.getPropertyValue("--home-pointer-y")).toBe("80px");

    act(() => {
      document.documentElement.dispatchEvent(new Event("pointerleave"));
    });

    expect(host.style.getPropertyValue("--home-pointer-active")).toBe("0");
  });

  it("keeps the spotlight on the same screen point when the page scrolls", async () => {
    const { container } = render(<HomeAtmosphere />);
    const host = container.querySelector<HTMLElement>(".village-home-atmosphere");
    expect(host).not.toBeNull();
    if (!host) return;

    pointerMove(200, 140);
    await flushFrame();
    expect(host.style.getPropertyValue("--home-pointer-y")).toBe("140px");

    // 页面滚动后宿主元素上移 100px，同一个屏幕坐标应换算成新的局部坐标，
    // 否则光斑会留在原地、和指针脱开。
    host.getBoundingClientRect = () =>
      ({ left: 0, top: -100, width: 0, height: 0 } as DOMRect);
    act(() => {
      window.dispatchEvent(new Event("scroll"));
    });
    await flushFrame();

    expect(host.style.getPropertyValue("--home-pointer-y")).toBe("240px");
  });
});
