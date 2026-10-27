// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef } from 'react';
import { useStoreApi } from '@xyflow/react';

import {
  forEachPointerAuraDot,
  mixRgb,
  resolveGridRgb,
  resolvePointerAuraGrid,
  rgba,
  type Rgb,
} from './canvasPointerAuraGeometry';
import { resolveCanvasGridRenderMetrics } from './canvasViewSettingsStore';

/** 指针周围「点凸起来」的半径，屏幕像素。 */
const AURA_REACH = 190;
/** 大范围柔光半径：整片背景跟着指针亮起来。 */
const AURA_RADIANCE = 300;
/** 每帧向目标淡入淡出的速度。 */
const AURA_EASE = 0.16;
const TAU = Math.PI * 2;
const WHITE: Rgb = [255, 255, 255];

/** 小球的贴图尺寸：32 个单位画进 128px 画布，缩放时留足细节。 */
const SPRITE_UNIT = 32;
const SPRITE_BODY_RADIUS = 7;
const SPRITE_PIXELS_PER_UNIT = 4;

interface CanvasPointerAuraProps {
  /** 逻辑网格间距（世界单位），与 AdaptiveCanvasGrid 保持一致。 */
  gap: number;
  /** 点直径（世界单位），与 AdaptiveCanvasGrid 保持一致。 */
  dotSize: number;
  /** 网格颜色，用来推导凸起点的亮部和柔光。 */
  color: string;
}

/**
 * 把「凸起的小点」预渲染成一张贴图：左上受光、右下压一道暗边。每帧只改
 * 透明度和绘制尺寸，不再逐点创建渐变，几百个点也能稳在 60fps。
 *
 * 不用四周发散的投影：深色背景上一个对称的阴影会变成一圈「黑括号」，
 * 只留右下侧的暗边，读起来才是球面自己的背光面。
 */
function createDotSprite(color: Rgb): HTMLCanvasElement {
  const sprite = document.createElement('canvas');
  sprite.width = SPRITE_UNIT * SPRITE_PIXELS_PER_UNIT;
  sprite.height = SPRITE_UNIT * SPRITE_PIXELS_PER_UNIT;
  const ink = sprite.getContext('2d');
  if (!ink) return sprite;

  ink.scale(SPRITE_PIXELS_PER_UNIT, SPRITE_PIXELS_PER_UNIT);
  const center = SPRITE_UNIT / 2;
  const bodyRadius = SPRITE_BODY_RADIUS;

  const shade = ink.createRadialGradient(
    center + bodyRadius * 0.34,
    center + bodyRadius * 0.42,
    bodyRadius * 0.1,
    center + bodyRadius * 0.16,
    center + bodyRadius * 0.2,
    bodyRadius * 1.16,
  );
  shade.addColorStop(0, 'rgba(0, 0, 0, 0.5)');
  shade.addColorStop(0.72, 'rgba(0, 0, 0, 0.32)');
  shade.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ink.fillStyle = shade;
  ink.beginPath();
  ink.arc(
    center + bodyRadius * 0.3,
    center + bodyRadius * 0.38,
    bodyRadius * 1.1,
    0,
    TAU,
  );
  ink.fill();

  const lightX = center - bodyRadius * 0.34;
  const lightY = center - bodyRadius * 0.4;
  const body = ink.createRadialGradient(
    lightX,
    lightY,
    bodyRadius * 0.06,
    center,
    center,
    bodyRadius,
  );
  body.addColorStop(0, rgba(mixRgb(color, WHITE, 0.82), 1));
  body.addColorStop(0.36, rgba(mixRgb(color, WHITE, 0.58), 1));
  body.addColorStop(0.72, rgba(mixRgb(color, WHITE, 0.4), 1));
  body.addColorStop(1, rgba(mixRgb(color, WHITE, 0.22), 1));
  ink.fillStyle = body;
  ink.beginPath();
  ink.arc(center, center, bodyRadius, 0, TAU);
  ink.fill();

  const specularRadius = bodyRadius * 0.42;
  const specular = ink.createRadialGradient(
    lightX,
    lightY,
    0,
    lightX,
    lightY,
    specularRadius,
  );
  specular.addColorStop(0, 'rgba(255, 255, 255, 0.65)');
  specular.addColorStop(0.5, 'rgba(255, 255, 255, 0.2)');
  specular.addColorStop(1, 'rgba(255, 255, 255, 0)');
  ink.fillStyle = specular;
  ink.beginPath();
  ink.arc(lightX, lightY, specularRadius, 0, TAU);
  ink.fill();

  return sprite;
}

/** 柔光贴图：整块径向渐变只画一次，帧循环里只改透明度。 */
const RADIANCE_SPRITE_PX = 256;

function createRadianceSprite(color: Rgb): HTMLCanvasElement {
  const sprite = document.createElement('canvas');
  sprite.width = RADIANCE_SPRITE_PX;
  sprite.height = RADIANCE_SPRITE_PX;
  const ink = sprite.getContext('2d');
  if (!ink) return sprite;
  const center = RADIANCE_SPRITE_PX / 2;
  const radiance = ink.createRadialGradient(
    center,
    center,
    0,
    center,
    center,
    center,
  );
  radiance.addColorStop(0, rgba(color, 1));
  radiance.addColorStop(0.45, rgba(color, 0.38));
  radiance.addColorStop(1, rgba(color, 0));
  ink.fillStyle = radiance;
  ink.fillRect(0, 0, RADIANCE_SPRITE_PX, RADIANCE_SPRITE_PX);
  return sprite;
}

/**
 * 画布指针光效：一层盖在点阵之上、节点之下的 canvas。
 *
 * 底层点阵是 XYFlow 用 SVG pattern 画的，单个点没有 DOM；这里按同一套
 * pattern 公式（见 canvasPointerAuraGeometry.ts）算出每个点的屏幕坐标，
 * 只在指针附近把点重画一遍：放大、左上高光、右下投影，看起来像被顶起来，
 * 再叠一层柔光。整层不吃指针事件，也不参与节点布局。窗口失焦、指针离开
 * 画布时整层淡出。
 */
export function CanvasPointerAura({ gap, dotSize, color }: CanvasPointerAuraProps) {
  const store = useStoreApi();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pointerRef = useRef({
    clientX: 0,
    clientY: 0,
    hasPointer: false,
    presence: 0,
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    // 收窄后的别名：闭包里直接引用 `canvas` / `context` 时 TS 会重新放宽成
    // 可空类型，用别名把非空类型固定下来。
    const surface: HTMLCanvasElement = canvas;
    const ink: CanvasRenderingContext2D = context;

    const gridColor = resolveGridRgb(color);
    const sprite = createDotSprite(gridColor);
    const radianceColor = mixRgb(gridColor, WHITE, 0.78);
    const radianceSprite = createRadianceSprite(radianceColor);
    let frame = 0;
    let pixelWidth = 0;
    let pixelHeight = 0;

    function schedule() {
      if (frame === 0) frame = window.requestAnimationFrame(draw);
    }

    function draw() {
      frame = 0;
      const rect = surface.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;

      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const nextWidth = Math.max(1, Math.round(rect.width * dpr));
      const nextHeight = Math.max(1, Math.round(rect.height * dpr));
      if (nextWidth !== pixelWidth || nextHeight !== pixelHeight) {
        surface.width = nextWidth;
        surface.height = nextHeight;
        pixelWidth = nextWidth;
        pixelHeight = nextHeight;
      }
      ink.setTransform(dpr, 0, 0, dpr, 0, 0);
      ink.clearRect(0, 0, rect.width, rect.height);

      const pointer = pointerRef.current;
      const localX = pointer.clientX - rect.left;
      const localY = pointer.clientY - rect.top;
      const inside =
        pointer.hasPointer &&
        localX >= 0 &&
        localX <= rect.width &&
        localY >= 0 &&
        localY <= rect.height;
      const target = inside ? 1 : 0;
      pointer.presence += (target - pointer.presence) * AURA_EASE;
      if (Math.abs(target - pointer.presence) < 0.004) {
        pointer.presence = target;
      }
      const presence = pointer.presence;

      if (presence > 0) {
        ink.globalAlpha = 0.1 * presence;
        ink.drawImage(
          radianceSprite,
          localX - AURA_RADIANCE,
          localY - AURA_RADIANCE,
          AURA_RADIANCE * 2,
          AURA_RADIANCE * 2,
        );
        ink.globalAlpha = 1;

        const { transform } = store.getState();
        const zoom = transform[2];
        const metrics = resolveCanvasGridRenderMetrics(gap, dotSize, zoom);
        const grid = resolvePointerAuraGrid({
          gap: metrics.gap,
          dotSize: metrics.dotSize,
          zoom,
          translateX: transform[0],
          translateY: transform[1],
        });

        forEachPointerAuraDot(grid, localX, localY, AURA_REACH, (x, y, intensity) => {
          if (x < -16 || x > rect.width + 16 || y < -16 || y > rect.height + 16) {
            return;
          }
          const strength = intensity * presence;
          if (strength <= 0.01) return;
          const radius = grid.dotRadius * (1 + 1.7 * strength);
          const size = SPRITE_UNIT * (radius / SPRITE_BODY_RADIUS);
          ink.globalAlpha = strength;
          ink.drawImage(sprite, x - size / 2, y - size / 2, size, size);
        });
        ink.globalAlpha = 1;
      }

      if (target !== presence) schedule();
    }

    const handlePointerMove = (event: PointerEvent) => {
      if (
        event.pointerType &&
        event.pointerType !== 'mouse' &&
        event.pointerType !== 'pen'
      ) {
        return;
      }
      const pointer = pointerRef.current;
      pointer.clientX = event.clientX;
      pointer.clientY = event.clientY;
      pointer.hasPointer = true;
      schedule();
    };

    const hidePointer = () => {
      pointerRef.current.hasPointer = false;
      schedule();
    };

    // 必须在捕获阶段监听：画布的框选（左键在空白处拖）在 window 捕获阶段
    // 调 `stopPropagation()`，冒泡阶段的监听从拖过 6px 起就收不到指针，光斑
    // 会冻在拖拽起点，直到松手后才追上来。
    window.addEventListener('pointermove', handlePointerMove, {
      passive: true,
      capture: true,
    });
    window.addEventListener('scroll', schedule, { passive: true, capture: true });
    window.addEventListener('resize', schedule, { passive: true });
    window.addEventListener('blur', hidePointer);
    document.documentElement.addEventListener('pointerleave', hidePointer, {
      passive: true,
    });

    const unsubscribe = store.subscribe((state, previous) => {
      if (state.transform !== previous.transform) schedule();
    });

    const observer =
      typeof ResizeObserver === 'function' ? new ResizeObserver(schedule) : null;
    observer?.observe(surface);

    // 触屏没有悬停指针，别让初始坐标处亮着一块。
    if (
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(hover: none)').matches
    ) {
      hidePointer();
    }

    return () => {
      window.removeEventListener('pointermove', handlePointerMove, {
        capture: true,
      });
      window.removeEventListener('scroll', schedule, { capture: true });
      window.removeEventListener('resize', schedule);
      window.removeEventListener('blur', hidePointer);
      document.documentElement.removeEventListener('pointerleave', hidePointer);
      unsubscribe();
      observer?.disconnect();
      if (frame !== 0) window.cancelAnimationFrame(frame);
    };
  }, [store, gap, dotSize, color]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      data-testid="canvas-pointer-aura"
      style={{
        position: 'absolute',
        inset: 0,
        display: 'block',
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        // 压在 XYFlow 的 pattern 背景（z-index: -1）之上、节点视口（z-index: 2）之下。
        zIndex: 0,
      }}
    />
  );
}
