import type { FreezoneStoryScriptPayload } from '@/api/ops';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import { buildScriptRowKeys } from './scriptViews';
import { scriptRowFingerprint } from './scriptRowFingerprint';
import { SCRIPT_SHOT_VIDEO_SOURCE_FIELD, SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD } from './scriptShotVideos';

export const VIDEO_REVIEW_FIELD = 'scriptVideoReviewNotes';
export const VIDEO_REVIEW_CATEGORIES = {
  artifact: '噪点与纹理闪烁', continuity: '镜头衔接', identity: '角色漂移',
  action: '动作与接触', performance: '表演', composition: '构图',
  prop_state: '道具状态', lighting: '光影', sound: '声音', story_clarity: '故事表达',
} as const;

// Issue fields follow screening_repair; this is an observation, never a QC pass.
export interface ScriptVideoReviewNote {
  issue_id: string; shot_id: string; video_node_id: string; video_url: string;
  row_fingerprint: string; timestamp_seconds: number;
  category: keyof typeof VIDEO_REVIEW_CATEGORIES;
  description: string; audience_effect: string; repair_direction: string;
}

export function scriptVideoReviewNotes(video: CanvasNode): ScriptVideoReviewNote[] {
  const notes = video.data[VIDEO_REVIEW_FIELD];
  if (!Array.isArray(notes)) return [];
  return notes.filter((note): note is ScriptVideoReviewNote => Boolean(note && typeof note === 'object'
    && typeof note.issue_id === 'string' && note.shot_id === readScriptShotId(video.data)
    && note.video_node_id === video.id && note.video_url === video.data.videoUrl
    && typeof note.row_fingerprint === 'string' && note.row_fingerprint.length > 0 && Number.isFinite(note.timestamp_seconds)
    && note.timestamp_seconds >= 0 && Object.prototype.hasOwnProperty.call(VIDEO_REVIEW_CATEGORIES, note.category)
    && typeof note.description === 'string' && note.description.trim()
    && typeof note.audience_effect === 'string' && typeof note.repair_direction === 'string'));
}

/** Snapshot current observations into the typed rewrite request; media URLs stay local. */
export function withScriptVideoFeedback(payload: FreezoneStoryScriptPayload, scriptId: string, videos: CanvasNode[]): FreezoneStoryScriptPayload {
  const rows = payload.currentRows ?? [];
  const keys = buildScriptRowKeys(rows);
  const sequence = payload.directorPlan?.sequences?.find(item => item.sequence_id === payload.rewriteSequenceId);
  const target = rows.map((row, index) => ({ key: keys[index], fingerprint: scriptRowFingerprint(row, index), index, row }))
    .filter(item => payload.rewriteSequenceId ? sequence?.shot_nos?.includes(Number(item.row.shot_no))
      : payload.rewriteShotId ? item.key === payload.rewriteShotId : item.index === payload.rewriteIndex);
  const notes = videos.filter(video => video.data[SCRIPT_SHOT_VIDEO_SOURCE_FIELD] === scriptId)
    .flatMap(scriptVideoReviewNotes)
    .filter(note => target.some(item => item.key === note.shot_id && item.fingerprint === note.row_fingerprint)
      && videos.some(video => video.id === note.video_node_id && video.data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD] === note.row_fingerprint))
    .map(({ video_url: _url, ...note }) => note);
  if (!notes.length) return payload;
  if (notes.length > 50) {
    throw new Error('本次返工有超过50条审看意见，请整理意见或按镜头分批处理。');
  }
  return { ...payload, videoFeedback: notes };
}
