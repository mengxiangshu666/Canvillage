// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Copy, Image as ImageIcon, Plus, Trash2, Wand2 } from 'lucide-react';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import {
  isRenderableImageSrc,
  resolveImageDisplayUrl,
} from '@/features/canvas/application/imageData';
import { EditableTableCell } from '@/features/canvas/ui/EditableTableCell';
import type { ScriptFieldDef } from './scriptFields';
import { buildScriptRowKeys } from './scriptViews';

type ScriptColumnDef = ScriptFieldDef;

/**
 * 行操作回调：由节点实现、表格只负责触发。
 *
 * 表格不认识 `shot_id`，也不做任何身份推导 —— 复制 / 插行 / 删行 / 重排后的
 * 新行数组由节点侧按稳定身份合同算好，再写回 `scriptResult.rows`。
 */
export interface ScriptRowOperations {
  /** 在当前行下方插入一个新镜头。 */
  onInsertAfter: (rowIndex: number) => void;
  /** 复制当前行；新行拿新身份，不共享原镜头。 */
  onDuplicate: (rowIndex: number) => void;
  /** 上移 / 下移一位；只改片序。 */
  onMove: (rowIndex: number, delta: -1 | 1) => void;
  /** 删除当前行。 */
  onDelete: (rowIndex: number) => void;
  /** 只重写这一镜：其余行原样保留，角色卡与第 7/8 段由服务端回填。 */
  onRewrite?: (rowIndex: number) => void;
  /** 只剩一行时禁用删除，避免整张表被删空。 */
  canDelete: boolean;
}

interface ScriptResultTableProps {
  rows: FreezoneStoryScriptRow[];
  /** 当前可见列（由列显隐决定）；宽度按它现算，收起来就不留空档。 */
  columns: ScriptColumnDef[];
  onCellCommit?: (rowIndex: number, colKey: string, nextValue: string) => void;
  /** 逐镜标记（过期 / 待补），按行键索引 —— 键与 `buildScriptRowKeys` 同一套。 */
  rowFlags?: Map<string, { tone: 'stale' | 'defect'; title: string }>;
  /** 行操作（复制 / 插行 / 删行 / 重排）；不传则整列不渲染。 */
  rowOperations?: ScriptRowOperations;
  /** 生成中禁掉行操作。 */
  rowActionsDisabled?: boolean;
}

// 单格最大高度：内容多的格子封顶，避免整行被一行长文撑到几千像素。
// 滚动由**外层表格容器**统一承担 —— 早先这里还有一层 `overflow-y-auto`，
// 结果是「表格可以滚、每格也能滚」两层滚动条叠在一起（鼠标停在格子里滚不动外层）。
const CELL_MAX_HEIGHT_PX = 180;

// 行操作列：复制 / 插行 / 上移 / 下移 / 删除 / 只改这一镜六个动作排成一行，固定列宽。
// 列宽自适应要把这 132px 算进总宽，否则选出来的列一定比容器宽、必然出现横向滚动。
export const SCRIPT_ROW_ACTIONS_WIDTH_PX = 132;

// 列被压到原宽的 74% 就不再压：再窄中文已经读不成行，宁可横向滚动。
const MIN_COLUMN_SCALE = 0.74;
const MIN_COLUMN_WIDTH_PX = 36;

/**
 * 把当前可见列按容器宽度等比压窄。
 *
 * 800px 的默认节点里，5 个必留列（镜号 / 时长 / 画面描述 / 两条提示词）合计
 * 724px，加上 132px 行操作列就比内容区宽 —— 选列逻辑已经保不住它们，最后一列
 * 会被裁掉。这里按比例压到刚好放下；压到 74% 仍放不下才允许横向滚动。
 */
export function fitColumnWidths(
  widths: readonly number[],
  availableWidthPx: number,
): number[] {
  const total = widths.reduce((sum, width) => sum + width, 0);
  if (availableWidthPx <= 0 || total <= availableWidthPx) return [...widths];
  // 留 2px 给边框/取整误差，避免压完还差一两像素再冒一条滚动条。
  const scale = Math.max(MIN_COLUMN_SCALE, (availableWidthPx - 2) / total);
  return widths.map((width) =>
    Math.max(MIN_COLUMN_WIDTH_PX, Math.floor(width * scale)),
  );
}
const ROW_ACTION_BUTTON_CLASS =
  'nodrag inline-flex h-5 w-5 items-center justify-center rounded-[5px] text-text-muted/80 transition-colors hover:bg-white/[0.1] hover:text-text-dark disabled:pointer-events-none disabled:opacity-30';

export function ScriptResultTable({
  rows,
  columns,
  onCellCommit,
  rowFlags,
  rowOperations,
  rowActionsDisabled,
}: ScriptResultTableProps) {
  // 镜号 / 时长这类短数字列居中、等宽数字，便于扫读。按**当前列集**现算，
  // 列被收起时不会留下「居中的空列」。
  const numericColumnKeys = new Set(
    columns.filter((col) => col.numeric).map((col) => col.key),
  );
  const showRowActions = Boolean(rowOperations);
  const actionsWidth = showRowActions ? SCRIPT_ROW_ACTIONS_WIDTH_PX : 0;
  // 表格容器实测宽度：列数没变但节点被拉窄时也要重新压列，不能只信选列时的估算。
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  useEffect(() => {
    const element = scrollerRef.current;
    if (!element) return;
    const measure = () => setContainerWidth(element.clientWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const fittedWidths = useMemo(
    () =>
      fitColumnWidths(
        columns.map((col) => col.widthPx),
        Math.max(0, containerWidth - actionsWidth),
      ),
    [actionsWidth, columns, containerWidth],
  );
  const tableMinWidth =
    fittedWidths.reduce((sum, width) => sum + width, 0) + actionsWidth;
  // 行键用稳定行键（镜号 / 关键帧序号 / 回落下标），与「生成分镜」写节点时同一套。
  // 用下标当 key 时，删行 / 重排会让 React 复用错位的 DOM，正在编辑的格子里会
  // 留下上一行的文本。
  const rowKeys = useMemo(() => buildScriptRowKeys(rows), [rows]);
  // 「镜号」列的表头在打了标时多一枚圆点：一眼看出这张表里有几行要处理，
  // 而不必去读横幅的措辞。标本身挂在第一个可见单元格的左侧色条上（见下）。
  const flaggedCount = rowFlags ? rowKeys.filter((key) => rowFlags.has(key)).length : 0;
  return (
    <div ref={scrollerRef} className="ui-scrollbar h-full w-full overflow-auto rounded-xl border border-white/[0.09] bg-[#101012]/80 [scrollbar-gutter:stable]">
      <table
        className="border-collapse text-left text-[11.5px] leading-[1.5] text-text-dark" aria-label="脚本分镜表"
        style={{ minWidth: tableMinWidth, tableLayout: 'fixed' }}
      >
        <thead className="sticky top-0 z-10">
          <tr>
            {showRowActions && (
              <th
                style={{ width: SCRIPT_ROW_ACTIONS_WIDTH_PX, minWidth: SCRIPT_ROW_ACTIONS_WIDTH_PX }}
                className="border-b border-r border-b-white/[0.1] border-r-white/[0.05] bg-[#18181A]/98 px-1 py-2 backdrop-blur"
              >
                <span className="sr-only">行操作</span>
              </th>
            )}
            {columns.map((col, columnIndex) => (
              <th
                key={col.key}
                style={{ width: fittedWidths[columnIndex] ?? col.widthPx, minWidth: fittedWidths[columnIndex] ?? col.widthPx }}
                className={`border-b border-r border-b-white/[0.1] border-r-white/[0.05] bg-[#18181A]/98 px-2.5 py-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-text-muted/90 backdrop-blur last:border-r-0 ${
                  numericColumnKeys.has(col.key) ? 'text-center' : ''
                }`}
                title={
                  columnIndex === 0 && flaggedCount > 0
                    ? `这张表里有 ${flaggedCount} 行带标记：脚本变更过的、或缺开拍条件的`
                    : undefined
                }
              >
                {col.label}
                {columnIndex === 0 && flaggedCount > 0 && (
                  <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-300/90 align-middle" />
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const flag = rowFlags?.get(rowKeys[idx] ?? '');
            return (
              <tr
                key={rowKeys[idx] ?? idx}
                className={`group/row align-top transition-colors hover:bg-[#14C9C9]/[0.05] ${
                  idx % 2 === 1 ? 'bg-white/[0.015]' : ''
                }`}
              >
                {showRowActions && rowOperations && (
                  <td
                    style={{ width: SCRIPT_ROW_ACTIONS_WIDTH_PX, minWidth: SCRIPT_ROW_ACTIONS_WIDTH_PX }}
                    className="border-b border-r border-white/[0.05] px-1 py-1.5 align-top"
                  >
                    <div className="flex items-center gap-0.5 opacity-45 transition-opacity focus-within:opacity-100 group-hover/row:opacity-100">
                      <button
                        type="button"
                        title="在下方插入一行"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled}
                        onClick={() => rowOperations.onInsertAfter(idx)}
                      >
                        <Plus className="h-3 w-3" />
                        <span className="sr-only">在下方插入一行</span>
                      </button>
                      <button
                        type="button"
                        title="复制这一行（新镜头，新身份）"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled}
                        onClick={() => rowOperations.onDuplicate(idx)}
                      >
                        <Copy className="h-3 w-3" />
                        <span className="sr-only">复制这一行</span>
                      </button>
                      <button
                        type="button"
                        title="上移一位"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled || idx === 0}
                        onClick={() => rowOperations.onMove(idx, -1)}
                      >
                        <ArrowUp className="h-3 w-3" />
                        <span className="sr-only">上移一位</span>
                      </button>
                      <button
                        type="button"
                        title="下移一位"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled || idx === rows.length - 1}
                        onClick={() => rowOperations.onMove(idx, 1)}
                      >
                        <ArrowDown className="h-3 w-3" />
                        <span className="sr-only">下移一位</span>
                      </button>
                      <button
                        type="button"
                        title="删除这一行"
                        className={ROW_ACTION_BUTTON_CLASS}
                        disabled={rowActionsDisabled || !rowOperations.canDelete}
                        onClick={() => rowOperations.onDelete(idx)}
                      >
                        <Trash2 className="h-3 w-3" />
                        <span className="sr-only">删除这一行</span>
                      </button>
                      {rowOperations.onRewrite && (
                        <button
                          type="button"
                          title="只改这一镜（其余行逐字不动）"
                          className={ROW_ACTION_BUTTON_CLASS}
                          disabled={rowActionsDisabled}
                          onClick={() => rowOperations.onRewrite?.(idx)}
                        >
                          <Wand2 className="h-3 w-3" />
                          <span className="sr-only">只改这一镜</span>
                        </button>
                      )}
                    </div>
                  </td>
                )}
                {columns.map((col, columnIndex) => {
                  const numeric = numericColumnKeys.has(col.key);
                  // 标记画在行首单元格的左边框上：一处色条标整行，不占列宽，
                  // 也不会因为列被收起就消失（只要还有任意一列可见）。
                  const flagOnFirstCell = columnIndex === 0 && Boolean(flag);
                  return (
                    <td
                      key={col.key}
                      style={{ width: fittedWidths[columnIndex] ?? col.widthPx, minWidth: fittedWidths[columnIndex] ?? col.widthPx }}
                      title={flagOnFirstCell ? flag?.title : undefined}
                      className={`border-b border-r border-white/[0.05] px-2.5 py-2 align-top last:border-r-0 ${
                        numeric ? 'text-center tabular-nums text-text-dark/90' : ''
                      } ${
                        flagOnFirstCell
                          ? flag?.tone === 'stale'
                            ? 'border-l-2 border-l-amber-300/80 bg-amber-400/[0.06]'
                            : 'border-l-2 border-l-orange-400/70'
                          : ''
                      }`}
                    >
                      <div className="overflow-hidden" style={{ maxHeight: CELL_MAX_HEIGHT_PX }}>
                        <ScriptResultCell
                          row={row}
                          col={col}
                          onCommit={
                            onCellCommit
                              ? (next) => onCellCommit(idx, col.key, next)
                              : undefined
                          }
                        />
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface ScriptResultCellProps {
  row: FreezoneStoryScriptRow;
  col: ScriptColumnDef;
  onCommit?: (nextValue: string) => void;
}

function ScriptResultCell({ row, col, onCommit }: ScriptResultCellProps) {
  const raw = row[col.key];

  if (col.render === 'image') {
    // 图片 URL 由服务端从当前项目资产中回填；仅渲染可验证的图片来源，防止模型文本
    // 或失效外链冒充参考图。
    const url =
      typeof raw === 'string' && isRenderableImageSrc(raw) ? raw : null;
    if (!url) {
      return (
        <div className="flex h-14 w-14 items-center justify-center rounded border border-dashed border-[rgba(255,255,255,0.14)] text-text-muted/50">
          <ImageIcon className="h-4 w-4" />
        </div>
      );
    }
    return (
      <img
        src={resolveImageDisplayUrl(url)}
        alt=""
        className="h-14 w-14 rounded border border-[rgba(255,255,255,0.08)] object-cover"
        draggable={false}
      />
    );
  }

  const initialText =
    raw == null
      ? ''
      : typeof raw === 'string' || typeof raw === 'number'
        ? String(raw)
        : JSON.stringify(raw);

  if (!onCommit) {
    // 没传 onCommit 视为只读，保留旧渲染。
    if (initialText.length === 0) {
      return <span className="text-text-muted/50">-</span>;
    }
    return (
      <span className="block whitespace-pre-wrap break-words leading-snug">{initialText}</span>
    );
  }

  return <EditableTableCell value={initialText} onCommit={onCommit} />;
}
