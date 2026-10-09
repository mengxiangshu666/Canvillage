import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { videoGenerationSourceMatches, videoGenerationSourcePatch } from './videoGenerationSource';
import fixture from '../../../../../tests/fixtures/video_generation_request.json';

describe('video generation source', () => {
  const prompt = '完整正文'.repeat(1200) + '手握[A + B]，看向红门';
  const source = {
    schema: 'video_generation_source.v1', task_type: 'freezone_video_gen', job_id: 'original-job',
    output_url: '/original.mp4', execution_prompt_sha256: createHash('sha256').update(prompt).digest('hex'),
  };

  it('checks the whole authored request and exact artifact URL', () => {
    expect(videoGenerationSourceMatches('/original.mp4', source, prompt)).toBe(true);
    expect(videoGenerationSourceMatches('/original.mp4', source, `[视频参考用途：@图片2锁定[A + B]]\n${prompt}\n `)).toBe(true);
    expect(videoGenerationSourceMatches('/original.mp4', source, prompt + '改看北门')).toBe(false);
    expect(videoGenerationSourceMatches('/other.mp4', source, prompt)).toBe(false);
  });

  it('unknown history and unrelated receipts clear old evidence without borrowing current prose', () => {
    expect(videoGenerationSourcePatch('/old.mp4', { prompt, prompt_sha256: source.execution_prompt_sha256 }).videoGenerationSource).toBeNull();
    expect(videoGenerationSourcePatch('/other.mp4', { video_generation_source: source }).videoGenerationSource).toBeNull();
    expect(videoGenerationSourcePatch('/original.mp4', { video_generation_source: { ...source, job_id: '' } }).videoGenerationSource).toBeNull();
    expect(videoGenerationSourcePatch('/original.mp4', { video_generation_source: source }).videoGenerationSource).toEqual(source);
    expect(videoGenerationSourcePatch('/original.mp4', { video_generation_source: source }, 'another-job').videoGenerationSource).toBeNull();
  });

  it('keeps the backend execution-input receipt without reading current node settings', () => {
    const recorded = { ...source, generation_request: fixture.request, provider_model: 'reported-model' };
    const patch = videoGenerationSourcePatch('/original.mp4', { video_generation_source: recorded });
    expect(patch.videoGenerationSource).toEqual(recorded);
    expect(patch.videoGenerationSource?.generation_request).not.toBe(recorded.generation_request);
    expect(videoGenerationSourceMatches('/original.mp4', recorded, prompt)).toBe(true);
  });

  it.each(['settings', 'role', 'extra', 'digest'])('rejects a damaged %s request instead of downgrading it to a legacy receipt', (field) => {
    const request = structuredClone(fixture.request);
    if (field === 'settings') request.settings.duration_seconds = '15';
    else if (field === 'role') request.inputs[0].role = 'motion';
    else if (field === 'extra') Object.assign(request, { raw_reference_url: 'https://fixture.invalid/unexpected' });
    else request.request_sha256 = 'f'.repeat(64);
    expect(videoGenerationSourcePatch('/original.mp4', { video_generation_source: { ...source, generation_request: request } }).videoGenerationSource).toBeNull();
  });
});
