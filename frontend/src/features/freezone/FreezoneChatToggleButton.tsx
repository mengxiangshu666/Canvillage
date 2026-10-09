import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { AgentMark } from "@/components/ui/AgentMark";

/**
 * Agent 入口的位置（相对容器右下角的 right/bottom 偏移，px）。
 * 注意 key 不用 `village-` 前缀——那个前缀会被 reset-region-state 的
 * localStorage 清扫误删；这只是个 UI 位置偏好，跨区域保留没问题。
 */
const CHAT_LAUNCHER_POS_STORAGE_KEY = "st.freezone.chatLauncherPos";
/** Circular Agent launcher. */
const CHAT_LAUNCHER_WIDTH = 56;
const CHAT_LAUNCHER_HEIGHT = 56;
const CHAT_LAUNCHER_MARGIN = 8;
/** 默认抬到 MiniMap（约 150px 高 + 15px 边距）上方，避免挡住画布缩略图。 */
const CHAT_LAUNCHER_DEFAULT_POS = { right: 16, bottom: 180 };
const CHAT_LAUNCHER_DRAG_THRESHOLD = 4;

function loadChatLauncherPos(): { right: number; bottom: number } {
  try {
    const raw = window.localStorage.getItem(CHAT_LAUNCHER_POS_STORAGE_KEY);
    if (!raw) return CHAT_LAUNCHER_DEFAULT_POS;
    const parsed = JSON.parse(raw) as { right?: unknown; bottom?: unknown };
    if (typeof parsed.right === "number" && typeof parsed.bottom === "number") {
      return { right: parsed.right, bottom: parsed.bottom };
    }
  } catch {
    // ignore malformed storage
  }
  return CHAT_LAUNCHER_DEFAULT_POS;
}

export function FreezoneChatToggleButton({
  label,
  expanded,
  onClick,
}: {
  label: string;
  expanded: boolean;
  onClick: () => void;
}) {
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const [entered, setEntered] = useState(false);
  const [pos, setPos] = useState(loadChatLauncherPos);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const parent = buttonRef.current?.offsetParent as HTMLElement | null;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();
    const maxRight = rect.width - CHAT_LAUNCHER_WIDTH - CHAT_LAUNCHER_MARGIN;
    const maxBottom = rect.height - CHAT_LAUNCHER_HEIGHT - CHAT_LAUNCHER_MARGIN;
    setPos((current) => {
      const clamped = {
        right: Math.min(Math.max(current.right, CHAT_LAUNCHER_MARGIN), maxRight),
        bottom: Math.min(Math.max(current.bottom, CHAT_LAUNCHER_MARGIN), maxBottom),
      };
      return clamped.right === current.right && clamped.bottom === current.bottom
        ? current
        : clamped;
    });
  }, []);

  const handlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      if (event.button !== 0) return;
      const parent = buttonRef.current?.offsetParent as HTMLElement | null;
      const parentRect = parent?.getBoundingClientRect();
      const start = {
        x: event.clientX,
        y: event.clientY,
        right: pos.right,
        bottom: pos.bottom,
      };
      let dragged = false;
      let latest = { right: pos.right, bottom: pos.bottom };

      const clamp = (value: number, max: number) =>
        Math.min(Math.max(value, CHAT_LAUNCHER_MARGIN), max);

      const onMove = (ev: PointerEvent) => {
        const dx = ev.clientX - start.x;
        const dy = ev.clientY - start.y;
        if (!dragged && Math.hypot(dx, dy) < CHAT_LAUNCHER_DRAG_THRESHOLD) return;
        dragged = true;
        const maxRight = parentRect
          ? parentRect.width - CHAT_LAUNCHER_WIDTH - CHAT_LAUNCHER_MARGIN
          : Number.MAX_SAFE_INTEGER;
        const maxBottom = parentRect
          ? parentRect.height - CHAT_LAUNCHER_HEIGHT - CHAT_LAUNCHER_MARGIN
          : Number.MAX_SAFE_INTEGER;
        latest = {
          right: clamp(start.right - dx, maxRight),
          bottom: clamp(start.bottom - dy, maxBottom),
        };
        setPos(latest);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        if (dragged) {
          suppressClickRef.current = true;
          try {
            window.localStorage.setItem(
              CHAT_LAUNCHER_POS_STORAGE_KEY,
              JSON.stringify(latest),
            );
          } catch {
            // storage full / unavailable — position just won't persist
          }
        }
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [pos.bottom, pos.right],
  );

  const handleClick = useCallback(() => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    onClick();
  }, [onClick]);

  return (
    <Button
      ref={buttonRef}
      type="button"
      size="icon-lg"
      variant="secondary"
      className={cn(
        "absolute z-50 h-14 w-14 cursor-grab touch-none overflow-visible rounded-full border border-white/12 bg-transparent p-0 shadow-[0_12px_32px_rgba(0,0,0,0.5)] transition-[opacity,transform] duration-200 ease-out hover:scale-[1.06] active:cursor-grabbing",
        entered ? "opacity-100" : "opacity-0",
        expanded && "ring-2 ring-emerald-400/55 ring-offset-2 ring-offset-black/40",
      )}
      style={{ right: pos.right, bottom: pos.bottom }}
      aria-label={label}
      aria-expanded={expanded}
      title={undefined}
      onPointerDown={handlePointerDown}
      onClick={handleClick}
      data-agent-launcher="agent"
    >
      <AgentMark
        size="launcher"
        className={cn(expanded && "agent-mark--pulse", "pointer-events-none")}
      />
      <span
        className={cn(
          "pointer-events-none absolute -bottom-0.5 -right-0.5 z-10 size-2.5 rounded-full ring-2 ring-[#0b0b0c]",
          expanded ? "bg-emerald-400" : "bg-white/70",
        )}
        aria-hidden
      />
    </Button>
  );
}
