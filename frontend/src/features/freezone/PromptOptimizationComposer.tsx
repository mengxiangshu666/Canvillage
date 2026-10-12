// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { ArrowRight, WandSparkles, X } from "lucide-react";

interface PromptOptimizationComposerProps {
  value: string;
  busy?: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onClose: () => void;
}

/**
 * A small human-steering pass before the model compiler runs.
 *
 * The current node prompt remains the source of truth. This field is only a
 * fast way to tell the director what to protect or change for this pass.
 */
export function PromptOptimizationComposer({
  value,
  busy = false,
  onChange,
  onSubmit,
  onClose,
}: PromptOptimizationComposerProps) {
  return (
    <div
      className="nodrag nowheel absolute bottom-14 left-3 right-3 z-[71] rounded-[14px] border border-white/[0.14] bg-black/80 p-3 text-white shadow-xl backdrop-blur-xl"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-xs font-medium">
          <WandSparkles className="h-3.5 w-3.5 shrink-0 text-amber-200" aria-hidden="true" />
          <span>告诉导演这次重点</span>
          <span className="truncate text-[10px] font-normal text-white/55">
            原提示词优先，空着也能直接优化
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          aria-label="关闭提示词优化要求"
          className="rounded p-1 text-white/60 transition hover:bg-white/10 hover:text-white disabled:opacity-40"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
      <textarea
        autoFocus
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            onSubmit();
          }
        }}
        rows={3}
        maxLength={2000}
        placeholder="例如：先让人物犹豫半秒，再抬头看向门外；镜头保持克制，别乱转；保留衣服和场景不变。"
        className="nodrag nowheel min-h-[72px] w-full resize-y rounded-lg border border-white/[0.12] bg-white/[0.07] px-2.5 py-2 text-xs leading-5 text-white outline-none placeholder:text-white/35 focus:border-amber-200/60"
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-[10px] text-white/45">Ctrl/⌘ + Enter 直接开始</span>
        <button
          type="button"
          onClick={onSubmit}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-md bg-amber-200 px-2.5 py-1.5 text-xs font-semibold text-black transition hover:bg-amber-100 disabled:cursor-wait disabled:opacity-60"
        >
          <span>{busy ? "正在优化…" : "开始优化"}</span>
          <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
