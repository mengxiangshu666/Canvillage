// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { TaskState } from "@/task-center/types";

export function TaskLogs({ task }: { task: TaskState }) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Auto-scroll to bottom when new log lines arrive.
    // The project's ScrollArea is built on base-ui, whose viewport carries
    // `data-slot="scroll-area-viewport"`. We also check the radix attribute
    // as a defensive fallback in case the primitive is swapped out later.
    const root = scrollRef.current;
    if (!root) return;
    const viewport =
      root.querySelector<HTMLDivElement>('[data-slot="scroll-area-viewport"]') ??
      root.querySelector<HTMLDivElement>("[data-radix-scroll-area-viewport]");
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }, [task.logs.length]);

  if (!task.logs.length) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        {t("taskCenter.detail.logs.placeholder")}
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="h-full">
      <ScrollArea className="h-full">
        <div className="space-y-1 p-4">
          {task.logs.map((line, index) => (
            <div
              key={`${index}-${line.slice(0, 24)}`}
              className="grid grid-cols-[28px_minmax(0,1fr)] gap-2 rounded-lg px-2 py-1.5 text-[11px] leading-5 odd:bg-white/[0.025]"
            >
              <span className="text-right tabular-nums text-white/24">{index + 1}</span>
              <span className="whitespace-pre-wrap break-words font-mono text-white/64">{line}</span>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
