import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { ScriptVideoReview, scriptVideoReviewRows } from '@/features/canvas/nodes/script/ScriptVideoReview';
import { scriptRowFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';
import { SCRIPT_SHOT_VIDEO_SOURCE_FIELD, SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD } from '@/features/canvas/nodes/script/scriptShotVideos';
import { VIDEO_REVIEW_FIELD, scriptVideoReviewNotes, withScriptVideoFeedback } from '@/features/canvas/nodes/script/scriptVideoReviewNotes';

const rows = [{ shot_id: 'b', shot_no: '2', duration: '8' }, { shot_id: 'a', shot_no: '1', duration: '4' }, { shot_id: 'new', shot_no: '3' }];
const video = (id: string, shotId: string, index: number, data: Record<string, unknown> = {}): CanvasNode => ({
  id, type: 'videoNode', position: { x: 0, y: 0 },
  data: { scriptShotId: shotId, [SCRIPT_SHOT_VIDEO_SOURCE_FIELD]: 'script',
    [SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD]: scriptRowFingerprint(rows[index], index), ...data },
});
afterEach(cleanup);

describe('script video review', () => {
  it('keeps imported current-version overflow visible and refuses submission', () => {
    const node = video('a1', 'a', 1, { videoUrl: '/a.mp4' });
    node.data[VIDEO_REVIEW_FIELD] = Array.from({ length: 51 }, (_, index) => ({
      issue_id: `a-${index}`, shot_id: 'a', video_node_id: node.id,
      video_url: '/a.mp4', row_fingerprint: node.data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD],
      timestamp_seconds: index, category: 'artifact', description: `问题${index}`, audience_effect: '', repair_direction: '',
    }));
    expect(scriptVideoReviewNotes(node)).toHaveLength(51);
    expect(() => withScriptVideoFeedback({ prompt: '改', currentRows: rows, rewriteShotId: 'a' }, 'script', [node])).toThrow('超过50条');
  });
  it('refuses sequence feedback overflow instead of silently dropping observations', () => {
    const videos = [video('a1', 'a', 1, { videoUrl: '/a.mp4' }), video('b1', 'b', 0, { videoUrl: '/b.mp4' })];
    for (const node of videos) node.data[VIDEO_REVIEW_FIELD] = Array.from({ length: 26 }, (_, index) => ({
      issue_id: `${node.id}-${index}`, shot_id: node.data.scriptShotId, video_node_id: node.id,
      video_url: node.data.videoUrl, row_fingerprint: node.data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD],
      timestamp_seconds: 0, category: 'continuity', description: `问题${index}`, audience_effect: '', repair_direction: '',
    }));
    const payload = { prompt: '改', currentRows: rows, rewriteSequenceId: 'S', directorPlan: { sequences: [{ sequence_id: 'S', shot_nos: [1, 2] }] } };
    expect(() => withScriptVideoFeedback(payload, 'script', videos)).toThrow('超过50条');
    expect(withScriptVideoFeedback({ ...payload, rewriteSequenceId: undefined, rewriteShotId: 'a' }, 'script', videos).videoFeedback).toHaveLength(26);
  });
  it('preserves old-version observations when saving and deleting a current-version note', () => {
    const original = useCanvasStore.getState();
    const node = video('a1', 'a', 1, { videoUrl: '/new.mp4' });
    const old = { issue_id: 'old', shot_id: 'a', video_node_id: 'a1', video_url: '/old.mp4',
      row_fingerprint: node.data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD], timestamp_seconds: 1,
      category: 'artifact', description: '旧版毛发闪烁', audience_effect: '', repair_direction: '' };
    node.data[VIDEO_REVIEW_FIELD] = [old];
    useCanvasStore.setState({ nodes: [node], edges: [] });
    const view = render(<ScriptVideoReview rows={rows} scriptNodeId="script" />);
    fireEvent.change(screen.getByRole('textbox', { name: '看到的问题' }), { target: { value: '新版接触位置错位' } });
    fireEvent.click(screen.getByRole('button', { name: '保存意见' }));
    const live = useCanvasStore.getState().nodes[0];
    expect(live.data[VIDEO_REVIEW_FIELD]).toHaveLength(2);
    const current = scriptVideoReviewNotes(live);
    expect(current.map(note => note.description)).toEqual(['新版接触位置错位']);
    expect(withScriptVideoFeedback({ prompt: '改', currentRows: rows, rewriteShotId: 'a' }, 'script', [live]).videoFeedback?.map(note => note.description)).toEqual(['新版接触位置错位']);
    fireEvent.click(screen.getByRole('button', { name: `删除意见 ${current[0].issue_id}` }));
    expect(useCanvasStore.getState().nodes[0].data[VIDEO_REVIEW_FIELD]).toEqual([old]);
    const restored = { ...live, data: { ...live.data, videoUrl: '/old.mp4', [VIDEO_REVIEW_FIELD]: [old] } };
    expect(scriptVideoReviewNotes(restored).map(note => note.description)).toEqual(['旧版毛发闪烁']);
    fireEvent.click(screen.getByRole('button', { name: '清理旧版本意见' }));
    expect(useCanvasStore.getState().nodes[0].data[VIDEO_REVIEW_FIELD]).toEqual([]);
    view.unmount(); useCanvasStore.setState(original);
  });
  it('records exact playback time on a version, restores notes, deletes and avoids changed video reuse', () => {
    const original = useCanvasStore.getState();
    useCanvasStore.setState({ nodes: [video('a1', 'a', 1, { videoUrl: '/a.mp4' })], edges: [] });
    let view = render(<ScriptVideoReview rows={rows} scriptNodeId="script" onRewrite={vi.fn()} />);
    view.container.querySelector('video')!.currentTime = 2.75;
    fireEvent.change(screen.getByRole('textbox', { name: '看到的问题' }), { target: { value: '毛发纹理逐帧闪烁' } });
    fireEvent.change(screen.getByRole('textbox', { name: '观看感受' }), { target: { value: '注意力被噪点打断' } });
    fireEvent.change(screen.getByRole('textbox', { name: '希望怎么修' }), { target: { value: '保留毛发，稳定纹理' } });
    fireEvent.click(screen.getByRole('button', { name: '保存意见' }));
    const saved = structuredClone(useCanvasStore.getState().nodes);
    const note = scriptVideoReviewNotes(saved[0])[0];
    expect(note).toMatchObject({ shot_id: 'a', video_node_id: 'a1', timestamp_seconds: 2.75, category: 'artifact', description: '毛发纹理逐帧闪烁' });
    view.unmount(); useCanvasStore.setState({ nodes: saved });
    view = render(<ScriptVideoReview rows={rows} scriptNodeId="script" />);
    expect(screen.getByText(/2.75s/)).toBeTruthy();
    const payload = { prompt: '修复', currentRows: rows, rewriteShotId: 'a' };
    expect(withScriptVideoFeedback(payload, 'script', saved).videoFeedback?.[0].repair_direction).toBe('保留毛发，稳定纹理');
    expect(JSON.stringify(withScriptVideoFeedback(payload, 'script', saved).videoFeedback)).not.toContain('/a.mp4');
    fireEvent.click(screen.getByRole('button', { name: `删除意见 ${note.issue_id}` }));
    expect(scriptVideoReviewNotes(useCanvasStore.getState().nodes[0])).toEqual([]);
    const changed = { ...saved[0], data: { ...saved[0].data, videoUrl: '/new.mp4' } };
    expect(scriptVideoReviewNotes(changed)).toEqual([]);
    expect(withScriptVideoFeedback(payload, 'script', [changed])).toBe(payload);
    view.unmount(); useCanvasStore.setState(original);
  });

  it('scopes observations to single/sequence targets and excludes stale, foreign and invalid notes', () => {
    const videos = [video('a1', 'a', 1, { videoUrl: '/a.mp4' }), video('b1', 'b', 0, { videoUrl: '/b.mp4' })];
    for (const node of videos) node.data[VIDEO_REVIEW_FIELD] = [{ issue_id: node.id, shot_id: node.data.scriptShotId,
      video_node_id: node.id, video_url: node.data.videoUrl, row_fingerprint: node.data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD],
      timestamp_seconds: 0, category: 'artifact', description: `问题${node.id}`, audience_effect: '', repair_direction: '' }];
    const single = withScriptVideoFeedback({ prompt: '改', currentRows: rows, rewriteIndex: 1 }, 'script', videos);
    expect(single.videoFeedback?.map(note => note.description)).toEqual(['问题a1']);
    const sequence = { prompt: '改', currentRows: rows, rewriteSequenceId: 'S', directorPlan: { sequences: [{ sequence_id: 'S', shot_nos: [2] }] } };
    expect(withScriptVideoFeedback(sequence, 'script', videos).videoFeedback?.[0].description).toBe('问题b1');
    expect(withScriptVideoFeedback(sequence, 'other', videos)).toBe(sequence);
    const stale = { ...sequence, currentRows: rows.map(row => ({ ...row, visual_description: '新内容' })) };
    expect(withScriptVideoFeedback(stale, 'script', videos)).toBe(stale);
    (videos[0].data[VIDEO_REVIEW_FIELD] as Record<string, unknown>[])[0].timestamp_seconds = NaN;
    expect(scriptVideoReviewNotes(videos[0])).toEqual([]);
  });
  it('keeps current order and versions, excludes removed identities and reflects lifecycle', () => {
    const videos = [video('a1', 'a', 1, { videoUrl: '/a.mp4' }), video('b1', 'b', 0, { generationError: 'provider failed' }),
      video('b2', 'b', 0, { isGenerating: true }), video('old', 'removed', 0, { videoUrl: '/old.mp4' })];
    const items = scriptVideoReviewRows(rows, videos, { nodes: videos, edges: [] });
    expect(items.map(item => item.key)).toEqual(['b', 'a', 'new']);
    expect(items.map(item => item.versions.map(version => version.status))).toEqual([['生成失败', '生成中'], ['已出片'], []]);
    videos[0].data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD] = 'old';
    expect(scriptVideoReviewRows(rows, videos, { nodes: videos, edges: [] })[1].versions[0].status).toBe('脚本已变更');
  });

  it('switches one player, shows failures without media, locates videos and updates after removal', () => {
    const videos = [video('a1', 'a', 1, { videoUrl: '/a.mp4' }), video('b1', 'b', 0, { videoUrl: '/b.mp4' }),
      video('bad', 'b', 0, { generationError: 'provider failed' }), video('old', 'removed', 0, { videoUrl: '/old.mp4' }),
      { ...video('foreign', 'a', 1, { videoUrl: '/foreign.mp4' }), data: { scriptShotId: 'a', [SCRIPT_SHOT_VIDEO_SOURCE_FIELD]: 'other' } }];
    const focus = vi.fn();
    const original = useCanvasStore.getState();
    useCanvasStore.setState({ nodes: videos, edges: [], requestFocusNode: focus });
    const view = render(<ScriptVideoReview rows={rows} scriptNodeId="script" />);
    expect(screen.getByRole('alert').textContent).toBe('provider failed');
    expect(screen.getByText(/1 条已移出/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '播放镜 1 版本 1' }));
    expect(view.container.querySelectorAll('video')).toHaveLength(1);
    expect(view.container.querySelector('video')?.getAttribute('src')).toContain('/a.mp4');
    fireEvent.click(screen.getByRole('button', { name: '定位当前视频节点' }));
    expect(focus).toHaveBeenCalledWith('a1');
    view.rerender(<ScriptVideoReview rows={[rows[0]]} scriptNodeId="script" />);
    expect(view.container.querySelector('video')?.getAttribute('src')).toContain('/b.mp4');
    view.unmount();
    useCanvasStore.setState(original);
  });

  it('targets current row after reorder, exposes intent, and blocks revision while busy', () => {
    const original = useCanvasStore.getState();
    const rewrite = vi.fn();
    useCanvasStore.setState({ nodes: [video('a1', 'a', 1, { videoUrl: '/a.mp4' })], edges: [] });
    const currentRows = rows.map(row => ({ ...row, shot_purpose: '让观众发现犹豫', start_state: '停在边缘' }));
    const view = render(<ScriptVideoReview rows={currentRows} scriptNodeId="script" onRewrite={rewrite} />);
    expect(screen.getByText('观看目的：让观众发现犹豫')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '返工镜 1' }));
    expect(rewrite).toHaveBeenLastCalledWith(1);
    view.rerender(<ScriptVideoReview rows={[currentRows[1], currentRows[0]]} scriptNodeId="script" onRewrite={rewrite} />);
    fireEvent.click(screen.getByRole('button', { name: '返工镜 1' }));
    expect(rewrite).toHaveBeenLastCalledWith(0);
    view.rerender(<ScriptVideoReview rows={currentRows} scriptNodeId="script" onRewrite={rewrite} disabled />);
    fireEvent.click(screen.getByRole('button', { name: '返工镜 1' }));
    expect(rewrite).toHaveBeenCalledTimes(2);
    view.unmount(); useCanvasStore.setState(original);
  });
});
