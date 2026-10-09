// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from "vitest";
import type { FreezoneVideoGenerationRequest } from '@/api/freezoneGenerationHistory';
import requestFixture from '../../../../../tests/fixtures/video_generation_request.json';

import {
  awaitTaskCompletion,
  isTaskMonitoring,
  listTasks,
} from "@/api/tasks";
import {
  nodeNeedsGenerationResume,
  resumeNodeGeneration,
  generationTaskDescriptor,
} from "@/features/canvas/application/resumeGeneration";
import { fetchFreezoneStoryScriptResult, fetchFreezoneJobResultWithRetry } from '@/api/ops';
import { CANVAS_NODE_TYPES, type CanvasNode } from "@/features/canvas/domain/canvasNodes";
import { scriptGenerationInputFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';

vi.mock("@/api/tasks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/tasks")>();
  return {
    ...actual,
    awaitTaskCompletion: vi.fn(),
    isTaskMonitoring: vi.fn(() => false),
    listTasks: vi.fn(),
  };
});
vi.mock('@/api/ops', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/ops')>(),
  fetchFreezoneStoryScriptResult: vi.fn(),
  fetchFreezoneJobResultWithRetry: vi.fn(),
}));

describe("resumeNodeGeneration arbitration", () => {
  it.each(['inline', 'http', 'unknown'] as const)('resumes video with %s provenance without using edited node prompt', async route => {
    const receipt = { schema: 'video_generation_source.v1' as const, task_type: 'freezone_video_gen' as const,
      job_id: 'job-video', output_url: '/original.mp4', execution_prompt_sha256: 'a'.repeat(64),
      generation_request: requestFixture.request as FreezoneVideoGenerationRequest, provider_model: 'original-provider-model' };
    const node = { id: 'video', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: {
      isGenerating: true, generationTaskKey: 'video-task', generationTaskType: 'freezone_video_gen',
      generationTaskJobId: 'job-video', prompt: '后来改过的正文', videoGenerationSource: receipt,
    } } as CanvasNode;
    let data = { ...node.data } as Record<string, unknown>;
    vi.mocked(listTasks).mockResolvedValue([{ task_key: 'video-task' } as never]);
    vi.mocked(awaitTaskCompletion).mockResolvedValue({ status: 'completed', result: route === 'http' ? {} : {
      output_url: '/original.mp4', ...(route === 'inline' ? { video_generation_source: receipt } : {}),
    } } as never);
    vi.mocked(fetchFreezoneJobResultWithRetry).mockResolvedValue({ url: '/original.mp4', size: 10, video_generation_source: receipt });
    await resumeNodeGeneration({ node, projectId: 'project', getNodeData: () => data,
      updateNodeData: (_id, patch) => { data = { ...data, ...patch }; } });
    expect(data.videoUrl).toBe('/original.mp4');
    expect(data.videoGenerationSource).toEqual(route === 'unknown' ? null : receipt);
    expect(data.prompt).toBe('后来改过的正文');
  });
  const scriptNode = (): CanvasNode => ({
    id: 'script', type: CANVAS_NODE_TYPES.script, position: { x: 0, y: 0 },
    data: { isGenerating: true, generationTaskKey: 'script-old', generationTaskType: 'freezone_story_script', generationTaskJobId: 'job-script',
      scriptDirectorPlanNeedsSync: true, scriptResult: { title: '原作品', director_plan: { story_promise: '原方向' }, rows: [{ shot_no: 1, visual_description: '原镜头' }] } },
  } as CanvasNode);

  it.each(['full', 'repair', 'sequence', undefined] as const)('restores script success with mode %s without confusing repair and regeneration', async (mode) => {
    const node = scriptNode();
    const descriptor = generationTaskDescriptor({ task_key: 'script-old', task_type: 'freezone_story_script', job_id: 'job-script' } as never, mode);
    let data = { ...node.data, ...descriptor } as Record<string, unknown>;
    vi.mocked(listTasks).mockResolvedValue([{ task_key: 'script-old' } as never]);
    vi.mocked(awaitTaskCompletion).mockResolvedValue({ status: 'completed' } as never);
    vi.mocked(fetchFreezoneStoryScriptResult).mockResolvedValue({ title: '新作品', rows: [{ shot_no: 1, visual_description: '新镜头' }], director_plan: { story_promise: '新方向' } } as never);
    await resumeNodeGeneration({ node, projectId: 'project-a', getNodeData: () => data,
      updateNodeData: (_id, patch) => { data = { ...data, ...patch }; } });
    expect(data.scriptDirectorPlanNeedsSync).toBe(mode !== 'full');
    expect(data.scriptGenerationMode).toBeNull();
    expect(data.generationTaskKey).toBeNull();
    expect((data.scriptResult as { title: string }).title).toBe('新作品');
  });

  it.each(['failed', 'missing'])('reports resumed script %s while retaining the original work', async (status) => {
    const node = scriptNode();
    vi.mocked(listTasks).mockResolvedValue(status === 'missing' ? [] : [{ task_key: 'script-old' } as never]);
    vi.mocked(awaitTaskCompletion).mockRejectedValue(new Error('文字服务暂不可用'));
    let data = { ...node.data } as Record<string, unknown>;
    const original = data.scriptResult;
    await resumeNodeGeneration({ node, projectId: 'project-a', getNodeData: () => data,
      updateNodeData: (_id, patch) => { data = { ...data, ...patch }; } });
    expect(data.isGenerating).toBe(false);
    expect(data.scriptResult).toBe(original);
    expect(data.scriptDirectorPlanNeedsSync).toBe(true);
    expect(data.generationError).toBe(status === 'missing' ? '生成任务已结束或不存在' : '文字服务暂不可用');
    expect(data.scriptGenerationMode).toBeNull();
  });

  it('does not unlock the current plan when an old full generation succeeds late', async () => {
    const node = scriptNode();
    let latest = { ...node.data, scriptGenerationMode: 'full' } as Record<string, unknown>;
    vi.mocked(listTasks).mockResolvedValue([{ task_key: 'script-old' } as never]);
    vi.mocked(awaitTaskCompletion).mockImplementationOnce(async () => {
      latest = { ...latest, generationTaskKey: 'script-new', scriptGenerationMode: 'repair' };
      return { status: 'completed' } as never;
    });
    vi.mocked(fetchFreezoneStoryScriptResult).mockResolvedValue({ title: '旧结果', rows: [] } as never);
    const updateNodeData = vi.fn();
    await resumeNodeGeneration({ node, projectId: 'project-a', getNodeData: () => latest, updateNodeData });
    expect(updateNodeData).not.toHaveBeenCalled();
  });

  it.each(['rows', 'plan', 'numbering', 'deleted', 'cancelled'] as const)('protects current script after %s changes while result is fetched', async change => {
    const node = scriptNode();
    let data = { ...node.data, scriptGenerationMode: 'sequence',
      scriptGenerationInputFingerprint: scriptGenerationInputFingerprint(node.data as Record<string, unknown>) } as Record<string, unknown>;
    const submitted = { ...node, data: { ...data } } as CanvasNode;
    vi.mocked(listTasks).mockResolvedValue([{ task_key: 'script-old' } as never]);
    vi.mocked(awaitTaskCompletion).mockResolvedValue({ status: 'completed' } as never);
    vi.mocked(fetchFreezoneStoryScriptResult).mockImplementationOnce(async () => {
      const result = data.scriptResult as { rows: Record<string, unknown>[]; director_plan: Record<string, unknown> };
      if (change === 'rows') data = { ...data, scriptResult: { ...result, rows: [{ ...result.rows[0], visual_description: '刚刚修改' }] } };
      if (change === 'numbering') data = { ...data, scriptResult: { ...result, rows: [{ ...result.rows[0], shot_no: 4 }] } };
      if (change === 'plan') data = { ...data, scriptResult: { ...result, director_plan: { story_promise: '刚刚修改' } } };
      if (change === 'cancelled') data = { ...data, isGenerating: false, generationTaskKey: null };
      return { title: '旧结果', rows: [] } as never;
    });
    const update = vi.fn((_id: string, patch: Record<string, unknown>) => { data = { ...data, ...patch }; });
    await resumeNodeGeneration({ node: submitted, projectId: 'project-a',
      getNodeData: () => change === 'deleted' ? undefined : data, updateNodeData: update });
    if (change === 'cancelled' || change === 'deleted') expect(update).not.toHaveBeenCalled();
    else {
      expect(data.generationError).toContain('保留当前内容');
      expect(data.scriptDirectorPlanNeedsSync).toBe(true);
      expect((data.scriptResult as { title: string }).title).toBe('原作品');
      expect(data.scriptGenerationInputFingerprint).toBeNull();
    }
  });

  it('ignores a late script failure after another task becomes current', async () => {
    vi.mocked(listTasks).mockResolvedValue([{ task_key: 'script-old' } as never]);
    const node = scriptNode();
    let latest = node.data as Record<string, unknown>;
    vi.mocked(awaitTaskCompletion).mockImplementationOnce(async () => {
      latest = { ...latest, generationTaskKey: 'script-new', isGenerating: true };
      throw new Error('旧任务失败');
    });
    const updateNodeData = vi.fn();
    await resumeNodeGeneration({ node, projectId: 'project-a', getNodeData: () => latest, updateNodeData });
    expect(updateNodeData).not.toHaveBeenCalled();
  });
  it("only suppresses resume while the task has a live monitor", () => {
    const node = {
      id: "video-node",
      type: CANVAS_NODE_TYPES.video,
      position: { x: 0, y: 0 },
      data: {
        isGenerating: true,
        generationTaskKey: "task-video",
        generationTaskType: "freezone_video_gen",
        generationTaskJobId: "job-video",
      },
    } as CanvasNode;

    vi.mocked(isTaskMonitoring).mockReturnValueOnce(true);
    expect(nodeNeedsGenerationResume(node)).toBe(false);

    vi.mocked(isTaskMonitoring).mockReturnValueOnce(false);
    expect(nodeNeedsGenerationResume(node)).toBe(true);
  });

  it("does not let an older resumed success overwrite the current failed task", async () => {
    vi.mocked(listTasks).mockResolvedValue([
      {
        task_key: "task-old",
        task_type: "freezone_image_gen",
        status: "running",
      } as never,
    ]);
    vi.mocked(awaitTaskCompletion).mockResolvedValue({
      task_key: "task-old",
      task_type: "freezone_image_gen",
      status: "completed",
      result: { output_url: "/outputs/old-success.png" },
    } as never);
    const updateNodeData = vi.fn();
    const node = {
      id: "image-node",
      type: CANVAS_NODE_TYPES.imageGen,
      position: { x: 0, y: 0 },
      data: {
        isGenerating: true,
        generationTaskKey: "task-old",
        generationTaskType: "freezone_image_gen",
        generationTaskJobId: "job-old",
      },
    } as CanvasNode;

    await resumeNodeGeneration({
      node,
      projectId: "project-a",
      updateNodeData,
      getNodeData: () => ({
        isGenerating: false,
        generationTaskKey: "task-new",
        generationError: "current task failed",
      }),
    });

    expect(updateNodeData).not.toHaveBeenCalled();
  });
});
