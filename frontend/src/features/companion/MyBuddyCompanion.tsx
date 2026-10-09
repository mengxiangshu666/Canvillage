// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { SpritePetCompanion } from "@/features/companion/petdex/SpritePetCompanion";
import {
  loadImportedPets,
  type ImportedPetEntry,
} from "@/features/companion/petdex/petdex-storage";
import {
  fetchLocalPets,
  PETDEX_STATE_OPTIONS,
  PETDEX_STATES,
  resolveActivePet,
  type PetdexCatalogEntry,
  type PetdexStateName,
} from "@/features/companion/petdex/petdex-pets";
import { useAppStore } from "@/stores/app-store";
import { useTaskCenterStore } from "@/task-center/store";
import { displayLabel, isActive, isTerminal } from "@/task-center/derivations";
import {
  BUBBLE_FADE_OUT_MS,
  BUBBLE_VISIBLE_MS,
  FAILURE_ACTION_MS,
  SUCCESS_ACTION_MS,
  useMyBuddyCompanionController,
} from "@/features/companion/use-mybuddy-companion-controller";
import { openVersionUpdateDialog } from "@/features/version-update/version-update-events";
import { beginCanvasInteraction } from "@/features/canvas/application/canvasInteractionPerformance";

/** 气泡可见时长按事件种类对齐宠物动作时长（成功/失败动画结束时气泡同步收尾）。 */
const PET_BUBBLE_VISIBLE_MS: Record<"running" | "success" | "failure", number> = {
  running: BUBBLE_VISIBLE_MS,
  success: SUCCESS_ACTION_MS,
  failure: FAILURE_ACTION_MS,
};

type PendingCompanionDragFrame = {
  left: number;
  top: number;
  deltaX: number;
  deltaY: number;
  tilt: number;
};

type ViewportSize = {
  width: number;
  height: number;
};

const COMPANION_FIGURE_W = 72;
const COMPANION_FIGURE_H = 80;
const DRAG_TRAIL_MAX = 10;
const DRAG_TRAIL_LIFETIME_MS = 520;
const DRAG_TRAIL_DISTANCE_PX = 11;
const DRAG_TILT_MAX_DEG = 14;
const DRAG_TILT_FACTOR = 0.58;
const COMPANION_DEBUG = import.meta.env.VITE_COMPANION_DEBUG === "true";

function currentViewportSize(): ViewportSize {
  if (typeof window === "undefined") return { width: 1280, height: 720 };
  return { width: window.innerWidth, height: window.innerHeight };
}

function randomCompanionHomeXPercent() {
  const leftRange = [18, 40] as const;
  const rightRange = [60, 82] as const;
  const [min, max] = Math.random() < 0.5 ? leftRange : rightRange;
  return min + Math.random() * (max - min);
}

export type CompanionTapIntent = "activate-agent" | "cycle-pet";

export function resolveCompanionTapIntent({
  hasAgentEntry,
  alternateAction = false,
}: {
  hasAgentEntry: boolean;
  alternateAction?: boolean;
}): CompanionTapIntent {
  if (hasAgentEntry && !alternateAction) return "activate-agent";
  return "cycle-pet";
}

/** Agent 入口属于产品功能，即使用户隐藏了装饰搭子也必须保留。 */
export function shouldRenderCompanion(
  companionHidden: boolean,
  agentEntryMode: boolean,
): boolean {
  return agentEntryMode || !companionHidden;
}

const AGENT_ANCHOR_LEFT =
  "var(--village-agent-companion-left, max(8px, calc(100vw - min(560px, 100vw) - 42px)))";
const AGENT_ANCHOR_TOP = "var(--village-agent-companion-top, 72px)";

/**
 * Agent 打开时只临时改变显示坐标，不改写用户拖拽后持久化的画布落点。
 * 面板关闭后组件会直接回到原来的 left/top。
 */
export function resolveCompanionViewportPosition({
  agentActive,
  left,
  top,
}: {
  agentActive: boolean;
  left: number;
  top: number;
}): Pick<CSSProperties, "left" | "top"> {
  if (!agentActive) return { left, top };
  return {
    left: AGENT_ANCHOR_LEFT,
    top: AGENT_ANCHOR_TOP,
  };
}

type MyBuddyCompanionProps = {
  onActivateAgent?: () => void;
  agentActive?: boolean;
  agentEntryMode?: boolean;
};

export function MyBuddyCompanion({
  onActivateAgent,
  agentActive = false,
  agentEntryMode = false,
}: MyBuddyCompanionProps = {}) {
  const { t } = useTranslation();
  const { action } = useMyBuddyCompanionController();
  const [importRefreshKey, setImportRefreshKey] = useState(0);
  const [importedPets, setImportedPets] = useState<ImportedPetEntry[]>([]);
  const [localPets, setLocalPets] = useState<PetdexCatalogEntry[]>([]);
  const [homeXPercent] = useState(randomCompanionHomeXPercent);
  // 宠物状态的手动覆盖（null = 跟随系统/任务）。「状态模拟」下拉选择 + 点击宠物循环共用。
  const [petStateName, setPetStateName] = useState<PetdexStateName | null>(null);
  const [companionResetNonce, setCompanionResetNonce] = useState(0);
  const [dropRippleNonce, setDropRippleNonce] = useState(0);
  const [dropSettling, setDropSettling] = useState(false);
  const [viewportSize, setViewportSize] = useState(currentViewportSize);
  const floatingRef = useRef<HTMLDivElement | null>(null);
  const dragTrailRef = useRef<HTMLDivElement | null>(null);
  const dragPixelIdRef = useRef(0);
  const dragPixelTimersRef = useRef<Set<number>>(new Set());
  const dragFrameRef = useRef<number | null>(null);
  const dragReleaseFrameRef = useRef<number | null>(null);
  const pendingDragFrameRef = useRef<PendingCompanionDragFrame | null>(null);
  const dragPerformanceEndRef = useRef<(() => void) | null>(null);
  const dropSettleTimerRef = useRef<number | null>(null);
  const companionKind = useAppStore((state) => state.companionKind);
  const companionPet = useAppStore((state) => state.companionPet);
  const companionXPercent = useAppStore((state) => state.companionXPercent);
  const companionYPercent = useAppStore((state) => state.companionYPercent);
  const companionHidden = useAppStore((state) => state.companionHidden);
  const setCompanionPosition = useAppStore((state) => state.setCompanionPosition);

  // 内置宠物目录（pets.json）。形象地址以目录为准，这样下架某只宠物后不会留下
  // 一个 404 的空形象。
  useEffect(() => {
    let cancelled = false;
    fetchLocalPets()
      .then((pets) => {
        if (!cancelled) setLocalPets(pets);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // 加载已导入宠物（IndexedDB），为每只建会话级 blob URL。回收策略：拿到新的一批后
  // 再 revoke 上一批（而不是在 cleanup 里先 revoke——否则会把当前正在显示的那只 URL
  // 提前撤销，导致刷新时闪一下），unmount 时回收最后一批。
  const importedUrlsRef = useRef<string[]>([]);
  useEffect(() => {
    let cancelled = false;
    loadImportedPets()
      .then((pets) => {
        if (cancelled) {
          pets.forEach((p) => URL.revokeObjectURL(p.spritesheetUrl));
          return;
        }
        const previous = importedUrlsRef.current;
        importedUrlsRef.current = pets.map((p) => p.spritesheetUrl);
        setImportedPets(pets);
        previous.forEach((url) => URL.revokeObjectURL(url));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [importRefreshKey]);

  useEffect(() => {
    const refreshImportedPets = () => setImportRefreshKey((key) => key + 1);
    window.addEventListener("mybuddy-imported-pets-changed", refreshImportedPets);
    return () => window.removeEventListener("mybuddy-imported-pets-changed", refreshImportedPets);
  }, []);

  useEffect(
    () => () => {
      importedUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    },
    [],
  );

  useEffect(() => {
    const resetCompanionState = () => {
      setPetStateName(null);
      setCompanionResetNonce((nonce) => nonce + 1);
    };
    window.addEventListener("mybuddy-companion-reset", resetCompanionState);
    return () => window.removeEventListener("mybuddy-companion-reset", resetCompanionState);
  }, []);

  const importedBySlug = useMemo(
    () => new Map(importedPets.map((p) => [p.slug, p] as const)),
    [importedPets],
  );

  // 当前宠物：导入宠物按 slug 取会话级 blob URL（持久化的 url 刷新后失效）；内置宠物
  // 以目录为准取条目。选中的那只已下架时退回目录第一只，而不是渲染一个 404 空形象。
  const activePet = useMemo(
    () =>
      resolveActivePet({
        kind: companionKind,
        selected: companionPet,
        importedBySlug,
        catalog: localPets,
      }),
    [companionKind, companionPet, importedBySlug, localPets],
  );

  // 宠物气泡：任务「开始 / 成功 / 失败」时事件触发，显示几秒后淡出。
  const tasks = useTaskCenterStore((state) => state.tasks);
  const [petBubble, setPetBubble] = useState<{
    kind: "running" | "success" | "failure";
    text: string;
  } | null>(null);
  const [petBubbleLeaving, setPetBubbleLeaving] = useState(false);
  const prevStatusRef = useRef<Map<string, string>>(new Map());
  const bubbleInitedRef = useRef(false);
  const bubbleTimerRef = useRef<number | null>(null);
  const bubbleLeaveTimerRef = useRef<number | null>(null);

  useEffect(() => {
    // 按任务 key 比对上一帧状态，捕捉「新开始 / 新成功 / 新失败」的跳变。
    const current = new Map<string, string>();
    let event: { kind: "running" | "success" | "failure"; name: string } | null = null;
    for (const [key, task] of tasks) {
      current.set(key, task.status);
      const prev = prevStatusRef.current.get(key);
      if (!bubbleInitedRef.current || prev === task.status) continue;
      if (isActive(task)) event = { kind: "running", name: displayLabel(task, t) };
      else if (isTerminal(task) && task.status === "completed")
        event = { kind: "success", name: displayLabel(task, t) };
      else if (isTerminal(task) && task.status === "failed")
        event = { kind: "failure", name: displayLabel(task, t) };
    }
    prevStatusRef.current = current;
    if (!bubbleInitedRef.current) {
      // 首帧记基线；但若此刻已有任务在跑，也弹一次「进行中」气泡（否则挂载时
      // 已在跑的任务永远不会有气泡）。
      bubbleInitedRef.current = true;
      const active = Array.from(tasks.values()).find(isActive);
      if (!active) return;
      event = { kind: "running", name: displayLabel(active, t) };
    }
    if (!event) return;
    const textKey =
      event.kind === "running"
        ? "myBuddy.taskRunning"
        : event.kind === "success"
          ? "myBuddy.taskSuccess"
          : "myBuddy.taskFailure";
    if (bubbleTimerRef.current) window.clearTimeout(bubbleTimerRef.current);
    if (bubbleLeaveTimerRef.current) window.clearTimeout(bubbleLeaveTimerRef.current);
    setPetBubbleLeaving(false);
    setPetBubble({ kind: event.kind, text: t(textKey, { name: event.name }) });
    // 可见时长随事件种类对齐动作动画（成功 2.6s / 失败 3.6s / 进行中 3.5s）→ 淡出 → 卸载。
    const visibleMs = PET_BUBBLE_VISIBLE_MS[event.kind];
    bubbleTimerRef.current = window.setTimeout(() => setPetBubbleLeaving(true), visibleMs);
    bubbleLeaveTimerRef.current = window.setTimeout(
      () => setPetBubble(null),
      visibleMs + BUBBLE_FADE_OUT_MS,
    );
  }, [tasks, t]);

  useEffect(
    () => () => {
      if (bubbleTimerRef.current) window.clearTimeout(bubbleTimerRef.current);
      if (bubbleLeaveTimerRef.current) window.clearTimeout(bubbleLeaveTimerRef.current);
    },
    [],
  );

  useEffect(
    () => () => {
      dragPixelTimersRef.current.forEach((timer) => window.clearTimeout(timer));
      dragPixelTimersRef.current.clear();
      if (dragFrameRef.current) window.cancelAnimationFrame(dragFrameRef.current);
      if (dragReleaseFrameRef.current) window.cancelAnimationFrame(dragReleaseFrameRef.current);
      dragPerformanceEndRef.current?.();
      dragPerformanceEndRef.current = null;
      if (dropSettleTimerRef.current) window.clearTimeout(dropSettleTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    const handleResize = () => setViewportSize(currentViewportSize());
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  // 位置：占视口宽/高的百分比，可整页任意拖动。未拖动过用默认落点（顶部随机 x）。
  const xPercent = companionXPercent ?? homeXPercent;
  // 画布版搭子避开 48px 顶栏，默认落点仍保持在视口上方区域，
  // 但不会再被顶部栏裁掉；拖动后的用户位置继续优先保留。
  const yPercent = companionYPercent ?? 8.5;
  const displayLeftPx = Math.round((xPercent / 100) * viewportSize.width);
  const displayTopPx = Math.round((yPercent / 100) * viewportSize.height);
  const viewportPosition = resolveCompanionViewportPosition({
    agentActive: agentEntryMode && agentActive,
    left: displayLeftPx,
    top: displayTopPx,
  });

  const positionDragPixels = useCallback((left: number, top: number) => {
    const trail = dragTrailRef.current;
    if (!trail) return;
    for (const child of Array.from(trail.children)) {
      if (!(child instanceof HTMLElement)) continue;
      const x = Number(child.dataset.screenX);
      const y = Number(child.dataset.screenY);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      child.style.left = `${x - left}px`;
      child.style.top = `${y - top}px`;
    }
  }, []);

  const emitDragPixel = useCallback((x: number, y: number, variant: number) => {
    const trail = dragTrailRef.current;
    if (!trail) return;
    const id = dragPixelIdRef.current + 1;
    dragPixelIdRef.current = id;
    while (trail.childElementCount >= DRAG_TRAIL_MAX) {
      trail.firstElementChild?.remove();
    }
    const pixel = document.createElement('span');
    pixel.className = 'mybuddy-companion-drag-pixel';
    pixel.dataset.variant = String(variant);
    pixel.dataset.screenX = String(x);
    pixel.dataset.screenY = String(y);
    trail.append(pixel);
    const timer = window.setTimeout(() => {
      pixel.remove();
      dragPixelTimersRef.current.delete(timer);
    }, DRAG_TRAIL_LIFETIME_MS);
    dragPixelTimersRef.current.add(timer);
  }, []);

  const scheduleDragFrame = useCallback((frame: PendingCompanionDragFrame) => {
    pendingDragFrameRef.current = frame;
    if (dragFrameRef.current) return;
    dragFrameRef.current = window.requestAnimationFrame(() => {
      const nextFrame = pendingDragFrameRef.current;
      pendingDragFrameRef.current = null;
      dragFrameRef.current = null;
      const floating = floatingRef.current;
      if (!nextFrame || !floating) return;
      floating.style.transform = `translate3d(${nextFrame.deltaX}px, ${nextFrame.deltaY}px, 0)`;
      floating.style.setProperty('--mybuddy-drag-tilt', `${nextFrame.tilt}deg`);
      positionDragPixels(nextFrame.left, nextFrame.top);
    });
  }, [positionDragPixels]);

  // 点击宠物：在 9 个状态（含「跟随系统」auto=null）之间循环切换动作预览。
  const cyclePetState = useCallback(() => {
    setPetStateName((current) => {
      const order: (PetdexStateName | null)[] = [null, ...PETDEX_STATE_OPTIONS.map((o) => o.name)];
      const next = (order.indexOf(current) + 1) % order.length;
      return order[next];
    });
  }, []);

  const handlePetStateChange = useCallback((event: ChangeEvent<HTMLSelectElement>) => {
    const next = event.target.value;
    setPetStateName(next === "auto" ? null : (next as PetdexStateName));
  }, []);

  // 拖拽 / 点击：透明把手层捕获指针。移动超过阈值 → 2D 拖动定位（按视口百分比持久化）；
  // 几乎没动 → 视为点击。村长画布中点击打开 Agent；普通模式点击循环预览宠物状态。
  const handleDragPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      if (agentEntryMode && agentActive) {
        if (event.altKey || event.shiftKey) cyclePetState();
        else onActivateAgent?.();
        return;
      }
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const startX = event.clientX;
      const startY = event.clientY;
      const startLeftPx = Math.round((xPercent / 100) * vw);
      const startTopPx = Math.round((yPercent / 100) * vh);
      const grabX = startX - startLeftPx;
      const grabY = startY - startTopPx;
      let dragging = false;
      let latestXPercent = xPercent;
      let latestYPercent = yPercent;
      let latestLeftPx = startLeftPx;
      let latestTopPx = startTopPx;
      let lastX = startX;
      let lastY = startY;
      let lastTrailX = startX;
      let lastTrailY = startY;
      if (dragReleaseFrameRef.current) {
        window.cancelAnimationFrame(dragReleaseFrameRef.current);
        dragReleaseFrameRef.current = null;
        floatingRef.current?.removeAttribute('data-dragging');
      }
      floatingRef.current?.style.setProperty("--mybuddy-drag-tilt", "0deg");
      const onMove = (moveEvent: PointerEvent) => {
        if (!dragging && Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) < 4) {
          return;
        }
        if (!dragging) {
          dragPerformanceEndRef.current?.();
          dragPerformanceEndRef.current = beginCanvasInteraction('companion-drag');
          floatingRef.current?.setAttribute('data-dragging', 'true');
        }
        dragging = true;
        const px = Math.round(
          Math.min(Math.max(0, moveEvent.clientX - grabX), Math.max(0, vw - COMPANION_FIGURE_W)),
        );
        const py = Math.round(
          Math.min(Math.max(0, moveEvent.clientY - grabY), Math.max(0, vh - COMPANION_FIGURE_H)),
        );
        latestLeftPx = px;
        latestTopPx = py;
        latestXPercent = (px / vw) * 100;
        latestYPercent = (py / vh) * 100;
        const moveX = moveEvent.clientX - lastX;
        const moveY = moveEvent.clientY - lastY;
        const speed = Math.hypot(moveX, moveY);
        const tilt =
          speed > 0.5
            ? Math.max(-DRAG_TILT_MAX_DEG, Math.min(DRAG_TILT_MAX_DEG, moveX * DRAG_TILT_FACTOR))
            : 0;
        scheduleDragFrame({
          left: px,
          top: py,
          deltaX: px - startLeftPx,
          deltaY: py - startTopPx,
          tilt,
        });
        const distanceFromTrail = Math.hypot(
          moveEvent.clientX - lastTrailX,
          moveEvent.clientY - lastTrailY,
        );
        if (distanceFromTrail >= DRAG_TRAIL_DISTANCE_PX) {
          const trailSpeed = Math.max(1, speed);
          const oppositeX = -(moveX / trailSpeed);
          const oppositeY = -(moveY / trailSpeed);
          const centerX = px + COMPANION_FIGURE_W * 0.46;
          const centerY = py + COMPANION_FIGURE_H * 0.46;
          const jitter = (dragPixelIdRef.current % 3) - 1;
          emitDragPixel(
            centerX + oppositeX * 16 + jitter * 2,
            centerY + oppositeY * 10 - jitter,
            dragPixelIdRef.current % 3,
          );
          lastTrailX = moveEvent.clientX;
          lastTrailY = moveEvent.clientY;
        }
        lastX = moveEvent.clientX;
        lastY = moveEvent.clientY;
      };
      const onUp = (upEvent?: PointerEvent) => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        window.removeEventListener('blur', onBlur);
        dragPerformanceEndRef.current?.();
        dragPerformanceEndRef.current = null;
        const floating = floatingRef.current;
        floating?.style.setProperty("--mybuddy-drag-tilt", "0deg");
        if (dragging) {
          if (dragFrameRef.current) {
            window.cancelAnimationFrame(dragFrameRef.current);
            dragFrameRef.current = null;
            pendingDragFrameRef.current = null;
          }
          if (floating) {
            floating.style.left = `${latestLeftPx}px`;
            floating.style.top = `${latestTopPx}px`;
            floating.style.transform = 'none';
          }
          positionDragPixels(latestLeftPx, latestTopPx);
          // 先把唯一持久坐标写入 store，再在下一帧撤掉拖动样式。
          // 避免本地拖动状态先渲染旧坐标，出现“回原位再弹回来”的一帧闪跳。
          setCompanionPosition(latestXPercent, latestYPercent);
          dragReleaseFrameRef.current = window.requestAnimationFrame(() => {
            floatingRef.current?.removeAttribute('data-dragging');
            dragReleaseFrameRef.current = null;
          });
          if (dropSettleTimerRef.current) window.clearTimeout(dropSettleTimerRef.current);
          setDropSettling(true);
          setDropRippleNonce((nonce) => nonce + 1);
          dropSettleTimerRef.current = window.setTimeout(() => {
            setDropSettling(false);
            dropSettleTimerRef.current = null;
          }, 260);
        }
        if (!dragging) {
          const intent = resolveCompanionTapIntent({
            hasAgentEntry: Boolean(onActivateAgent),
            alternateAction: Boolean(upEvent?.altKey || upEvent?.shiftKey),
          });
          if (intent === "activate-agent") onActivateAgent?.();
          else cyclePetState();
        }
      };
      const onBlur = () => onUp();
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
      window.addEventListener('blur', onBlur);
    },
    [
      cyclePetState,
      emitDragPixel,
      agentActive,
      agentEntryMode,
      onActivateAgent,
      positionDragPixels,
      scheduleDragFrame,
      setCompanionPosition,
      xPercent,
      yPercent,
    ],
  );

  // 切换形象时清掉手动覆盖，回到跟随系统。
  useEffect(() => {
    setPetStateName(null);
  }, [companionKind]);

  // 全局隐藏只影响装饰陪伴形象；画布里的搭子是 Agent 唯一入口，不能一起被关掉。
  if (!shouldRenderCompanion(companionHidden, agentEntryMode)) return null;

  return (
    <div
      className="mybuddy-companion-lane"
      data-agent-entry={agentEntryMode ? "true" : undefined}
      data-agent-active={agentEntryMode && agentActive ? "true" : undefined}
    >
      {COMPANION_DEBUG && (
        <div className="mybuddy-companion-debug mybuddy-companion-debug-panel">
          <span className="mybuddy-companion-debug-label">
            <span className="mybuddy-companion-debug-brand">搭子</span>
            {t("myBuddy.debug.suffix")}
          </span>
          <select
            className="mybuddy-companion-debug-select mybuddy-companion-debug-action-select"
            value={petStateName ?? "auto"}
            onChange={handlePetStateChange}
            aria-label={t("myBuddy.debug.stateSimulation")}
          >
            <option value="auto">{t("myBuddy.debug.auto")}</option>
            {PETDEX_STATE_OPTIONS.map((option) => (
              <option key={option.name} value={option.name}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="mybuddy-companion-debug-button"
            data-tone="neutral"
            onClick={openVersionUpdateDialog}
          >
            {t("myBuddy.debug.triggerUpdateDialog")}
          </button>
        </div>
      )}
      {/* 整页任意定位的浮动容器（fixed，相对视口）。形象/气泡/拖拽把手都在其中。 */}
      <div
        ref={floatingRef}
        className="mybuddy-companion-floating"
        data-companion-kind="petdex"
        data-settling={dropSettling || undefined}
        data-agent-entry={agentEntryMode || undefined}
        data-agent-active={agentEntryMode && agentActive ? "true" : undefined}
        style={{
          position: "fixed",
          ...viewportPosition,
          width: 66,
          height: 80,
          zIndex: agentEntryMode && agentActive ? 110 : agentEntryMode ? 72 : 25,
          pointerEvents: "none",
        }}
      >
        <div ref={dragTrailRef} className="mybuddy-companion-drag-trail" aria-hidden="true" />
        {dropRippleNonce > 0 && (
          <span
            key={dropRippleNonce}
            className="mybuddy-companion-drop-ripple"
            aria-hidden="true"
          />
        )}
        <div className="mybuddy-companion-motion-layer">
          <div className="mybuddy-companion-settle-layer">
            {activePet ? (
              <SpritePetCompanion
                key={`pet-${activePet.slug}-${companionResetNonce}`}
                action={action}
                pet={activePet}
                stateOverride={petStateName ? PETDEX_STATES[petStateName] : null}
              />
            ) : null}
          </div>
        </div>
        {/* 任务开始/成功/失败：宠物右上角的马赛克像素风气泡，几秒后淡出。 */}
        {activePet && petBubble && (
          <div
            className="petdex-pet-bubble"
            data-kind={petBubble.kind}
            data-leaving={petBubbleLeaving || undefined}
            role="status"
          >
            {petBubble.text}
          </div>
        )}
        {/* 透明拖拽把手：盖在形象上方，按住可整页任意拖动。 */}
        <div
          className="mybuddy-companion-drag-handle"
          onPointerDown={handleDragPointerDown}
          style={{
            position: "absolute",
            inset: 0,
            pointerEvents: "auto",
            touchAction: "none",
            zIndex: 3,
          }}
          title={
            agentEntryMode
              ? agentActive
                ? "搭子正靠在 Agent 创作台上；点击收起创作台"
                : "点击搭子打开 Agent 创作台；拖动可移动；Shift/Alt 点击预览宠物动作"
              : t("myBuddy.dragHint")
          }
          role={agentEntryMode ? "button" : undefined}
          aria-label={
            agentEntryMode
              ? agentActive
                ? "收起搭子创作台"
                : "打开搭子创作台"
              : undefined
          }
          aria-pressed={agentEntryMode ? agentActive : undefined}
          aria-hidden={agentEntryMode ? undefined : true}
          tabIndex={agentEntryMode ? 0 : undefined}
          onKeyDown={(event) => {
            if (!agentEntryMode || !onActivateAgent) return;
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            onActivateAgent();
          }}
        />
      </div>
    </div>
  );
}
