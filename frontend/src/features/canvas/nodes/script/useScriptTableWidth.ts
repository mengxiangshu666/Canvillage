// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from 'react';

/**
 * 分镜表当前能用的宽度（像素）。
 *
 * 表格的列集按这个宽度选（见 `scriptColumns.ts`）——节点内用 React Flow 给的节点宽，
 * 全屏弹层用视口宽。全屏是 `fixed inset-0 p-6` + 内层 `p-2` + 表格自身 1px 边框，
 * 所以可用宽度是视口宽减 66。
 *
 * 只读 `window.innerWidth`，不观察 DOM：这里要的是「窗口有多大」，不是「某个元素
 * 现在多宽」——节点宽由 React Flow 的 measured 尺寸给出，两者都不是需要 ResizeObserver
 * 去追的东西。窗口缩放才需要重新算。
 */

/** 节点宽的扣减量：外层 `p-2`（16）+ 表格自身边框（2）。 */
export const SCRIPT_TABLE_NODE_CHROME_PX = 18;

/** 全屏弹层的扣减量：弹层 `p-6`（48）+ 内层 `p-2`（16）+ 表格自身边框（2）。 */
export const SCRIPT_TABLE_FULLSCREEN_CHROME_PX = 66;

export function useViewportWidth(): number {
  const [width, setWidth] = useState(() =>
    typeof window === 'undefined' ? 1440 : window.innerWidth,
  );

  useEffect(() => {
    const handleResize = () => setWidth(window.innerWidth);
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return width;
}
