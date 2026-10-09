import { act, renderHook, waitFor } from "@testing-library/react";
import { createServer } from 'node:http';
import * as api from '@/api/client';
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useVideoGenerationSubmission,
  type VideoGenerationSubmissionContext,
} from "@/features/canvas/nodes/useVideoGenerationSubmission";
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { scatterScriptShotVideos } from '@/features/canvas/nodes/script/scriptShotVideos';
import { ensureScriptVideoAssetReferences } from '@/features/canvas/nodes/script/scriptShotVideoReferences';
import { useVideoReferences } from '@/features/canvas/hooks/useVideoReferences';

const mocks = vi.hoisted(() => ({
  submit: vi.fn(),
  awaitMedia: vi.fn(),
  register: vi.fn(),
  dialog: vi.fn(),
  toast: vi.fn(),
  capture: vi.fn(),
  upload: vi.fn(),
}));

vi.mock("@/api/ops", () => ({
  submitFreezoneVideoGen: mocks.submit,
  submitFreezoneVideoEdit: mocks.submit,
  submitFreezoneVideoI2v: mocks.submit,
  submitFreezoneVideoKeyframes: mocks.submit,
  submitFreezoneVideoOmniGen: mocks.submit,
  uploadFreezoneImage: mocks.upload,
}));
vi.mock('@/features/canvas/nodes/videoNodeModelRules', async importOriginal => ({
  ...await importOriginal<typeof import('@/features/canvas/nodes/videoNodeModelRules')>(),
  captureVideoFrameBlob: mocks.capture,
}));
vi.mock("@/features/canvas/application/awaitFreezoneJobMediaResult", () => ({
  awaitFreezoneJobMediaResult: mocks.awaitMedia,
}));
vi.mock("@/features/canvas/application/useCancelNodeGeneration", () => ({
  registerNodeGenerationTask: mocks.register,
  cancelSubmittedTaskIfAborted: vi.fn(),
}));
vi.mock("@/features/app/errorDialogEvents", () => ({ openGlobalErrorDialog: mocks.dialog }));
vi.mock("sonner", () => ({ toast: { error: mocks.toast, warning: mocks.toast } }));
vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ project: "test-project", canvas: "test-canvas" }),
}));
vi.mock("@/features/canvas/application/generationDependencies", () => ({
  upstreamGenerationGate: () => "go",
  UPSTREAM_GATE_RETRY_MS: 100,
}));

function setup(count: 1 | 2 = 1, overrides: Partial<VideoGenerationSubmissionContext> = {}) {
  const updateNodeData = vi.fn();
  const context: VideoGenerationSubmissionContext = {
    id: "video-node",
    data: {} as VideoGenerationSubmissionContext["data"],
    t: ((key: string) => key) as VideoGenerationSubmissionContext["t"],
    updateNodeData,
    submittingRef: { current: false },
    generationQueueAbortRef: { current: null },
    setAutoSubmitRetryTick: vi.fn(),
    autoSubmitRetryTick: 0,
    isGenerating: false,
    submitDisabled: false,
    capabilityVideoModel: null,
    selectedVideoModel: null,
    videoChannelEnabled: true,
    videoChannelDisabledReason: null,
    quality: "720p" as VideoGenerationSubmissionContext["quality"],
    effectiveAspectRatio: "16:9",
    durationParameterEnabled: false,
    durationSec: 5,
    durationBounds: { min: 1, max: 15 },
    durationOptions: undefined,
    generateAudio: false,
    prompt: "A moving subject",
    upstreamTextJoined: "",
    genMode: "textToVideo",
    referenceMedia: [],
    isDirectVideoModel: false,
    modelId: "test-model",
    selectedVideoModelId: "test-model",
    isSeedance20Model: false,
    sceneOptimize: undefined,
    isHappyHorseModel: false,
    isKacangKlingV2vModel: false,
    autoTailFrameReference: false,
    videoModelFamily: "generic" as VideoGenerationSubmissionContext["videoModelFamily"],
    count,
    refreshHistory: vi.fn(),
    ...overrides,
  };
  const hook = renderHook(() => useVideoGenerationSubmission(context));
  return { hook, context, updateNodeData };
}

describe("video local observation cancellation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useCanvasStore.getState().setCanvasData([], []);
    mocks.submit.mockResolvedValue({ job_id: "job", task_key: "task", task_type: "freezone_video_gen" });
  });

  it.each([
    { limit: 1, count: 2, kling: false },
    { limit: 0, count: 1, kling: false },
    { limit: undefined, count: 6, kling: false },
    { limit: undefined, count: 10, kling: true },
  ])('refuses video-edit reference truncation before any submission ($limit/$count/$kling)', async ({ limit, count, kling }) => {
    const nodes = [
      { id: 'source', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { videoUrl: '/source.mp4' } },
      ...Array.from({ length: count }, (_, i) => ({ id: `image-${i}`, type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: `/image-${i}.png`, referenceRole: i === count - 1 ? 'style' : 'motion' } })),
      { id: 'video-node', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { videoUrl: '/existing.mp4', generationBatch: ['/existing.mp4'] } },
    ];
    useCanvasStore.getState().setCanvasData(nodes as never,
      nodes.slice(0, -1).map(node => ({ id: `${node.id}-edge`, source: node.id, target: 'video-node' })) as never);
    mocks.awaitMedia.mockResolvedValue({ url: '/new.mp4' });
    const submission = setup(1, {
      genMode: 'videoEdit', isKacangKlingV2vModel: kling,
      capabilityVideoModel: limit === undefined ? null : { referenceLimits: { videoEdit: { image: limit } } } as never,
    });
    await act(async () => { await submission.hook.result.current(); });
    expect(mocks.submit).not.toHaveBeenCalled();
    expect(mocks.awaitMedia).not.toHaveBeenCalled();
    expect(mocks.register).not.toHaveBeenCalled();
    expect(mocks.dialog).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining('参考图') }));
    expect(submission.updateNodeData).toHaveBeenCalledWith('video-node', expect.objectContaining({ isGenerating: false, generationStartedAt: null }));
    expect(submission.updateNodeData.mock.calls.every(([, patch]) => !('videoUrl' in patch) && !('generationBatch' in patch) && !('videoGenerationSource' in patch))).toBe(true);
    expect(submission.context.submittingRef.current).toBe(false);
    const keep = limit ?? (kling ? 9 : 5);
    const retained = nodes.filter(node => !node.id.startsWith('image-') || Number(node.id.slice(6)) < keep);
    useCanvasStore.getState().setCanvasData(retained as never,
      retained.slice(0, -1).map(node => ({ id: `${node.id}-edge`, source: node.id, target: 'video-node' })) as never);
    await act(async () => { await submission.hook.result.current(); });
    expect(mocks.submit).toHaveBeenCalledOnce();
    expect(mocks.submit.mock.calls[0][1].imageUrls).toHaveLength(keep);
    submission.hook.unmount();
  });

  it('keeps every ordered video-edit reference and its style/motion role at the model limit', async () => {
    const nodes = [
      { id: 'motion', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/motion.png', referenceRole: 'motion' } },
      { id: 'style', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/style.png', referenceRole: 'style' } },
      ...Array.from({ length: 4 }, (_, i) => ({ id: `extra-${i}`, type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: `/extra-${i}.png` } })),
      { id: 'source', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { videoUrl: '/source.mp4' } },
      { id: 'video-node', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { referenceOrder: ['source', 'motion', 'extra-0', 'extra-1', 'extra-2', 'extra-3', 'style'] } },
    ];
    useCanvasStore.getState().setCanvasData(nodes as never,
      nodes.slice(0, -1).map(node => ({ id: `${node.id}-edge`, source: node.id, target: 'video-node' })) as never);
    const received: Array<{ path: string; body: Record<string, unknown> }> = [];
    const server = createServer(async (request, response) => {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      received.push({ path: request.url ?? '', body: JSON.parse(Buffer.concat(chunks).toString()) });
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({ ok: true, data: { job_id: 'http-job', task_key: 'http-task', task_type: 'freezone_video_gen' } }));
    });
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    const address = server.address() as import('node:net').AddressInfo;
    const transport = vi.spyOn(api, 'apiCall').mockImplementation(async (path, options) => {
      const envelope = await api.apiClient(path, { ...options, prefix: `http://127.0.0.1:${address.port}/api/v1` }).json<api.ApiEnvelope<never>>();
      return envelope.data as never;
    });
    const realOps = await vi.importActual<typeof import('@/api/ops')>('@/api/ops');
    mocks.submit.mockImplementation(realOps.submitFreezoneVideoEdit);
    mocks.awaitMedia.mockResolvedValue({ url: '/new.mp4' });
    const submission = setup(1, { genMode: 'videoEdit', capabilityVideoModel: { referenceLimits: { videoEdit: { image: 6 } } } as never });
    try {
      await act(async () => { await submission.hook.result.current(); });
      expect(received).toHaveLength(1);
      expect(received[0].path).toBe('/api/v1/projects/test-project/freezone/video/video-edit');
      expect(received[0].body.video_url).toBe('/source.mp4');
      expect(received[0].body.image_urls).toEqual(['/motion.png', '/extra-0.png', '/extra-1.png', '/extra-2.png', '/extra-3.png', '/style.png']);
      expect(received[0].body.prompt).toContain('图片6 是风格参考');
      expect(received[0].body.prompt).toContain('图片1 是动作/运镜参考');
    } finally {
      submission.hook.unmount();
      transport.mockRestore();
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  });

  it('stores each completed batch receipt with its own URL and clears an unknown new source', async () => {
    mocks.submit.mockResolvedValueOnce({ job_id: 'job-1', task_key: 'task-1', task_type: 'freezone_video_gen' })
      .mockResolvedValueOnce({ job_id: 'job-2', task_key: 'task-2', task_type: 'freezone_video_gen' });
    mocks.awaitMedia.mockImplementation(async (_project, ref) => {
      const url = `/${ref.job_id}.mp4`;
      return { url, result: { video_generation_source: { schema: 'video_generation_source.v1',
        task_type: 'freezone_video_gen', job_id: ref.job_id, output_url: url, execution_prompt_sha256: 'a'.repeat(64) } } };
    });
    const batch = setup(2);
    await act(async () => { await batch.hook.result.current(); });
    const patches = batch.updateNodeData.mock.calls.map(([, patch]) => patch);
    const main = patches.find(patch => patch.videoUrl);
    expect(main.videoGenerationSource.output_url).toBe(main.videoUrl);
    const final = patches.filter(patch => patch.generationBatchSources).slice(-1)[0];
    expect(Object.keys(final.generationBatchSources).sort()).toEqual(['/job-1.mp4', '/job-2.mp4']);
    mocks.awaitMedia.mockResolvedValue({ url: '/legacy.mp4' });
    const legacy = setup();
    await act(async () => { await legacy.hook.result.current(); });
    expect(legacy.updateNodeData).toHaveBeenCalledWith('video-node', expect.objectContaining({ videoUrl: '/legacy.mp4', videoGenerationSource: null }));
  });

  it('submits the displayed ordered asset references with matching prompt numbers and roles', async () => {
    useCanvasStore.getState().setCanvasData([
      { id: 'frame', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/frame.png', displayName: '本镜' } },
      { id: 'video-node', type: CANVAS_NODE_TYPES.video, position: { x: 400, y: 0 }, data: { prompt: '滑板向前运动' } },
    ] as never, [{ id: 'frame-edge', source: 'frame', target: 'video-node', data: { role: 'scriptShotVideo', label: '首帧' } }] as never);
    const capabilities = { supportedModes: ['allReference'], referenceLimits: { allReference: { image: 4 } } };
    ensureScriptVideoAssetReferences({ scriptNodeId: 'script', videoNodeId: 'video-node', firstFrameNodeId: 'frame', model: capabilities,
      references: (['character', 'scene', 'prop'] as const).map((role, index) => ({ assetId: role, role, roleLabel: ['角色', '场景', '道具'][index], name: ['阿波', '桥', '滑板'][index], imageUrl: `/${role}.png`, assetRevision: 1, assetContentHash: 'hash', identityLocks: ['结构'], dependencies: [] })) });
    const video = useCanvasStore.getState().nodes.find(node => node.id === 'video-node')!;
    const refs = renderHook(() => useVideoReferences({ nodeId: video.id, prompt: String(video.data.prompt), referenceOrder: video.data.referenceOrder as string[], genMode: 'allReference', capsByMode: {}, referenceLimits: capabilities.referenceLimits, updatePrompt: vi.fn() }));
    expect(refs.result.current.referenceMedia.map(item => item.role)).toEqual(['first_frame', 'identity', 'scene', 'prop']);
    mocks.awaitMedia.mockResolvedValue({ url: '/result.mp4' });
    const submission = setup(1, { data: video.data as never, prompt: String(video.data.prompt), genMode: 'allReference', referenceMedia: refs.result.current.referenceMedia, capabilityVideoModel: capabilities as never, selectedVideoModel: capabilities as never });
    await act(async () => { await submission.hook.result.current(); });
    const request = mocks.submit.mock.calls[0][1];
    expect(request.references.map((reference: { url: string }) => reference.url)).toEqual(['/frame.png', '/character.png', '/scene.png', '/prop.png']);
    expect(request.prompt).toContain('角色阿波引用@图片2');
    expect(request.prompt).toContain('图片2 是角色身份锚点（角色 阿波）');
    expect(request.prompt).toContain('道具滑板引用@图片4');
    expect(request.firstFrameUrl).toBeUndefined();
    submission.hook.unmount();
    refs.unmount();
  });

  it('does not remap an already recompiled asset block a second time during rearm', () => {
    const initialPrompt = '手动@图片2 [视频资产引用：角色阿波引用@图片2；道具滑板引用@图片3]';
    useCanvasStore.getState().setCanvasData(['frame', 'character', 'prop'].map(id => ({ id, type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: `/${id}.png` } })).concat([
      { id: 'video-node', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { prompt: initialPrompt, scriptVideoAssetReferenceOrder: ['frame', 'character', 'prop'] } } as never,
    ]) as never, ['frame', 'character', 'prop'].map(id => ({ id: `${id}-edge`, source: id, target: 'video-node' })) as never);
    const updatePrompt = vi.fn();
    const refs = renderHook(({ prompt, order }) => useVideoReferences({ nodeId: 'video-node', prompt, referenceOrder: order, genMode: 'allReference', capsByMode: {}, updatePrompt }), { initialProps: { prompt: initialPrompt, order: ['frame', 'character', 'prop'] } });
    act(() => { useCanvasStore.getState().updateNodeData('video-node', { scriptVideoAssetReferenceOrder: ['frame', 'prop', 'character'] }); });
    const updated = '手动@图片2 [视频资产引用：角色阿波引用@图片3；道具滑板引用@图片2]';
    refs.rerender({ prompt: updated, order: ['frame', 'prop', 'character'] });
    expect(updatePrompt).toHaveBeenLastCalledWith('手动@图片3 [视频资产引用：角色阿波引用@图片3；道具滑板引用@图片2]');
    refs.unmount();
  });

  it('records converted video tails in actual image order even with a stale UI snapshot', async () => {
    const facts = { shotId: 'S1', creativeHandoff: { cutReason: '切向落点', referenceResponsibilities: [
      { scope: 'storyboard', imageNumber: 1, role: 'character', name: '阿波', responsibility: '锁定身份', prohibited: '不覆盖状态' },
    ] } };
    const data = { scriptShotSourceNodeId: 'script', genMode: 'allReference', referenceOrder: ['opening', 'character', 'tail', 'state'], shotContractFacts: facts };
    useCanvasStore.getState().setCanvasData([
      { id: 'tail', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { videoUrl: '/previous.mp4', displayName: '上一镜' } },
      ...['opening', 'state', 'character'].map(id => ({ id, type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: `/${id}.png`, displayName: id, ...(id === 'state' ? { scriptShotKeyframeState: '脚掌落稳' } : {}) } })),
      { id: 'video-node', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { ...data, referenceOrder: ['tail', 'opening', 'state', 'character'] } },
    ] as never, [
      { id: 'tail-edge', source: 'tail', target: 'video-node', data: { role: 'scriptShotContinuity' } },
      { id: 'opening-edge', source: 'opening', target: 'video-node', data: { role: 'scriptShotVideo' } },
      { id: 'state-edge', source: 'state', target: 'video-node', data: { role: 'scriptShotKeyframe' } },
      { id: 'character-edge', source: 'character', target: 'video-node', data: { role: 'scriptShotAssetReference', label: '角色 阿波' } },
    ] as never);
    mocks.capture.mockResolvedValue(new Blob(['tail'], { type: 'image/png' }));
    mocks.upload.mockResolvedValue({ url: '/captured-tail.png' });
    mocks.awaitMedia.mockResolvedValue({ url: '/result.mp4' });
    const submission = setup(1, { data: data as never, prompt: '连续滑行 [视频参考用途：旧角色引用@图片2]', genMode: 'allReference', autoTailFrameReference: true,
      referenceMedia: [{ kind: 'image', nodeId: 'deleted', imageUrl: '/deleted.png', role: 'prop', displayName: '已删除' }] });
    await act(async () => { await submission.hook.result.current(); });
    const request = mocks.submit.mock.calls[0][1];
    expect(request.references.map((item: { url: string }) => item.url)).toEqual(['/captured-tail.png', '/opening.png', '/state.png', '/character.png']);
    expect(request.prompt).toContain('@图片1是上一镜实际尾帧');
    expect(request.prompt).toContain('@图片2是本镜起始画面参考');
    expect(request.prompt).toContain('@图片3是本镜状态关键帧');
    expect(request.prompt).toContain('角色阿波引用@图片4');
    expect(request.prompt).toContain('图片4 是角色身份锚点');
    expect(request.prompt).not.toMatch(/旧角色|已删除|视频1/);
    const saved = submission.updateNodeData.mock.calls.find(([, patch]) => patch.shotContractFacts)?.[1].shotContractFacts;
    expect(saved.creativeHandoff.cutReason).toBe('切向落点');
    expect(saved.creativeHandoff.referenceResponsibilities.map((item: { scope: string; imageNumber: number; role: string }) => [item.scope, item.imageNumber, item.role])).toEqual([
      ['storyboard', 1, 'character'], ['video', 1, 'continuity_frame'], ['video', 2, 'opening_frame'], ['video', 3, 'state_frame'], ['video', 4, 'character'],
    ]);
    expect(submission.updateNodeData.mock.invocationCallOrder[submission.updateNodeData.mock.calls.findIndex(([, patch]) => patch.shotContractFacts)]).toBeLessThan(mocks.submit.mock.invocationCallOrder[0]);
    submission.hook.unmount();
  });

  it('refreshes stale script reference numbers when a state frame completes and submits its purpose', async () => {
    useCanvasStore.getState().setCanvasData([
      { id: 'frame', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/frame.png' } },
      { id: 'state', type: CANVAS_NODE_TYPES.imageGen, position: { x: 0, y: 0 }, data: { referenceImageUrl: '/frame.png', scriptShotKeyframeRowKey: 'shot:1', scriptShotKeyframeState: '右拳碰到左臂', scriptShotKeyframePurpose: '锁定接触点' } },
      { id: 'character', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/character.png' } },
      { id: 'video-node', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { scriptShotSourceNodeId: 'script', referenceOrder: ['frame', 'state', 'character'], shotContractFacts: { shotId: 'S1' }, prompt: '连续动作\n[视频参考用途：状态画面0引用@图片0[旧状态[接触]]；角色阿波引用@图片2]' } },
    ] as never, [
      { id: 'first', source: 'frame', target: 'video-node', data: { role: 'scriptShotVideo' } },
      { id: 'state-edge', source: 'state', target: 'video-node', data: { role: 'scriptShotKeyframe' } },
      { id: 'asset-edge', source: 'character', target: 'video-node', data: { role: 'scriptShotAssetReference', label: '角色 阿波' } },
    ] as never);
    const refs = renderHook(() => {
      const data = useCanvasStore(state => state.nodes.find(node => node.id === 'video-node')!.data);
      return useVideoReferences({ nodeId: 'video-node', prompt: String(data.prompt), referenceOrder: data.referenceOrder as string[], genMode: 'allReference', capsByMode: {}, updatePrompt: next => useCanvasStore.getState().updateNodeData('video-node', { prompt: next }) });
    });
    const videoData = () => useCanvasStore.getState().nodes.find(node => node.id === 'video-node')!.data;
    expect(refs.result.current.referenceMedia.map(item => item.nodeId)).toEqual(['frame', 'character']);
    expect(videoData().prompt).not.toContain('@图片0');
    expect(videoData().prompt).not.toContain('旧状态');
    const referenceRecords = () => (videoData().shotContractFacts as VideoGenerationSubmissionContext['data']['shotContractFacts'])?.creativeHandoff?.referenceResponsibilities;
    expect(referenceRecords()?.map(item => [item.imageNumber, item.sourceNodeId])).toEqual([[1, 'frame'], [2, 'character']]);
    act(() => { useCanvasStore.getState().updateNodeData('state', { imageUrl: '/contact.png' }); });
    expect(refs.result.current.referenceMedia.map(item => item.nodeId)).toEqual(['frame', 'state', 'character']);
    expect(videoData().prompt).toContain('@图片2是本镜状态关键帧');
    expect(videoData().prompt).toContain('锁定接触点');
    expect(videoData().prompt).toContain('角色阿波引用@图片3');
    expect(referenceRecords()?.map(item => [item.imageNumber, item.sourceNodeId])).toEqual([[1, 'frame'], [2, 'state'], [3, 'character']]);
    mocks.awaitMedia.mockResolvedValue({ url: '/result.mp4' });
    const submission = setup(1, { data: videoData() as never, prompt: '连续动作 [视频参考用途：引用@图片0]', genMode: 'allReference', referenceMedia: refs.result.current.referenceMedia });
    await act(async () => { await submission.hook.result.current(); });
    const request = mocks.submit.mock.calls[0][1];
    expect(request.references.map((item: { url: string }) => item.url)).toEqual(['/frame.png', '/contact.png', '/character.png']);
    expect(request.prompt).toContain('@图片2是本镜状态关键帧');
    expect(request.prompt).toContain('右拳碰到左臂');
    expect(request.prompt).not.toContain('@图片0');
    expect(request.prompt.startsWith('[视频参考用途：')).toBe(true);
    expect(request.prompt).toContain('连续动作');
    expect(request.prompt).not.toContain('旧状态');
    submission.hook.unmount();
    refs.unmount();
  });

  it('submits an image-free script T2V draft with full design and motion, and stops after an image is connected', async () => {
    useCanvasStore.getState().setCanvasData([{
      id: 'script', type: CANVAS_NODE_TYPES.script, position: { x: 0, y: 0 },
      data: { scriptResult: { title: 'Rain', rows: [{ shot_no: '1', duration: '5', generation_mode: 'text_to_video', start_state: '雨水沿门环边缘流下', end_state: '雨滴落下', shot_prompt: '剪纸动画，青绿色门环，左侧晨光', video_motion_prompt: '固定镜头，雨滴沿门环滑落' }] } },
    }] as never, []);
    const derived = scatterScriptShotVideos({ scriptNodeId: 'script', model: 'test-model', generateVideos: false });
    expect(derived.ok).toBe(true);
    if (!derived.ok) return;
    const source = useCanvasStore.getState().nodes.find(node => node.id === derived.nodeIds[0])!;
    const data = source.data as VideoGenerationSubmissionContext['data'];
    mocks.awaitMedia.mockResolvedValue({ url: '/result.mp4' });
    const valid = setup(1, { id: source.id, data, prompt: String(data.prompt), genMode: 'textToVideo', durationParameterEnabled: true, durationSec: 5, durationOptions: [5] });
    await act(async () => { await valid.hook.result.current(); });
    expect(mocks.submit).toHaveBeenCalledWith('test-project', expect.objectContaining({
      genMode: 'textToVideo', durationSeconds: 5, prompt: expect.stringContaining('剪纸动画，青绿色门环，左侧晨光'),
    }));
    const request = mocks.submit.mock.calls[0][1];
    expect(request.prompt).toContain('固定镜头，雨滴沿门环滑落');
    expect(request.prompt).toContain('纹理爬动');
    expect(request.firstFrameUrl).toBeUndefined();
    expect(request.referenceImageUrls).toBeUndefined();
    expect(valid.updateNodeData).toHaveBeenCalledWith(source.id, expect.objectContaining({ videoUrl: '/result.mp4' }));
    valid.hook.unmount();
    const state = useCanvasStore.getState();
    state.setCanvasData([...state.nodes, { id: 'image', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/image.png' } }] as never,
      [{ id: 'manual', source: 'image', target: source.id }] as never);
    mocks.submit.mockClear();
    const invalid = setup(1, { id: source.id, data, prompt: String(data.prompt), genMode: 'textToVideo', durationParameterEnabled: true, durationSec: 5, durationOptions: [5] });
    await act(async () => { await invalid.hook.result.current(); });
    expect(mocks.submit).not.toHaveBeenCalled();
    expect(invalid.updateNodeData).toHaveBeenCalledWith(source.id, expect.objectContaining({ generationErrorCode: 'SCRIPT_TEXT_VIDEO_HAS_IMAGES' }));
    expect(useCanvasStore.getState().edges.some(edge => edge.id === 'manual')).toBe(true);
    invalid.hook.unmount();
  });

  it('submits the script shot own ordered keyframes and rejects an incoming continuity image', async () => {
    const data = { genMode: 'firstLastFrame', scriptShotSourceNodeId: 'script', scriptShotImageNodeId: 'first' };
    useCanvasStore.getState().setCanvasData([
      { id: 'first', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/first.png' } },
      { id: 'last', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/last.png' } },
      { id: 'video-node', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data },
    ] as never, [
      { id: 'a', source: 'first', target: 'video-node' },
      { id: 'b', source: 'last', target: 'video-node' },
    ] as never);
    mocks.awaitMedia.mockResolvedValue({ url: '/result.mp4' });
    const valid = setup(1, { genMode: 'firstLastFrame', data: data as never });
    await act(async () => { await valid.hook.result.current(); });
    expect(mocks.submit).toHaveBeenCalledWith('test-project', expect.objectContaining({ firstFrameUrl: '/first.png', lastFrameUrl: '/last.png', genMode: 'firstLastFrame' }));
    expect(valid.updateNodeData).toHaveBeenCalledWith('video-node', expect.objectContaining({ videoUrl: '/result.mp4', scriptShotRenderedFrames: expect.objectContaining({ videoUrl: '/result.mp4', firstFrameUrl: '/first.png', lastFrameUrl: '/last.png' }) }));
    valid.hook.unmount();
    mocks.awaitMedia.mockRejectedValue(new Error('provider failed'));
    const failed = setup(1, { genMode: 'firstLastFrame', data: data as never });
    await act(async () => { await failed.hook.result.current(); });
    expect(failed.updateNodeData.mock.calls.some(([, patch]) => 'scriptShotRenderedFrames' in patch)).toBe(false);
    failed.hook.unmount();
    const state = useCanvasStore.getState();
    state.setCanvasData(state.nodes, state.edges.map(edge => edge.id === 'b' ? { ...edge, data: { role: 'scriptShotContinuity' } } : edge));
    mocks.submit.mockClear();
    const invalid = setup(1, { genMode: 'firstLastFrame', data: data as never });
    await act(async () => { await invalid.hook.result.current(); });
    expect(mocks.submit).not.toHaveBeenCalled();
    expect(invalid.updateNodeData).toHaveBeenCalledWith('video-node', expect.objectContaining({ generationErrorCode: 'SCRIPT_KEYFRAMES_INVALID' }));
    invalid.hook.unmount();
  });

  it.each(["reject", "resolve"])("does not write after cancellation when monitoring later %ss", async (outcome) => {
    let finish!: () => void;
    mocks.awaitMedia.mockImplementation(() => new Promise((resolve, reject) => {
      finish = () => outcome === "reject"
        ? reject(new DOMException("signal is aborted without reason", "AbortError"))
        : resolve({ url: "/late-video.mp4" });
    }));
    const { hook, context, updateNodeData } = setup();
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current(); });
    await waitFor(() => expect(mocks.awaitMedia).toHaveBeenCalledOnce());
    context.generationQueueAbortRef.current!.abort();
    hook.unmount();
    updateNodeData.mockClear();
    await act(async () => { finish(); await pending; });

    expect(updateNodeData).not.toHaveBeenCalled();
    expect(mocks.dialog).not.toHaveBeenCalled();
    expect(mocks.toast).not.toHaveBeenCalled();
  });

  it("keeps a real failure visible even when its text mentions abort", async () => {
    const message = "Upstream failed: signal is aborted without reason";
    mocks.awaitMedia.mockRejectedValue(new Error(message));
    const { hook, updateNodeData } = setup();
    await act(async () => { await hook.result.current(); });

    expect(updateNodeData).toHaveBeenCalledWith("video-node", expect.objectContaining({
      generationError: message,
      generationRecoveryJobId: "job",
    }));
    expect(mocks.dialog).toHaveBeenCalledWith(expect.objectContaining({ message }));
  });

  it.each([
    { planned: 4, effective: 5, enabled: true, options: [5, 10] },
    { planned: 10, effective: 5, enabled: true, options: [5] },
    { planned: 5, effective: 5, enabled: false, options: undefined },
  ])("rejects silent script duration changes before submission: $planned / $effective", async ({ planned, effective, enabled, options }) => {
    const { hook, context, updateNodeData } = setup(1, {
      data: { videoUrl: "/existing.mp4", aspectRatio: "16:9", scriptShotSourceNodeId: "script", scriptShotRowDurationSec: planned },
      durationSec: effective, durationParameterEnabled: enabled, durationOptions: options,
    });
    await act(async () => { await hook.result.current(); });
    expect(mocks.submit).not.toHaveBeenCalled();
    expect(updateNodeData).toHaveBeenCalledWith("video-node", expect.objectContaining({
      generationErrorCode: "SCRIPT_VIDEO_DURATION_MISMATCH", generationErrorStage: "preflight",
    }));
    expect(context.submittingRef.current).toBe(false);
    expect(context.generationQueueAbortRef.current).toBeNull();
    expect(updateNodeData.mock.calls.every(([, patch]) => !("videoUrl" in patch) && !("generationBatch" in patch))).toBe(true);
  });

  it("submits a supported script duration without rewriting timing", async () => {
    mocks.awaitMedia.mockResolvedValue({ url: "/clip.mp4" });
    const { hook } = setup(1, {
      data: { videoUrl: "", aspectRatio: "16:9", scriptShotSourceNodeId: "script", scriptShotRowDurationSec: 5 },
      durationSec: 5, durationParameterEnabled: true, durationOptions: [5, 10],
    });
    await act(async () => { await hook.result.current(); });
    expect(mocks.submit).toHaveBeenCalledOnce();
    expect(mocks.submit.mock.calls[0][1]).toEqual(expect.objectContaining({ durationSeconds: 5 }));
  });

  it('submits adapted generation seconds while retaining two-second editorial timing', async () => {
    mocks.awaitMedia.mockResolvedValue({ url: '/clip.mp4' });
    const { hook } = setup(1, {
      data: { videoUrl: '', aspectRatio: '16:9', scriptShotSourceNodeId: 'script', scriptShotRowDurationSec: 2, scriptShotGenerationDurationSec: 5 },
      durationSec: 5, durationParameterEnabled: true, durationOptions: [5, 10],
    });
    await act(async () => { await hook.result.current(); });
    expect(mocks.submit).toHaveBeenCalledOnce();
    expect(mocks.submit.mock.calls[0][1]).toEqual(expect.objectContaining({ durationSeconds: 5 }));
  });

  it("does not report earlier sibling failures after the batch is abandoned", async () => {
    let rejectSecond!: (error: unknown) => void;
    mocks.awaitMedia
      .mockRejectedValueOnce(new Error("first job failed"))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectSecond = reject; }));
    const { hook, context, updateNodeData } = setup(2);
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current(); });
    await waitFor(() => expect(updateNodeData).toHaveBeenCalledWith("video-node",
      expect.objectContaining({ generationError: "first job failed" })));
    context.generationQueueAbortRef.current!.abort();
    updateNodeData.mockClear();
    await act(async () => {
      rejectSecond(new DOMException("aborted", "AbortError"));
      await pending;
    });

    expect(updateNodeData).not.toHaveBeenCalled();
    expect(mocks.dialog).not.toHaveBeenCalled();
    expect(mocks.toast).not.toHaveBeenCalled();
  });
});
