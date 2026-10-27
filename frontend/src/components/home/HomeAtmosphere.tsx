// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef } from "react";

/**
 * 首页氛围层：一层始终存在的点阵，加一层只在指针附近提亮的点阵和光晕。
 *
 * 坐标写进 CSS 变量，由 radial-gradient 的 mask 把第二层点阵裁成跟随指针的
 * 圆；指针移动只改变量、不触发布局，所以整页滚动和拖拽都不会被拖慢。鼠标
 * 离开窗口或窗口失焦时两层一起淡出，不会停在最后一个位置上。
 */
export function HomeAtmosphere() {
  const hostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let frame: number | null = null;
    let clientX = 0;
    let clientY = 0;

    const paint = () => {
      frame = null;
      // 画布区域会滚动，光斑必须换算成相对宿主元素的位置，否则滚动后
      // 光斑会留在原地，和指针脱开。
      const rect = host.getBoundingClientRect();
      host.style.setProperty("--home-pointer-x", `${clientX - rect.left}px`);
      host.style.setProperty("--home-pointer-y", `${clientY - rect.top}px`);
      host.style.setProperty("--home-pointer-active", "1");
    };

    const schedulePaint = () => {
      if (frame === null) frame = window.requestAnimationFrame(paint);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (
        event.pointerType &&
        event.pointerType !== "mouse" &&
        event.pointerType !== "pen"
      ) {
        return;
      }
      clientX = event.clientX;
      clientY = event.clientY;
      schedulePaint();
    };

    const hidePointer = () => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
        frame = null;
      }
      host.style.setProperty("--home-pointer-active", "0");
    };

    window.addEventListener("pointermove", handlePointerMove, { passive: true });
    window.addEventListener("scroll", schedulePaint, { passive: true, capture: true });
    document.documentElement.addEventListener("pointerleave", hidePointer, {
      passive: true,
    });
    window.addEventListener("blur", hidePointer);

    // 触屏没有悬停指针，别让初始坐标处的光斑常亮。（测试环境没有 matchMedia）
    if (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(hover: none)").matches
    ) {
      hidePointer();
    }

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("scroll", schedulePaint, { capture: true });
      document.documentElement.removeEventListener("pointerleave", hidePointer);
      window.removeEventListener("blur", hidePointer);
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div ref={hostRef} className="village-home-atmosphere" aria-hidden="true">
      <div className="village-home-atmosphere__dots" />
      <div className="village-home-atmosphere__radiance" />
    </div>
  );
}
