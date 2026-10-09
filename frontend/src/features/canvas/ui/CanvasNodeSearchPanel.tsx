// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Crosshair, Search } from 'lucide-react';

import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { useCanvasStore } from '@/stores/canvasStore';
import { useCanvasFollowStore } from './canvasFollowStore';

/**
 * 画布节点搜索（⌘F）。
 *
 * 画布大了以后，「我记得有个叫 XX 的节点」是最常见的找法 —— 靠拖动视口去翻是
 * 不可行的。匹配口径故意只认**节点显示名**：按提示词 / 台词正文搜索是另一件事
 * （那是「内容检索」，代价和预期都不同），混在一起会让这里的结果又慢又杂。
 *
 * 选中即聚焦并选中该节点，与左侧资源面板的「聚焦」走同一条 store 请求。
 */
function matchesQuery(node: CanvasNode, needle: string): boolean {
  if (needle.length === 0) return true;
  const type = node.type;
  if (!type) return false;
  const name = resolveNodeDisplayName(type, node.data ?? {});
  return name.toLowerCase().includes(needle);
}

const MAX_RESULTS = 40;

interface CanvasNodeSearchPanelProps {
  onClose: () => void;
}

export function CanvasNodeSearchPanel({ onClose }: CanvasNodeSearchPanelProps) {
  const { t } = useTranslation();
  const nodes = useCanvasStore((state) => state.nodes);
  const requestFocusNode = useCanvasStore((state) => state.requestFocusNode);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const startFollow = useCanvasFollowStore((state) => state.startFollow);
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = nodes.filter((node) => matchesQuery(node, needle));
    // 上限只是防御：节点数上千时不必把整份列表都塞进 DOM。
    return matched.slice(0, MAX_RESULTS);
  }, [nodes, query]);

  const handlePick = useCallback(
    (nodeId: string) => {
      setSelectedNode(nodeId);
      requestFocusNode(nodeId);
      onClose();
    },
    [onClose, requestFocusNode, setSelectedNode],
  );

  return (
    <div
      className="absolute inset-0 z-[150] flex items-start justify-center bg-black/35 pt-[12vh]"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-[420px] max-w-[92vw] overflow-hidden rounded-[14px] border border-[rgba(255,255,255,0.14)] bg-surface-dark/95 shadow-2xl backdrop-blur"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="relative border-b border-[rgba(255,255,255,0.08)] px-3 py-2.5">
          <Search
            className="pointer-events-none absolute left-[22px] top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/35"
            aria-hidden="true"
          />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (event.key === 'Escape') onClose();
              if (event.key === 'Enter' && results.length > 0) handlePick(results[0].id);
            }}
            placeholder={t('canvas.nodeSearch.placeholder')}
            aria-label={t('canvas.nodeSearch.placeholder')}
            className="canvas-tool-search__input w-full rounded-[9px] py-1.5 pl-7 pr-2 text-[13px] leading-5"
          />
        </div>
        <div className="ui-scrollbar max-h-[46vh] overflow-y-auto p-1.5">
          {results.length === 0 ? (
            <div className="px-2.5 py-3 text-[12px] leading-5 text-white/38">
              {t('canvas.nodeSearch.empty')}
            </div>
          ) : (
            results.map((node) => (
              <div key={node.id} className="group/searchrow flex items-center gap-1">
                <button
                  type="button"
                  className={`${CANVAS_SEARCH_ROW_CLASS} min-w-0 flex-1`}
                  onClick={() => handlePick(node.id)}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {resolveNodeDisplayName(node.type ?? 'textAnnotation', node.data ?? {})}
                  </span>
                </button>
                {/* 跟随：相机持续贴着这个节点，它被拖动或重算时视口跟着走。
                    平时藏起来、hover 才出现 —— 它是个「持续生效」的动作，不该和
                    点一下就聚焦的整行抢注意力。 */}
                <button
                  type="button"
                  aria-label={t('canvas.follow.start')}
                  title={t('canvas.follow.start')}
                  className="mr-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-white/35 opacity-0 transition-opacity hover:bg-white/[0.08] hover:text-white/85 group-hover/searchrow:opacity-100 focus-visible:opacity-100"
                  onClick={() => {
                    startFollow(
                      node.id,
                      resolveNodeDisplayName(node.type ?? 'textAnnotation', node.data ?? {}),
                    );
                    setSelectedNode(node.id);
                    requestFocusNode(node.id);
                    onClose();
                  }}
                >
                  <Crosshair className="h-3.5 w-3.5" />
                </button>
              </div>
            ))          )}
        </div>
        <div className="border-t border-[rgba(255,255,255,0.08)] px-3 py-1.5 text-[11px] text-white/38">
          {t('canvas.nodeSearch.count', { count: nodes.length })}
        </div>
      </div>
    </div>
  );
}

const CANVAS_SEARCH_ROW_CLASS =
  'canvas-tool-row flex min-h-9 w-full items-center gap-3 rounded-[9px] px-2.5 py-1.5 text-left text-[13px] font-medium leading-5';
