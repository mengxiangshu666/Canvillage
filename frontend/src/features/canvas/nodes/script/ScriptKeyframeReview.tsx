// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from 'react';
import { Check, CircleDashed, LocateFixed, X } from 'lucide-react';
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { focusDerivedNodes } from '@/features/canvas/application/focusDerivedNodes';
import { useScriptGraphSlice } from './useScriptGraphSlice';
import { buildScriptRowKeys, scriptRowShotNumber } from './scriptViews';
import {
  currentKeyframeReview,
  referenceImageUrlForNode,
  saveKeyframeReview,
  sha256Url,
  type ScriptKeyframeReviewState,
} from './scriptKeyframeReviewReceipt';

function keyframeNodes(scriptNodeId: string, nodes: CanvasNode[]): CanvasNode[] {
  return nodes.filter(node => node.data.scriptShotKeyframeSourceNodeId === scriptNodeId
    && Boolean(node.data.imageUrl ?? node.data.previewImageUrl));
}

function reviewLabel(node: CanvasNode): string {
  const review = currentKeyframeReview(node);
  if (!review) return '待审';
  return review.state === 'PASS' ? '已通过' : review.state === 'REJECT' ? '已退回' : '未审';
}

export function ScriptKeyframeReview({
  rows,
  scriptNodeId,
}: {
  rows: FreezoneStoryScriptRow[];
  scriptNodeId: string;
}) {
  const graph = useScriptGraphSlice(scriptNodeId);
  const nodes = keyframeNodes(scriptNodeId, graph.nodes);
  const keys = buildScriptRowKeys(rows);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [identity, setIdentity] = useState(false);
  const [requirement, setRequirement] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const selected = nodes.find(node => node.id === selectedId) ?? nodes[0];

  useEffect(() => {
    if (!selected) return;
    const review = currentKeyframeReview(selected);
    setIdentity(review?.identityMatchesReference ?? false);
    setRequirement(review?.matchesShotRequirement ?? false);
    setNote(review?.note ?? '');
    setError('');
  }, [selected?.id, selected?.data.imageUrl]);

  const counts = nodes.reduce(
    (result, node) => {
      const label = reviewLabel(node);
      if (label === '已通过') result.passed += 1;
      else if (label === '已退回') result.rejected += 1;
      else if (label === '未审') result.unreviewed += 1;
      else result.pending += 1;
      return result;
    },
    { passed: 0, rejected: 0, unreviewed: 0, pending: 0 },
  );

  const save = async (state: ScriptKeyframeReviewState) => {
    if (!selected) return;
    setBusy(true);
    setError('');
    try {
      const imageUrl = String(selected.data.imageUrl ?? selected.data.previewImageUrl ?? '');
      const frameSha256 = await sha256Url(resolveImageDisplayUrl(imageUrl));
      const referenceUrl = referenceImageUrlForNode(selected.id, graph);
      const referenceSha256 = referenceUrl
        ? await sha256Url(resolveImageDisplayUrl(referenceUrl))
        : '';
      if (state === 'PASS' && !referenceUrl) {
        setError('这张关键帧没有可核对的参考图；先补参考图，或标记为未审/退回');
        return;
      }
      const reason = saveKeyframeReview(selected, {
        state,
        note,
        identityMatchesReference: identity,
        matchesShotRequirement: requirement,
        frameSha256,
        referenceUrl,
        referenceSha256,
      });
      if (reason) setError(reason);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="nodrag nowheel flex h-full min-w-0 flex-col gap-2 overflow-auto" aria-label="关键帧验收">
      <p className="text-xs text-text-muted">
        {nodes.length} 张关键帧 · {counts.passed} 已通过 · {counts.rejected} 已退回 · {counts.unreviewed + counts.pending} 待审
      </p>
      {!nodes.length && <p className="text-xs text-text-muted">还没有关键帧。先在分镜计划里生成状态画面。</p>}
      {selected && (
        <div className="shrink-0 border-b border-white/10 pb-2">
          <img
            src={resolveImageDisplayUrl(String(selected.data.imageUrl ?? selected.data.previewImageUrl))}
            alt="关键帧"
            className="max-h-64 w-full bg-black object-contain"
          />
          <div className="mt-1 flex items-center gap-2 text-xs">
            <span className="min-w-0 flex-1 break-words">
              {String(selected.data.scriptShotKeyframeRole ?? '关键帧')} · {String(selected.data.scriptShotKeyframeState ?? '')}
            </span>
            <button type="button" aria-label="定位关键帧节点" title="定位关键帧节点" onClick={() => focusDerivedNodes({ nodeIds: [selected.id] })} className="p-1">
              <LocateFixed className="h-4 w-4" />
            </button>
          </div>
          <p className="mt-1 break-words text-xs text-text-muted">
            {String(selected.data.scriptShotKeyframePurpose ?? '')}
          </p>
          <div className="mt-2 flex flex-wrap gap-3 text-xs">
            <label className="flex items-start gap-1">
              <input type="checkbox" checked={identity} onChange={event => setIdentity(event.target.checked)} />
              身份与参考图一致
            </label>
            <label className="flex items-start gap-1">
              <input type="checkbox" checked={requirement} onChange={event => setRequirement(event.target.checked)} />
              符合分镜要求
            </label>
          </div>
          <textarea
            aria-label="关键帧观察或退回原因"
            maxLength={1200}
            value={note}
            onChange={event => setNote(event.target.value)}
            placeholder="写实际观察：脸、服装、领口、站位、光线、道具状态"
            className="mt-2 w-full rounded border border-white/15 bg-black/20 p-2 text-xs"
          />
          {error && <p role="alert" className="mt-1 break-words text-xs text-red-300">{error}</p>}
          <div className="mt-2 flex flex-wrap gap-3 text-xs">
            <button type="button" disabled={busy} onClick={() => void save('PASS')} className="flex items-center gap-1 disabled:opacity-40">
              <Check size={14} />通过
            </button>
            <button type="button" disabled={busy} onClick={() => void save('REJECT')} className="flex items-center gap-1 disabled:opacity-40">
              <X size={14} />退回
            </button>
            <button type="button" disabled={busy} onClick={() => void save('UNREVIEWED')} className="flex items-center gap-1 disabled:opacity-40">
              <CircleDashed size={14} />未审
            </button>
          </div>
        </div>
      )}
      <ol className="divide-y divide-white/10">
        {nodes.map(node => {
          const index = nodes.indexOf(node);
          const rowIndex = keys.indexOf(String(node.data.scriptShotKeyframeRowKey ?? ''));
          const label = rowIndex >= 0 ? scriptRowShotNumber(rows[rowIndex], rowIndex) : index + 1;
          return (
            <li key={node.id} className="flex min-w-0 items-center gap-2 py-2 text-xs">
              <button
                type="button"
                className="h-12 w-16 shrink-0 overflow-hidden rounded border border-white/10"
                onClick={() => setSelectedId(node.id)}
                aria-label={`查看镜 ${label} 关键帧`}
              >
                <img src={resolveImageDisplayUrl(String(node.data.imageUrl ?? node.data.previewImageUrl))} alt="" className="h-full w-full object-cover" />
              </button>
              <span className="min-w-0 flex-1 break-words">
                镜 {label} · {String(node.data.scriptShotKeyframeRole ?? '关键帧')} · {String(node.data.scriptShotKeyframeState ?? '')}
              </span>
              <span className="shrink-0 text-text-muted">{reviewLabel(node)}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
