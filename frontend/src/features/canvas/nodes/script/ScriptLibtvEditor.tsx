// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Check, FileText, MoreHorizontal, Plus, X } from 'lucide-react';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { EditableTableCell } from '@/features/canvas/ui/EditableTableCell';
import { SCRIPT_NODE_Z } from './scriptNodeLayout';
import type { ScriptReadiness } from './scriptReadiness';
import type { ScriptRowOperations } from './ScriptResultTable';

/**
 * LibTV 脚本 V2 全屏编辑器的外观复刻。
 *
 * 只搬**版式与配色**：顶部三阶段（确认镜头 / 准备资产 / 合成提示词）、
 * 无卡片边框的通栏分镜表、底部「添加镜头 / 下一步」。数据与写入仍然走
 * 我们自己的 readiness / assetLedger / onCellCommit，不引入任何外部状态。
 *
 * 与 LibTV 截图的一处刻意差异：左上保留一行很淡的脚本名，否则全屏后
 * 用户没有任何上下文能确认自己打开的是哪个脚本。
 */

interface LibtvColumn {
  key: string;
  label: string;
  /** 百分比宽度；合计 100，列宽随视口伸缩。 */
  width: string;
  align?: 'center';
  /** 正文色调：accent 为 LibTV 的青蓝（画面描述 / 对白）。 */
  tone?: 'accent' | 'muted';
  headTone?: 'pink';
  numeric?: boolean;
}

const LIBTV_COLUMNS: readonly LibtvColumn[] = [
  { key: 'shot_no', label: '镜号', width: '4.5%', align: 'center', numeric: true },
  { key: 'duration', label: '时长', width: '5%', align: 'center', numeric: true },
  { key: 'visual_description', label: '画面描述', width: '25%', tone: 'accent' },
  { key: 'shot', label: '景别', width: '6%' },
  { key: 'lighting_mood', label: '光影氛围', width: '12%' },
  { key: 'dialogue', label: '对白·旁白', width: '15%', tone: 'accent', headTone: 'pink' },
  { key: 'sound', label: '音效', width: '9%' },
  { key: 'video_motion_prompt', label: '运镜', width: '9%' },
  { key: 'shot_prompt', label: '最终提示词', width: '11%' },
];

const OPERATIONS_WIDTH = '3.5%';

const HEADER_CELL =
  'h-10 border-r border-b border-r-[#2b2b2b] border-b-[#2b2b2b] bg-[#1e1e1e] px-3 text-left text-[11px] font-normal tracking-[0.02em] text-[#8c8c8c]';
const BODY_CELL =
  'border-r border-b border-r-[#272727] border-b-[#272727] px-3 py-3.5 align-top text-[12.5px] leading-[1.55]';

function readCellText(row: FreezoneStoryScriptRow, key: string): string {
  const raw = row[key];
  if (raw == null) return '';
  if (typeof raw === 'string' || typeof raw === 'number') return String(raw);
  return JSON.stringify(raw);
}

export interface ScriptLibtvEditorProps {
  open: boolean;
  title: string;
  rows: FreezoneStoryScriptRow[];
  readiness: ScriptReadiness;
  /** 资产台账总数 / 已有可送进生成链的资产图数量（阶段 2 的读数）。 */
  assetTotal: number;
  assetReady: number;
  disabled?: boolean;
  nextLabel: string;
  onClose: () => void;
  onNext: () => void;
  onCellCommit?: (rowIndex: number, colKey: string, nextValue: string) => void;
  rowOperations: ScriptRowOperations;
}

export function ScriptLibtvEditor({
  open,
  title,
  rows,
  readiness,
  assetTotal,
  assetReady,
  disabled,
  nextLabel,
  onClose,
  onNext,
  onCellCommit,
  rowOperations,
}: ScriptLibtvEditorProps) {
  const [menuRow, setMenuRow] = useState<number | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (menuRow !== null) setMenuRow(null);
        else onClose();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, menuRow, onClose]);

  useEffect(() => {
    if (!open) setMenuRow(null);
  }, [open]);

  const compiledCount = useMemo(
    () =>
      rows.filter((row) => readCellText(row, 'shot_prompt').trim().length > 0).length,
    [rows],
  );

  if (!open) return null;

  const shotReady =
    rows.length > 0 && readiness.counts.contractBlockingCount === 0;
  const assetsReady =
    assetTotal === 0 ? rows.length > 0 : assetReady >= assetTotal;
  const promptsReady = rows.length > 0 && compiledCount >= rows.length;
  const stages = [
    {
      key: 'shots',
      title: '确认镜头',
      done: shotReady,
      detail: shotReady
        ? `${rows.length}个镜头已就绪`
        : readiness.counts.contractBlockingCount > 0
          ? `${rows.length}个镜头 · ${readiness.counts.contractBlockingCount} 项硬阻塞`
          : `${rows.length}个镜头待确认`,
    },
    {
      key: 'assets',
      title: '准备资产',
      done: assetsReady,
      detail:
        assetTotal === 0
          ? '没有需要生成的资产'
          : assetReady >= assetTotal
            ? `${assetReady}/${assetTotal} 已生成`
            : `${assetReady}/${assetTotal} 已生成、还差 ${assetTotal - assetReady} 个`,
    },
    {
      key: 'prompts',
      title: '合成提示词',
      done: promptsReady,
      detail: `${compiledCount}/${rows.length} 已合成`,
    },
  ];
  const doneStages = stages.filter((stage) => stage.done).length;
  const activeIndex = (() => {
    const index = stages.findIndex((stage) => !stage.done);
    return index === -1 ? stages.length - 1 : index;
  })();

  return (
    <div
      className="fixed inset-0 flex flex-col bg-[#0b0b0b] text-[#d6d6d6]"
      style={{ zIndex: SCRIPT_NODE_Z.fullscreen }}
      role="dialog"
      aria-label="脚本编辑器"
      onClick={(event) => event.stopPropagation()}
    >
      <header className="relative flex h-[72px] shrink-0 items-center border-b border-[#232323] bg-[#0a0a0a] px-6">
        <div className="hidden min-w-0 max-w-[220px] items-center gap-2 lg:flex">
          <FileText className="h-4 w-4 shrink-0 text-[#6f6f6f]" />
          <span className="truncate text-[12px] text-[#8a8a8a]" title={title}>
            {title}
          </span>
        </div>

        <div className="absolute left-1/2 flex -translate-x-1/2 items-center">
          {stages.map((stage, index) => {
            const active = index === activeIndex;
            return (
              <div key={stage.key} className="flex items-center">
                {index > 0 && (
                  <span className="mx-4 h-px w-[120px] bg-[#343434] xl:w-[170px]" aria-hidden />
                )}
                <div
                  className={`flex items-center gap-2.5 rounded-[10px] px-3 py-1.5 ${
                    active ? 'bg-[#1f1f1f]' : ''
                  }`}
                >
                  <span
                    className={`flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full border text-[12px] font-semibold ${
                      stage.done
                        ? 'border-[#f2f2f2] bg-[#f2f2f2] text-[#111]'
                        : active
                          ? 'border-[#bdbdbd] text-[#f0f0f0]'
                          : 'border-[#5a5a5a] text-[#b9b9b9]'
                    }`}
                  >
                    {stage.done ? <Check className="h-3.5 w-3.5" /> : index + 1}
                  </span>
                  <span className="flex flex-col leading-tight">
                    <span
                      className={`whitespace-nowrap text-[13px] ${
                        stage.done || active ? 'font-semibold text-[#f2f2f2]' : 'text-[#cfcfcf]'
                      }`}
                    >
                      {stage.title}
                    </span>
                    <span className="mt-0.5 whitespace-nowrap text-[10.5px] text-[#8a8a8a]">
                      {stage.detail}
                    </span>
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        <div className="ml-auto flex items-center gap-4 pl-4">
          <span className="hidden whitespace-nowrap text-[11.5px] text-[#b4b4b4] md:inline">
            {doneStages}/3 完成后可批量生视频
          </span>
          <button
            type="button"
            aria-label="关闭脚本编辑器"
            className="flex h-7 w-7 items-center justify-center rounded text-[#b8b8b8] transition-colors hover:bg-white/[0.08] hover:text-white"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-auto px-6 py-6">
        <div className="min-w-[980px] overflow-hidden rounded-[6px] border border-[#2b2b2b] bg-[#171717]">
          <table className="w-full border-collapse" style={{ tableLayout: 'fixed' }}>
            <colgroup>
              {LIBTV_COLUMNS.map((column) => (
                <col key={column.key} style={{ width: column.width }} />
              ))}
              <col style={{ width: OPERATIONS_WIDTH }} />
            </colgroup>
            <thead>
              <tr>
                {LIBTV_COLUMNS.map((column) => (
                  <th
                    key={column.key}
                    scope="col"
                    className={`${HEADER_CELL} ${column.align === 'center' ? 'text-center' : ''} ${
                      column.headTone === 'pink' ? 'text-[#d79ab0]' : ''
                    }`}
                  >
                    {column.label}
                  </th>
                ))}
                <th scope="col" className={`${HEADER_CELL} border-r-0 text-center`}>
                  <span className="sr-only">操作</span>
                  <MoreHorizontal className="mx-auto h-3.5 w-3.5" />
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={readCellText(row, 'shot_id') || index} className="group/librow align-top">
                  {LIBTV_COLUMNS.map((column) => {
                    const text = readCellText(row, column.key);
                    const display =
                      column.key === 'duration' && text.trim()
                        ? `${text.trim()}s`
                        : column.key === 'shot_no' && !text.trim()
                          ? readCellText(row, 'display_shot_no')
                          : text;
                    return (
                      <td
                        key={column.key}
                        className={`${BODY_CELL} ${column.align === 'center' ? 'text-center' : ''} ${
                          column.tone === 'accent' ? 'text-[#67c9df]' : 'text-[#cfcfcf]'
                        } ${column.numeric ? 'tabular-nums' : ''}`}
                        style={{ maxHeight: 140 }}
                      >
                        <div className="max-h-[120px] overflow-hidden">
                          {column.key === 'shot_no' || column.key === 'duration' ? (
                            <span className="whitespace-pre-wrap break-words">{display}</span>
                          ) : onCellCommit ? (
                            <EditableTableCell
                              value={text}
                              emptyPlaceholder={
                                column.key === 'shot_prompt' ? '待生成提示词' : '-'
                              }
                              onCommit={(next) => onCellCommit(index, column.key, next)}
                            />
                          ) : (
                            <span className="whitespace-pre-wrap break-words">{text || '-'}</span>
                          )}
                        </div>
                      </td>
                    );
                  })}
                  <td className={`${BODY_CELL} relative border-r-0 text-center`}>
                    <button
                      type="button"
                      aria-label={`第 ${index + 1} 镜操作`}
                      disabled={disabled}
                      className="inline-flex h-7 w-7 items-center justify-center rounded text-[#c9c9c9] transition-colors hover:bg-white/[0.08] hover:text-white disabled:pointer-events-none disabled:opacity-40"
                      onClick={() => setMenuRow(menuRow === index ? null : index)}
                    >
                      <MoreHorizontal className="h-4 w-4" />
                    </button>
                    {menuRow === index && (
                      <>
                        <button
                          type="button"
                          aria-label="关闭行菜单"
                          className="fixed inset-0 z-10 cursor-default"
                          onClick={() => setMenuRow(null)}
                        />
                        <div
                          ref={menuRef}
                          className="absolute right-2 top-10 z-20 w-40 overflow-hidden rounded-md border border-[#3a3a3a] bg-[#1d1d1d] py-1 text-left shadow-xl"
                        >
                          {(
                            [
                              { label: '在下方插入', run: () => rowOperations.onInsertAfter(index) },
                            { label: '复制这一镜', run: () => rowOperations.onDuplicate(index) },
                            {
                              label: '上移一位',
                              run: () => rowOperations.onMove(index, -1),
                              disabled: index === 0,
                            },
                            {
                              label: '下移一位',
                              run: () => rowOperations.onMove(index, 1),
                              disabled: index === rows.length - 1,
                            },
                            {
                              label: '只改这一镜',
                              run: () => rowOperations.onRewrite?.(index),
                              disabled: !rowOperations.onRewrite,
                              hidden: !rowOperations.onRewrite,
                            },
                              {
                                label: '删除这一镜',
                                run: () => rowOperations.onDelete(index),
                                disabled: !rowOperations.canDelete,
                              },
                            ] as Array<{
                              label: string;
                              run: () => void;
                              disabled?: boolean;
                              hidden?: boolean;
                            }>
                          ).map((item) =>
                            item.hidden ? null : (
                              <button
                                key={item.label}
                                type="button"
                                disabled={disabled || item.disabled}
                                className="flex w-full items-center px-3 py-1.5 text-[12px] text-[#d0d0d0] hover:bg-white/[0.08] disabled:pointer-events-none disabled:opacity-40"
                                onClick={() => {
                                  item.run();
                                  setMenuRow(null);
                                }}
                              >
                                {item.label}
                              </button>
                            ),
                          )}
                        </div>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>

      <footer className="flex h-[60px] shrink-0 items-center justify-between border-t border-[#232323] bg-[#0d0d0d] px-6">
        <button
          type="button"
          disabled={disabled}
          className="inline-flex items-center gap-1.5 text-[12.5px] text-[#e8e8e8] transition-colors hover:text-white disabled:pointer-events-none disabled:opacity-40"
          onClick={() => rowOperations.onInsertAfter(rows.length - 1)}
        >
          <Plus className="h-4 w-4" />
          添加镜头
        </button>
        <button
          type="button"
          disabled={disabled}
          className="inline-flex h-[34px] items-center gap-1.5 rounded-[8px] bg-[#f2f2f2] px-4 text-[12.5px] font-semibold text-[#101010] transition-colors hover:bg-white disabled:pointer-events-none disabled:opacity-45"
          onClick={onNext}
        >
          <ArrowRight className="h-3.5 w-3.5" />
          {nextLabel}
        </button>
      </footer>
    </div>
  );
}
