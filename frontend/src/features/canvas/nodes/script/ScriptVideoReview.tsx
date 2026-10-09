import { useRef, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { Download, LocateFixed, Play, RefreshCw } from 'lucide-react';
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { focusDerivedNodes } from '@/features/canvas/application/focusDerivedNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { downloadUrlAsFile } from '@/lib/browserDownload';
import { buildScriptRowKeys, scriptRowShotNumber } from './scriptViews';
import { scriptRowFingerprint } from './scriptRowFingerprint';
import { ScriptVideoFeedback } from './ScriptVideoFeedback';
import { scriptShotFrameChangeReason } from './scriptShotKeyframes';
import { scriptShotVideoNodesInRowOrder, scriptTailReferenceStaleReason, SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD, type CanvasGraphSlice } from './scriptShotVideos';

export function scriptVideoReviewRows(rows: FreezoneStoryScriptRow[], videos: CanvasNode[], graph: CanvasGraphSlice) {
  const keys = buildScriptRowKeys(rows);
  return rows.map((row, index) => ({ row, index, label: scriptRowShotNumber(row, index), key: keys[index],
    versions: videos.filter(video => readScriptShotId(video.data) === keys[index]).map(video => {
      const stale = video.data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD] !== scriptRowFingerprint(row, index)
        || Boolean(scriptTailReferenceStaleReason(video, graph));
      const frameChanged = scriptShotFrameChangeReason(video, graph);
      const status = video.data.isGenerating ? '生成中' : video.data.generationError ? '生成失败'
        : stale ? '脚本已变更' : frameChanged ? '参考图片已变更' : video.data.videoUrl ? '已出片' : '待生成';
      return { video, status, url: typeof video.data.videoUrl === 'string' ? video.data.videoUrl : '' };
    }),
  }));
}

export function ScriptVideoReview({ rows, scriptNodeId, onRewrite, disabled }: {
  rows: FreezoneStoryScriptRow[]; scriptNodeId: string;
  onRewrite?: (rowIndex: number) => void; disabled?: boolean;
}) {
  const graph = useCanvasStore(useShallow(state => ({ nodes: state.nodes, edges: state.edges })));
  const videos = scriptShotVideoNodesInRowOrder(scriptNodeId, graph);
  const items = scriptVideoReviewRows(rows, videos, graph);
  const currentKeys = new Set(items.map(item => item.key));
  const retiredCount = videos.filter(video => !currentKeys.has(readScriptShotId(video.data) ?? '')).length;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const player = useRef<HTMLVideoElement>(null);
  const choices = items.flatMap(item => item.versions.map(version => ({ ...version, label: item.label, row: item.row, rowIndex: item.index })));
  const selected = choices.find(choice => choice.video.id === selectedId) ?? choices.find(choice => choice.url);
  return <section className="nodrag nowheel flex h-full min-w-0 flex-col gap-2 overflow-auto" aria-label="视频素材">
    <p className="text-xs text-text-muted">{items.length} 镜 · {items.filter(item => item.versions.some(version => version.url)).length} 镜有素材{retiredCount ? ` · ${retiredCount} 条已移出片序素材保留在画布` : ''}</p>
    {selected?.url && <div className="shrink-0 border-b border-white/10 pb-2">
      <video ref={player} key={selected.video.id + selected.url} aria-label={`镜 ${String(selected.label)} 视频`} src={resolveImageDisplayUrl(selected.url)} controls playsInline preload="metadata" className="max-h-64 w-full bg-black" style={{ aspectRatio: '16 / 9' }} />
      <div className="mt-1 flex items-center gap-2 text-xs">
        <span className="min-w-0 flex-1 break-words">镜 {String(selected.label)} · {String(selected.status)}</span>
        <button type="button" onClick={() => void downloadUrlAsFile(resolveImageDisplayUrl(selected.url), `shot-${selected.label}.mp4`)} aria-label="下载当前镜头" title="下载当前镜头" className="p-1"><Download className="h-4 w-4" /></button>
        <button type="button" title="定位视频节点" aria-label="定位当前视频节点" onClick={() => focusDerivedNodes({ nodeIds: [selected.video.id] })} className="p-1"><LocateFixed className="h-4 w-4" /></button>
      </div>
      <div className="mt-2 space-y-1 break-words text-xs text-text-muted" aria-label="当前镜头创作意图">
        {(['shot_purpose', 'start_state', 'end_state', 'cut_reason'] as const).map((key, index) => typeof selected.row[key] === 'string' && selected.row[key] && <p key={key}>{['观看目的', '起点', '切点', '切镜理由'][index]}：{String(selected.row[key])}</p>)}
      </div>
      <ScriptVideoFeedback key={`feedback-${selected.video.id}-${selected.url}`} video={selected.video} shotId={buildScriptRowKeys(rows)[selected.rowIndex]} fingerprint={scriptRowFingerprint(selected.row, selected.rowIndex)} currentTime={() => player.current?.currentTime ?? 0} disabled={disabled} onRewrite={onRewrite ? () => onRewrite(selected.rowIndex) : undefined} />
    </div>}
    <ol className="divide-y divide-white/10">
      {items.map(item => <li key={item.key} className="flex min-w-0 flex-col gap-1 py-2 text-xs">
        <div className="flex items-start gap-2"><span className="shrink-0">镜 {String(item.label)} · {String(item.row.duration ?? '')}s</span><span className="min-w-0 flex-1 break-words text-text-muted">{String(item.row.visual_description ?? '')}</span>
          {onRewrite && <button type="button" disabled={disabled} title="只改这一镜" aria-label={`返工镜 ${item.label}`} onClick={() => onRewrite(item.index)} className="flex h-7 w-7 shrink-0 items-center justify-center disabled:opacity-30"><RefreshCw className="h-4 w-4" /></button>}
        </div>
        {!item.versions.length && <span className="text-text-muted">尚无视频节点</span>}
        {item.versions.map((version, index) => <div key={version.video.id} className="flex items-center gap-2">
          <span className="min-w-0 flex-1 break-words">{item.versions.length > 1 ? `版本 ${index + 1} · ` : ''}{String(version.status)}</span>
          <button type="button" disabled={!version.url} title="播放镜头" aria-label={`播放镜 ${item.label} 版本 ${index + 1}`} onClick={() => setSelectedId(version.video.id)} className="p-1 disabled:opacity-30"><Play className="h-4 w-4" /></button>
          <button type="button" title="定位视频节点" aria-label={`定位镜 ${item.label} 版本 ${index + 1}`} onClick={() => focusDerivedNodes({ nodeIds: [version.video.id] })} className="p-1"><LocateFixed className="h-4 w-4" /></button>
        </div>)}
        {item.versions.filter(version => Boolean(version.video.data.generationError)).map(version => <p key={`error-${version.video.id}`} role="alert" className="break-words text-red-300">{String(version.video.data.generationError)}</p>)}
      </li>)}
    </ol>
  </section>;
}
