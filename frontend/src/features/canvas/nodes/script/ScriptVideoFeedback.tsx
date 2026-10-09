import { useState } from 'react';
import { Save, Trash2, RefreshCw } from 'lucide-react';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { VIDEO_REVIEW_CATEGORIES, VIDEO_REVIEW_FIELD, scriptVideoReviewNotes, type ScriptVideoReviewNote } from './scriptVideoReviewNotes';
import { SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD } from './scriptShotVideos';

export function ScriptVideoFeedback({ video, shotId, fingerprint, currentTime, disabled, onRewrite }: {
  video: CanvasNode; shotId: string; fingerprint: string; currentTime: () => number;
  disabled?: boolean; onRewrite?: () => void;
}) {
  const [category, setCategory] = useState<ScriptVideoReviewNote['category']>('artifact');
  const [description, setDescription] = useState('');
  const [effect, setEffect] = useState('');
  const [direction, setDirection] = useState('');
  const [error, setError] = useState('');
  const notes = scriptVideoReviewNotes(video);
  const history = Array.isArray(video.data[VIDEO_REVIEW_FIELD]) ? video.data[VIDEO_REVIEW_FIELD] : [];
  const archivedCount = history.length - notes.length;
  const save = () => {
    const live = useCanvasStore.getState().nodes.find(node => node.id === video.id);
    const time = currentTime();
    if (!live || live.data.videoUrl !== video.data.videoUrl) { setError('视频已更新，请重新审看。'); return; }
    const sourceFingerprint = live.data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD];
    if (typeof sourceFingerprint !== 'string' || !sourceFingerprint) { setError('视频缺少脚本版本记录，暂时不能保存意见。'); return; }
    const existing = scriptVideoReviewNotes(live);
    if (existing.length >= 50) { setError('本版本已有50条意见，请先整理已有记录。'); return; }
    const history = Array.isArray(live.data[VIDEO_REVIEW_FIELD]) ? live.data[VIDEO_REVIEW_FIELD] : [];
    if (history.length >= 500) { setError('本节点已有500条历史意见，请先整理已有记录。'); return; }
    if (!Number.isFinite(time) || time < 0 || !description.trim()) return;
    useCanvasStore.getState().updateNodeData(video.id, { [VIDEO_REVIEW_FIELD]: [...history, {
      issue_id: crypto.randomUUID(), shot_id: shotId, video_node_id: video.id, video_url: String(video.data.videoUrl ?? ''),
      row_fingerprint: sourceFingerprint, timestamp_seconds: time, category,
      description: description.trim(), audience_effect: effect.trim(), repair_direction: direction.trim(),
    } satisfies ScriptVideoReviewNote] });
    setDescription(''); setEffect(''); setDirection(''); setError('');
  };
  return <details className="mt-2 border-t border-white/10 pt-2 text-xs">
    <summary>审看意见 · {notes.length}</summary>
    <div className="mt-2 flex flex-col gap-2">
      {archivedCount > 0 && <button type="button" title={`清理 ${archivedCount} 条旧版本意见`} aria-label="清理旧版本意见" disabled={disabled} className="flex items-center gap-1 self-start p-1 disabled:opacity-30" onClick={() => {
        const live = useCanvasStore.getState().nodes.find(node => node.id === video.id);
        if (live && live.data.videoUrl === video.data.videoUrl) {
          useCanvasStore.getState().updateNodeData(video.id, { [VIDEO_REVIEW_FIELD]: scriptVideoReviewNotes(live) });
          setError('');
        }
      }}><Trash2 className="h-4 w-4" />旧版本意见 · {archivedCount}</button>}
      <label>问题类型<select aria-label="审看问题类型" value={category} disabled={disabled} onChange={event => setCategory(event.target.value as ScriptVideoReviewNote['category'])} className="ml-2 bg-surface-dark">
        {Object.entries(VIDEO_REVIEW_CATEGORIES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label>
      <label>看到的问题<textarea aria-label="看到的问题" maxLength={1600} value={description} disabled={disabled} onChange={event => setDescription(event.target.value)} className="mt-1 h-16 w-full resize-y rounded border border-white/10 bg-black/20 p-2" /></label>
      <label>观看感受<input aria-label="观看感受" maxLength={1200} value={effect} disabled={disabled} onChange={event => setEffect(event.target.value)} className="mt-1 w-full rounded border border-white/10 bg-black/20 p-2" /></label>
      <label>希望怎么修<input aria-label="希望怎么修" maxLength={1200} value={direction} disabled={disabled} onChange={event => setDirection(event.target.value)} className="mt-1 w-full rounded border border-white/10 bg-black/20 p-2" /></label>
      <button type="button" title="保存当前播放时间的审看意见" disabled={disabled || !description.trim()} onClick={save} className="flex items-center gap-1 self-start p-1 disabled:opacity-30"><Save className="h-4 w-4" />保存意见</button>
      {error && <p role="alert">{error}</p>}
      {notes.map(note => <div key={note.issue_id} className="flex items-start gap-2 border-t border-white/10 pt-2">
        <div className="min-w-0 flex-1 break-words"><p>{note.timestamp_seconds.toFixed(2)}s · {VIDEO_REVIEW_CATEGORIES[note.category]}：{note.description}</p>
          {note.audience_effect && <p>观看感受：{note.audience_effect}</p>}{note.repair_direction && <p>修复方向：{note.repair_direction}</p>}
          {note.row_fingerprint !== fingerprint && <p>对应旧脚本版本</p>}
        </div>
        <button type="button" title="删除这条意见" aria-label={`删除意见 ${note.issue_id}`} disabled={disabled} onClick={() => {
          const live = useCanvasStore.getState().nodes.find(node => node.id === video.id);
          const history = live?.data[VIDEO_REVIEW_FIELD];
          if (live && Array.isArray(history)) useCanvasStore.getState().updateNodeData(video.id, { [VIDEO_REVIEW_FIELD]: history.filter(item => !item || typeof item !== 'object' || item.issue_id !== note.issue_id) });
        }} className="p-1"><Trash2 className="h-4 w-4" /></button>
      </div>)}
      {onRewrite && <button type="button" disabled={disabled || !notes.some(note => note.row_fingerprint === fingerprint)} onClick={onRewrite} className="flex items-center gap-1 self-start p-1 disabled:opacity-30"><RefreshCw className="h-4 w-4" />返工这一镜</button>}
    </div>
  </details>;
}
