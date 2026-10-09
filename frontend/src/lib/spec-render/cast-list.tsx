"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

export type CastEntry = { text: string; costumeDescription?: string };

export function CastList({ entries }: { entries: CastEntry[] }) {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const active = openIdx !== null ? entries[openIdx] : null;

  useEffect(() => {
    if (openIdx === null) return;
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpenIdx(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [openIdx]);

  return (
    <div className="mb-4">
      <div className="mb-2 text-ms font-medium text-white/50">
        【出场人物、身份】
      </div>
      <div className="space-y-1.5">
        {entries.map((entry, i) => (
          <div
            key={i}
            className="flex items-center gap-1 text-sm text-zinc-300"
          >
            <span className="text-white/60">•</span>
            <span>{entry.text}</span>
            {entry.costumeDescription && (
              <button
                type="button"
                className={`ml-1 inline-flex items-center gap-0.5 transition ${
                  openIdx === i
                    ? "text-cyan-300"
                    : "text-cyan-400/80 hover:text-cyan-300"
                }`}
                onClick={() => setOpenIdx(openIdx === i ? null : i)}
              >
                <span>服饰描述</span>
                <span className="text-[10px]">▸</span>
              </button>
            )}
          </div>
        ))}
      </div>

      {active?.costumeDescription &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={panelRef}
            className="fixed top-1/2 right-[20%] z-[60] w-80 -translate-y-1/2 rounded-xl border border-white/10 bg-[#131520] p-4 shadow-2xl"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-white/60">
                {active.text}
              </span>
              <button
                type="button"
                className="text-white/40 transition hover:text-white/80"
                onClick={() => setOpenIdx(null)}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <p className="text-sm leading-6 text-zinc-300 whitespace-pre-wrap">
              {active.costumeDescription}
            </p>
          </div>,
          document.body,
        )}
    </div>
  );
}
