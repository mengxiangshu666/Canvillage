// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { DirectModelConfig } from '@/lib/queries/model-gateway';
import {
  resolveDirectCanvasModelId,
  resolveDirectCanvasModelSelection,
  runnableDirectCanvasModels,
} from './useDirectModelCatalog';

function model(
  id: string,
  patch: Partial<DirectModelConfig> = {},
): DirectModelConfig {
  return {
    id,
    label: id,
    modelId: `${id}-upstream`,
    baseUrl: 'https://example.test/v1',
    enabled: true,
    isDefault: false,
    configured: true,
    apiKeyPreview: '***',
    protocol: 'openai-compatible',
    runtimeReady: true,
    ...patch,
  };
}

describe('direct canvas model catalog', () => {
  it('keeps only runnable configured models', () => {
    const result = runnableDirectCanvasModels([
      model('ready'),
      model('offline', { runtimeReady: false }),
      model('legacy-cache', { runtimeReady: undefined }),
      model('probe-incomplete', { runtimeProbeRequired: true, runtimeProbeComplete: false }),
      model('disabled', { enabled: false }),
      model('explicitly-disabled', { disabled: true }),
      model('missing-key', { configured: false }),
    ]);

    expect(result.map((item) => item.catalogId)).toEqual(['direct/ready']);
  });

  it('accepts a model without a probe requirement once runtime readiness is verified', () => {
    const result = runnableDirectCanvasModels([
      model('verified', { runtimeProbeRequired: false }),
      model('probe-complete', { runtimeProbeRequired: true, runtimeProbeComplete: true }),
    ]);

    expect(result.map((item) => item.catalogId)).toEqual([
      'direct/verified',
      'direct/probe-complete',
    ]);
  });

  it('filters audio models by the node operation contract', () => {
    const items = [
      model('speech', { supportedModes: ['text_to_speech'] }),
      model('music', { supportedModes: ['text_to_music'] }),
      model('both', { supportedModes: ['text_to_speech', 'text_to_music'] }),
    ];

    expect(runnableDirectCanvasModels(items, 'text_to_speech').map((item) => item.id)).toEqual([
      'speech',
      'both',
    ]);
    expect(runnableDirectCanvasModels(items, 'text_to_music').map((item) => item.id)).toEqual([
      'music',
      'both',
    ]);
  });

  it('renders the server verdict instead of re-deriving it', () => {
    const result = runnableDirectCanvasModels([
      // Server says usable even though the legacy fields would block it.
      model('server-usable', {
        usable: true,
        runtimeReady: false,
        runtimeProbeRequired: true,
        runtimeProbeComplete: false,
      }),
      // Server says blocked even though the legacy fields look fine.
      model('server-blocked', { usable: false, runtimeReady: true }),
    ]);

    expect(result.map((item) => item.id)).toEqual(['server-usable']);
  });

  it('gates the unified chat family by the declared capability', () => {
    const items = [
      model('plain', { supportsTools: false, supportsVision: false }),
      model('tooled', { supportsTools: true, supportsVision: false }),
      model('multimodal', { supportsTools: true, supportsVision: true }),
    ];

    expect(runnableDirectCanvasModels(items, undefined, 'chat').map((item) => item.id)).toEqual([
      'plain',
      'tooled',
      'multimodal',
    ]);
    expect(runnableDirectCanvasModels(items, undefined, 'agent').map((item) => item.id)).toEqual([
      'tooled',
      'multimodal',
    ]);
    expect(runnableDirectCanvasModels(items, undefined, 'vision').map((item) => item.id)).toEqual([
      'multimodal',
    ]);
  });

  it('resolves a retired agent/text/vision binding through the merged chat row', () => {
    const models = runnableDirectCanvasModels([
      model('chat-merged', { aliases: ['text-retired', 'vision-retired'] }),
    ]);

    expect(resolveDirectCanvasModelId('direct/text-retired', models)).toBe('direct/chat-merged');
    expect(resolveDirectCanvasModelId('vision-retired', models)).toBe('direct/chat-merged');
  });

  it('hydrates empty selections but preserves stale bindings for the caller to handle', () => {
    const models = runnableDirectCanvasModels([
      model('first'),
      model('preferred', { isDefault: true }),
    ]);

    expect(resolveDirectCanvasModelId('', models)).toBe('direct/preferred');
    expect(resolveDirectCanvasModelId('direct/removed', models)).toBe('');
    expect(resolveDirectCanvasModelId('direct/first', models)).toBe('direct/first');
    expect(resolveDirectCanvasModelId('first', models)).toBe('direct/first');
    expect(resolveDirectCanvasModelId('first-upstream', models)).toBe('direct/first');
    expect(resolveDirectCanvasModelSelection('direct/removed', models)).toMatchObject({
      model: null,
      modelId: '',
      requestedId: 'direct/removed',
      status: 'stale',
    });
  });
});
