import { describe, expect, it } from 'vitest';

import { resolveImageAutoSubmitDecision } from './image-auto-submit';

const ready = {
  queued: true,
  isGenerating: false,
  imageModelsLoading: false,
  hasAuthoritativeImageModel: true,
  submitDisabled: false,
  hasServerTask: false,
};

describe('resolveImageAutoSubmitDecision', () => {
  it('keeps the durable flag while the model catalog is loading', () => {
    expect(resolveImageAutoSubmitDecision({ ...ready, imageModelsLoading: true })).toBe('wait');
  });

  it('submits only after the authoritative model and prompt are ready', () => {
    expect(resolveImageAutoSubmitDecision(ready)).toBe('submit');
    expect(resolveImageAutoSubmitDecision({ ...ready, submitDisabled: true })).toBe('wait');
  });

  it('does not launch a browser duplicate for a server-owned task', () => {
    expect(resolveImageAutoSubmitDecision({ ...ready, hasServerTask: true })).toBe('server-owned');
  });
});
