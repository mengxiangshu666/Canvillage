import { beforeEach, expect, it, vi } from 'vitest';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { submitScriptLocalRewrite } from '@/features/canvas/application/submitScriptLocalRewrite';
import { storyScriptRewriteBody } from '@/api/scriptContract';
import { submitFreezoneStoryScript, fetchFreezoneStoryScriptResult } from '@/api/ops';
import { awaitTaskCompletion, cancelProjectTask, listTasks } from '@/api/tasks';
import { CLEARED_GENERATION_TASK_PATCH } from '@/features/canvas/application/generationTaskArbitration';
import { VIDEO_REVIEW_FIELD } from '@/features/canvas/nodes/script/scriptVideoReviewNotes';
import { scriptRowFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';
import { SCRIPT_SHOT_VIDEO_SOURCE_FIELD, SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD } from '@/features/canvas/nodes/script/scriptShotVideos';

vi.mock('@/api/ops', async original => ({ ...await original<typeof import('@/api/ops')>(),
  submitFreezoneStoryScript: vi.fn(), fetchFreezoneStoryScriptResult: vi.fn() }));
vi.mock('@/api/tasks', async original => ({ ...await original<typeof import('@/api/tasks')>(),
  awaitTaskCompletion: vi.fn(), listTasks: vi.fn(), cancelProjectTask: vi.fn() }));

const ref = { task_key: 'joint', task_type: 'freezone_story_script' as const, job_id: 'joint-job' };
const initial = { title: '原片', rows: [{ shot_id: 'A', shot_no: 1, visual_description: '原镜' }],
  director_plan: { sequences: [{ sequence_id: 'S1', shot_nos: [1] }] } };
const payload = { prompt: '调整节奏', currentRows: initial.rows, directorPlan: initial.director_plan, rewriteSequenceId: 'S1' };
const read = () => useCanvasStore.getState().nodes[0].data as Record<string, unknown>;

beforeEach(() => {
  vi.clearAllMocks();
  useCanvasStore.setState({ nodes: [{ id: 'script', type: CANVAS_NODE_TYPES.script, position: { x: 0, y: 0 },
    data: { scriptResult: structuredClone(initial), scriptDirectorPlanNeedsSync: true } } as CanvasNode], edges: [] });
  vi.mocked(submitFreezoneStoryScript).mockResolvedValue(ref as never);
  vi.mocked(listTasks).mockResolvedValue([{ task_key: ref.task_key } as never]);
  vi.mocked(awaitTaskCompletion).mockResolvedValue({ status: 'completed' } as never);
  vi.mocked(cancelProjectTask).mockResolvedValue({} as never);
  vi.mocked(fetchFreezoneStoryScriptResult).mockResolvedValue({ ...initial, title: '已返工' });
});

it('serializes a separate sequence request and copies the director plan', () => {
  const request = storyScriptRewriteBody(payload);
  expect(request.rewrite_sequence_id).toBe('S1');
  expect(request).not.toHaveProperty('rewrite_index');
  expect(request).not.toHaveProperty('rewrite_shot_id');
  expect(request.director_plan).toEqual(initial.director_plan);
  expect(request.director_plan).not.toBe(initial.director_plan);
});

it('submits a snapshot of version-bound observations through the existing persisted rewrite task', async () => {
  const fingerprint = scriptRowFingerprint(initial.rows[0], 0);
  const note = { issue_id: 'issue', shot_id: 'A', video_node_id: 'video', video_url: '/clip.mp4',
    row_fingerprint: fingerprint, timestamp_seconds: 1.2, category: 'artifact', description: '暗部闪烁', audience_effect: '出戏', repair_direction: '保持材质' };
  useCanvasStore.setState({ nodes: [...useCanvasStore.getState().nodes, { id: 'video', type: CANVAS_NODE_TYPES.video,
    position: { x: 0, y: 0 }, data: { videoUrl: '/clip.mp4', scriptShotId: 'A',
      [SCRIPT_SHOT_VIDEO_SOURCE_FIELD]: 'script', [SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD]: fingerprint,
      [VIDEO_REVIEW_FIELD]: [note] } } as CanvasNode] });
  await submitScriptLocalRewrite('project', 'script', payload);
  const submitted = vi.mocked(submitFreezoneStoryScript).mock.calls[0][1];
  expect(submitted.videoFeedback?.[0]).toMatchObject({ description: '暗部闪烁', timestamp_seconds: 1.2 });
  expect(JSON.stringify(submitted.videoFeedback)).not.toContain('/clip.mp4');
  expect(storyScriptRewriteBody(submitted).video_feedback).toEqual(submitted.videoFeedback);
  expect(submitted.rewriteSequenceId).toBe('S1');
  expect(payload.prompt).toBe('调整节奏');
  expect(useCanvasStore.getState().nodes[1].data[VIDEO_REVIEW_FIELD]).toEqual([note]);
});

it('persists a real job before awaiting and retains whole-plan pending status', async () => {
  vi.mocked(awaitTaskCompletion).mockImplementationOnce(async () => {
    expect(read().generationTaskKey).toBe('joint');
    expect(read().scriptGenerationMode).toBe('sequence');
    expect(read().scriptGenerationInputFingerprint).toEqual(expect.any(String));
    return { status: 'completed' } as never;
  });
  await submitScriptLocalRewrite('project', 'script', payload);
  expect((read().scriptResult as { title: string }).title).toBe('已返工');
  expect(read().scriptDirectorPlanNeedsSync).toBe(true);
  expect(read().generationTaskKey).toBeNull();
  expect(read().scriptGenerationAttemptId).toBeNull();
});

it('cancels a late submission after local cancellation without restoring a job', async () => {
  vi.mocked(submitFreezoneStoryScript).mockImplementationOnce(async () => {
    useCanvasStore.getState().updateNodeData('script', CLEARED_GENERATION_TASK_PATCH);
    return ref as never;
  });
  await submitScriptLocalRewrite('project', 'script', payload);
  expect(cancelProjectTask).toHaveBeenCalledWith({ projectId: 'project', taskType: ref.task_type, scope: ref.job_id });
  expect(awaitTaskCompletion).not.toHaveBeenCalled();
  expect((read().scriptResult as { title: string }).title).toBe('原片');
});

it('rejects results after content changes, preserving edited work', async () => {
  vi.mocked(fetchFreezoneStoryScriptResult).mockImplementationOnce(async () => {
    useCanvasStore.getState().updateNodeData('script', { scriptResult: { ...initial, title: '刚刚编辑' } });
    return { ...initial, title: '旧结果' };
  });
  await expect(submitScriptLocalRewrite('project', 'script', payload)).rejects.toThrow('保留当前内容');
  expect((read().scriptResult as { title: string }).title).toBe('刚刚编辑');
});

it('leaves a newer job untouched when an old submission returns late', async () => {
  vi.mocked(submitFreezoneStoryScript).mockImplementationOnce(async () => {
    useCanvasStore.getState().updateNodeData('script', { isGenerating: true,
      scriptGenerationAttemptId: 'new-attempt', generationTaskKey: 'new-job' });
    return ref as never;
  });
  await submitScriptLocalRewrite('project', 'script', payload);
  expect(read().generationTaskKey).toBe('new-job');
  expect(read().scriptGenerationAttemptId).toBe('new-attempt');
  expect(read().isGenerating).toBe(true);
  expect(cancelProjectTask).toHaveBeenCalledTimes(1);
});

it('retains the original work and clears task state when submission fails', async () => {
  vi.mocked(submitFreezoneStoryScript).mockRejectedValueOnce(new Error('文字服务不可用'));
  await expect(submitScriptLocalRewrite('project', 'script', payload)).rejects.toThrow('文字服务不可用');
  expect(read().isGenerating).toBe(false);
  expect(read().scriptGenerationAttemptId).toBeNull();
  expect((read().scriptResult as { title: string }).title).toBe('原片');
});
