// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useState } from 'react';
import { CheckCircle2, Eye, Loader2, RotateCcw, Scissors, TriangleAlert, X } from 'lucide-react';

import type { ComposeTimelineState } from '@/features/canvas/compose/timelineModel';
import {
  fetchFreezoneVideoCutResult,
  submitFreezoneVideoCut,
} from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import {
  reviewScriptFilmBoundary,
  reviewScriptFilmWhole,
  scriptFilmBoundaries,
  scriptFilmReviewStatus,
  type ScriptFilmBoundary,
  type ScriptFilmReview,
} from './scriptFilmReview';

export interface ScriptFilmReviewPanelProps {
  timeline: ComposeTimelineState;
  review: ScriptFilmReview;
  onChange: (review: ScriptFilmReview) => void;
  onClose: () => void;
  /** 有项目才允许用现有切段接口把接点窗口切出来联看；没有时只显示时间。 */
  project?: string | null;
}

type BoundaryClipState = {
  status: 'idle' | 'cutting' | 'ready' | 'failed';
  leftUrl?: string;
  rightUrl?: string;
  error?: string;
};

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(2)}s`;
}

export function ScriptFilmReviewPanel({
  timeline,
  review,
  onChange,
  onClose,
  project = null,
}: ScriptFilmReviewPanelProps) {
  const boundaries = useMemo(() => scriptFilmBoundaries(timeline), [timeline]);
  const status = useMemo(() => scriptFilmReviewStatus(timeline, review), [review, timeline]);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [boundaryClips, setBoundaryClips] = useState<Record<string, BoundaryClipState>>({});
  const entryFor = (boundaryId: string) =>
    review.boundaries.find((entry) => entry.boundary_id === boundaryId);
  const hasBlockingBoundary = status.reworkCount > 0 || status.staleCount > 0 || status.pendingCount > 0;

  const cutBoundaryWindows = async (boundary: ScriptFilmBoundary) => {
    if (!project) return;
    setBoundaryClips((previous) => ({
      ...previous,
      [boundary.boundary_id]: { status: 'cutting' },
    }));
    try {
      const cutSide = async (side: ScriptFilmBoundary['left']) => {
        const ref = await submitFreezoneVideoCut(project, {
          sourceUrl: side.sourceUrl,
          segments: [{
            index: 0,
            start: side.sourceStartMs / 1000,
            end: side.sourceEndMs / 1000,
          }],
        });
        await awaitTaskCompletion(ref.task_key, project);
        // Task completion and the manifest readback can lag by a tick; the
        // route reports "not yet on disk" until it appears. Poll briefly instead
        // of turning a completed cut into a false failure.
        let result = await fetchFreezoneVideoCutResult(project, ref.job_id);
        for (let attempt = 0; attempt < 12 && !result.segments[0]?.url; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 250));
          result = await fetchFreezoneVideoCutResult(project, ref.job_id);
        }
        const url = result.segments[0]?.url;
        if (!url) throw new Error('切段结果没有返回视频地址');
        return url;
      };
      const [leftUrl, rightUrl] = await Promise.all([
        cutSide(boundary.left),
        cutSide(boundary.right),
      ]);
      setBoundaryClips((previous) => ({
        ...previous,
        [boundary.boundary_id]: { status: 'ready', leftUrl, rightUrl },
      }));
    } catch (error) {
      setBoundaryClips((previous) => ({
        ...previous,
        [boundary.boundary_id]: {
          status: 'failed',
          error: error instanceof Error ? error.message : String(error),
        },
      }));
    }
  };

  return (
    <div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/70 p-6">
      <div className="flex max-h-[88vh] w-[min(860px,94vw)] flex-col gap-4 overflow-hidden rounded-[14px] border border-white/[0.1] bg-[#1c1c1e]/98 p-5 text-text-dark shadow-[0_20px_60px_rgba(0,0,0,0.55)]">
        <header className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[14px] font-medium">
              <Eye className="h-4 w-4 text-text-muted" />
              接点与整片审片
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-text-muted">
              接点窗口来自两侧片段当前实际使用范围，不是脚本计划秒数。素材、裁剪、速度或声音一变，对应认可自动失效。
            </p>
          </div>
          <button type="button" onClick={onClose} className="rounded-full p-1 text-text-muted hover:bg-white/[0.06]">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex flex-wrap items-center gap-2 rounded-[10px] bg-white/[0.03] px-3 py-2 text-[11px] text-text-muted">
          <span>接点 {status.acceptedCount}/{status.boundaryTotal} 已通过</span>
          {status.pendingCount > 0 && <span className="text-amber-200/85">{status.pendingCount} 待看听</span>}
          {status.reworkCount > 0 && <span className="text-red-200/85">{status.reworkCount} 需返工</span>}
          {status.staleCount > 0 && <span className="text-amber-200/85">{status.staleCount} 已过期</span>}
          <span className={status.formalReady ? 'text-emerald-300/90' : 'text-text-muted/80'}>
            {status.formalReady ? '正式成片依据齐全' : '未达到正式成片依据'}
          </span>
        </div>

        {boundaries.length === 0 ? (
          <p className="rounded-[10px] border border-amber-300/20 bg-amber-400/5 p-3 text-[12px] text-amber-100/80">
            时间线还没有两个相邻视频片段。至少接入两镜并排好顺序，才有可联看的接点。
          </p>
        ) : (
          <div className="min-h-0 flex-1 space-y-2 overflow-auto pr-1">
            {boundaries.map((boundary, index) => {
              const entry = entryFor(boundary.boundary_id);
              const current = entry?.signature === boundary.signature;
              const accepted = current && entry?.status === 'accepted';
              const rework = current && entry?.status === 'rework';
              return (
                <section key={boundary.boundary_id} className="rounded-[10px] border border-white/[0.08] p-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-[12px] font-medium">接点 {index + 1} · {boundary.left.label} → {boundary.right.label}</span>
                    <span className={`text-[11px] ${
                      accepted ? 'text-emerald-300/90' : rework ? 'text-red-200/85' : 'text-amber-200/85'
                    }`}>
                      {!entry ? '待看听' : !current ? '记录已过期' : accepted ? '已通过' : rework ? '需返工' : '待看听'}
                    </span>
                  </div>
                  <div className="mt-2 grid gap-1 text-[11px] text-text-muted sm:grid-cols-2">
                    <p>
                      前段尾窗：{seconds(boundary.left.sourceStartMs)}–{seconds(boundary.left.sourceEndMs)}
                      <span className="text-text-muted/60">（速度 {boundary.left.speed}×，{boundary.left.muted ? '静音' : `音量 ${boundary.left.volume}`}）</span>
                    </p>
                    <p>
                      后段头窗：{seconds(boundary.right.sourceStartMs)}–{seconds(boundary.right.sourceEndMs)}
                      <span className="text-text-muted/60">（速度 {boundary.right.speed}×，{boundary.right.muted ? '静音' : `音量 ${boundary.right.volume}`}）</span>
                    </p>
                  </div>
                  <p className="mt-1 text-[10px] leading-relaxed text-text-muted/70">
                    看动作是否重复或缺一截、持物/视线是否跳变；正常速度听尾音、环境声与下一段开头。单帧相同不能代替这段看听。
                  </p>
                  <textarea
                    value={notes[boundary.boundary_id] ?? entry?.note ?? ''}
                    onChange={(event) => setNotes((prev) => ({ ...prev, [boundary.boundary_id]: event.target.value }))}
                    placeholder="记录观察到什么；返工时写清是动作、身份、空间还是声音。"
                    className="mt-2 h-12 w-full resize-none rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-text-dark outline-none placeholder:text-text-muted/50"
                  />
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={!project || boundaryClips[boundary.boundary_id]?.status === 'cutting'}
                      onClick={() => void cutBoundaryWindows(boundary)}
                      className="flex items-center gap-1 rounded-md border border-white/[0.12] px-2.5 py-1 text-[10px] text-text-muted hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45"
                    >
                      {boundaryClips[boundary.boundary_id]?.status === 'cutting'
                        ? <Loader2 className="h-3 w-3 animate-spin" />
                        : <Scissors className="h-3 w-3" />}
                      {boundaryClips[boundary.boundary_id]?.status === 'ready'
                        ? '重新切出接点窗口'
                        : '切出前后窗口联看'}
                    </button>
                    {!project && (
                      <span className="text-[10px] text-text-muted/60">需要项目上下文才能切窗口</span>
                    )}
                    {boundaryClips[boundary.boundary_id]?.status === 'failed' && (
                      <span className="text-[10px] text-red-200/85">{boundaryClips[boundary.boundary_id]?.error}</span>
                    )}
                  </div>
                  {boundaryClips[boundary.boundary_id]?.status === 'ready' && (
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      <div>
                        <p className="mb-1 text-[10px] text-text-muted/70">前段尾窗（实际切出）</p>
                        <video
                          src={resolveImageDisplayUrl(boundaryClips[boundary.boundary_id].leftUrl ?? '')}
                          controls
                          playsInline
                          className="w-full rounded-md bg-black"
                        />
                      </div>
                      <div>
                        <p className="mb-1 text-[10px] text-text-muted/70">后段头窗（实际切出）</p>
                        <video
                          src={resolveImageDisplayUrl(boundaryClips[boundary.boundary_id].rightUrl ?? '')}
                          controls
                          playsInline
                          className="w-full rounded-md bg-black"
                        />
                      </div>
                    </div>
                  )}
                  <div className="mt-2 flex flex-wrap justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => onChange(reviewScriptFilmBoundary(review, boundary, 'rework', {
                        note: notes[boundary.boundary_id] ?? entry?.note,
                      }))}
                      className="flex items-center gap-1 rounded-md border border-red-300/25 px-3 py-1 text-[11px] text-red-100/85 hover:bg-red-500/10"
                    >
                      <RotateCcw className="h-3 w-3" />
                      需返工
                    </button>
                    <button
                      type="button"
                      onClick={() => onChange(reviewScriptFilmBoundary(review, boundary, 'accepted', {
                        note: notes[boundary.boundary_id] ?? entry?.note,
                      }))}
                      className="flex items-center gap-1 rounded-md bg-primary px-3 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90"
                    >
                      <CheckCircle2 className="h-3 w-3" />
                      正常速度看听通过
                    </button>
                  </div>
                </section>
              );
            })}
          </div>
        )}

        <section className="rounded-[10px] border border-white/[0.08] p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-[12px] font-medium">整片正常速度看听</p>
              <p className="mt-0.5 text-[11px] text-text-muted">
                必须基于当前时间线导出的成片文件完整看听；只看时间线预览或静帧不算。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => onChange(reviewScriptFilmWhole(review, timeline, 'rework'))}
                className="rounded-md border border-red-300/25 px-3 py-1.5 text-[11px] text-red-100/85 hover:bg-red-500/10"
              >
                整片需返工
              </button>
              <button
                type="button"
                disabled={!status.technicalOutputCurrent || hasBlockingBoundary || status.wholeFilmStatus === 'rework'}
                onClick={() => onChange(reviewScriptFilmWhole(review, timeline, 'accepted'))}
                className="rounded-md bg-primary px-3 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-45"
              >
                整片看听通过
              </button>
            </div>
          </div>
          {status.wholeFilmStatus === 'rework' && (
            <p className="mt-2 text-[11px] leading-relaxed text-red-200/85">
              整片已标为返工。改完后重新导出，再按当前版本完整看听。
            </p>
          )}
          {!status.technicalOutputCurrent && (
            <p className="mt-2 text-[11px] leading-relaxed text-amber-200/85">
              还没有当前时间线导出的成片。先导出候选文件，再完整看听；导出只完成技术合成，不等于正式认可。
            </p>
          )}
          {status.blockingReasons.length > 0 && (
            <ul className="mt-2 space-y-1 text-[10px] leading-relaxed text-text-muted/75">
              {status.blockingReasons.slice(0, 6).map((reason) => (
                <li key={reason} className="flex items-start gap-1">
                  <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0 text-amber-300/80" />
                  <span>{reason}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
