// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from 'react';
import { Loader2, Wand2 } from 'lucide-react';

/**
 * 「只改这一镜」弹层。
 *
 * 这是脚本节点上第一个**局部**修改入口：此前只有「按当前提示词重新生成脚本」，
 * 改一句台词就要把整篇重出，既贵又会把已经满意的镜头一起换掉。
 *
 * 冻结事实显示本镜人物和全片风格；逐镜技术参数按创作目的调整。用户因此可以
 * 放心写「让她把信撕了」，而不必担心顺带把全片色调换掉。
 */
export interface ScriptShotRewriteDialogProps {
  open: boolean;
  /** 目标行序（0 基）。 */
  rowIndex: number;
  /** 用户看到的镜号。 */
  shotNo: string;
  /** 这一镜当前的画面描述，作为改动前后对照。 */
  currentSummary: string;
  /** 冻结清单（本镜角色卡 / 全片风格）摘要。 */
  frozenFacts: string[];
  /** 除本镜以外的镜数——说清「其余行不动」到底是多少行。 */
  untouchedCount: number;
  sequenceLabel?: string;
  targetCount?: number;
  busy?: boolean;
  canCancelTask?: boolean;
  /** 生成失败时的错误文案（就地显示，不关弹层）。 */
  error?: string | null;
  onCancel: () => void;
  onSubmit: (instruction: string) => void;
}

export function ScriptShotRewriteDialog({
  open,
  rowIndex,
  shotNo,
  currentSummary,
  frozenFacts,
  untouchedCount,
  sequenceLabel,
  targetCount,
  busy = false,
  canCancelTask = false,
  error = null,
  onCancel,
  onSubmit,
}: ScriptShotRewriteDialogProps) {
  const [instruction, setInstruction] = useState('');

  // 每次打开都从空开始：上一次的改写要求留在框里会让人以为这次也要用它。
  useEffect(() => {
    if (open) setInstruction('');
  }, [open, rowIndex, sequenceLabel]);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onCancel();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const trimmed = instruction.trim();

  return (
    <div
      className="fixed inset-0 z-[230] flex items-center justify-center bg-black/70 p-6"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex w-[min(520px,92vw)] flex-col gap-4 rounded-[14px] border border-white/[0.1] bg-[#1c1c1e]/98 p-4 text-text-dark shadow-[0_20px_60px_rgba(0,0,0,0.55)] backdrop-blur-xl">
        <header className="flex items-center gap-2">
          <Wand2 className="h-4 w-4 text-text-muted" />
          <span className="text-[13px] font-medium">{sequenceLabel ? `联合返工 · ${sequenceLabel}` : `只改第 ${shotNo} 镜`}</span>
        </header>

        <p className="text-[12px] leading-relaxed text-text-muted">
          {sequenceLabel ? `本段共 ${targetCount ?? 0} 镜，一次联合设计动作、节奏和切点；旁边 ${untouchedCount} 镜保持不动。` : `其余 ${untouchedCount} 镜保持不动。`}角色与全片风格沿用已有设定。
        </p>

        {currentSummary && (
          <div className="rounded-[10px] bg-white/[0.03] p-3">
            <div className="mb-1 text-[11px] uppercase tracking-wide text-text-muted/80">
              {sequenceLabel ? '这一段当前的镜头' : '这一镜现在是什么'}
            </div>
            <p className="max-h-24 overflow-y-auto ui-scrollbar text-[12px] leading-relaxed text-text-dark/90">
              {currentSummary}
            </p>
          </div>
        )}

        {frozenFacts.length > 0 && (
          <div className="rounded-[10px] border border-white/[0.06] p-3">
            <div className="mb-1 text-[11px] uppercase tracking-wide text-text-muted/80">
              不会被改动的部分
            </div>
            <ul className="flex flex-col gap-1">
              {frozenFacts.map((fact) => (
                <li key={fact} className="text-[12px] leading-relaxed text-text-muted">
                  {fact}
                </li>
              ))}
            </ul>
          </div>
        )}

        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] text-text-muted">你想怎么改</span>
          <textarea
            className="ui-scrollbar h-24 w-full resize-none rounded-[10px] border border-white/[0.1] bg-black/25 p-2.5 text-[12px] leading-relaxed text-text-dark outline-none transition-colors focus:border-white/[0.24]"
            placeholder="例如：把「她起身走向落地窗」改成「她停在原地，手指按住桌面，没有回头」"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            disabled={busy}
          />
        </label>

        {error && <p className="text-[12px] leading-relaxed text-red-300/90">{error}</p>}

        <footer className="flex items-center justify-end gap-2">
          <button
            type="button"
            className="h-7 rounded-[8px] px-2.5 text-[12px] text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text-dark disabled:opacity-50"
            onClick={onCancel}
            disabled={busy && !canCancelTask}
          >
            {busy ? '终止返工' : '取消'}
          </button>
          <button
            type="button"
            className="inline-flex h-7 items-center gap-1.5 rounded-[8px] bg-white px-3 text-[12px] font-medium text-bg-dark transition-colors hover:bg-white/90 disabled:opacity-50"
            onClick={() => onSubmit(trimmed)}
            disabled={busy || trimmed.length === 0}
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {sequenceLabel ? '联合返工这一段' : '重写这一镜'}
          </button>
        </footer>
      </div>
    </div>
  );
}
