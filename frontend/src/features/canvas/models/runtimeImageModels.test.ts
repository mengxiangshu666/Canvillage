import { describe, expect, it } from 'vitest';

import type { ModelOption } from '@/features/canvas/ui/ProviderModelPicker';

import { runtimeImageModelFromOption } from './runtimeImageModels';

function model(overrides: Partial<ModelOption> = {}): ModelOption {
  return {
    id: 'direct/image-primary',
    providerId: 'direct',
    apiModel: 'direct/image-primary',
    label: 'Primary image model',
    ...overrides,
  };
}

describe('runtime image capability mapping', () => {
  it('uses exact backend-declared aspect and resolution options', () => {
    const runtime = runtimeImageModelFromOption(
      model({
        aspectRatioOptions: ['1:1', '3:2', '2:3'],
        resolutionOptions: ['1K', '2K'],
      }),
    );

    expect(runtime.aspectRatios.map((item) => item.value)).toEqual(['1:1', '3:2', '2:3']);
    expect(runtime.resolutions.map((item) => item.value)).toEqual(['1K', '2K']);
  });

  it('preserves all fourteen gpt-image-1k-th aspect ratios for the node', () => {
    const ratios = [
      '1:1', '1:4', '1:8', '2:3', '3:2', '3:4', '4:1',
      '4:3', '4:5', '5:4', '8:1', '9:16', '16:9', '21:9',
    ];
    const runtime = runtimeImageModelFromOption(
      model({ aspectRatioOptions: ratios, capabilitySource: 'profile' }),
    );

    expect(runtime.aspectRatios.map((item) => item.value)).toEqual(ratios);
  });

  it('normalizes stale defaults to the first supported capability', () => {
    const runtime = runtimeImageModelFromOption(
      model({
        aspectRatioOptions: ['3:2', '2:3'],
        resolutionOptions: ['1K', '4K'],
        parameterDefaults: { aspectRatio: '16:9', resolution: '2K' },
      }),
    );

    expect(runtime.defaultAspectRatio).toBe('3:2');
    expect(runtime.defaultResolution).toBe('1K');
  });

  it('keeps the legacy fallback only when no capability contract exists', () => {
    const runtime = runtimeImageModelFromOption(model());

    expect(runtime.aspectRatios.map((item) => item.value)).toEqual([
      '1:1',
      '16:9',
      '9:16',
      '4:3',
      '3:4',
    ]);
    expect(runtime.resolutions.map((item) => item.value)).toEqual(['1K', '2K', '4K']);
  });

  it('does not restore presets when the upstream explicitly declares no options', () => {
    const runtime = runtimeImageModelFromOption(
      model({ aspectRatioOptions: [], resolutionOptions: [] }),
    );

    expect(runtime.aspectRatios).toEqual([]);
    expect(runtime.resolutions).toEqual([]);
    expect(runtime.defaultAspectRatio).toBe('');
    expect(runtime.defaultResolution).toBe('');
  });

  it('hides controls when the capability contract is explicitly unknown', () => {
    const runtime = runtimeImageModelFromOption(
      model({ capabilitySource: 'unknown' }),
    );

    expect(runtime.aspectRatios).toEqual([]);
    expect(runtime.resolutions).toEqual([]);
    expect(runtime.supportedModes).toEqual([]);
    expect(runtime.defaultAspectRatio).toBe('');
    expect(runtime.defaultResolution).toBe('');
  });

  it('keeps declared custom capabilities available to the runtime model', () => {
    const runtime = runtimeImageModelFromOption(
      model({
        aspectRatioOptions: ['1:1', '16:9'],
        resolutionOptions: ['1K', '2K'],
        qualityOptions: ['low', 'high', 'auto'],
        supportsCustomAspectRatio: true,
        supportsCustomResolution: true,
        capabilitySource: 'upstream',
      }),
    );

    expect(runtime.qualityOptions?.map((item) => item.value)).toEqual(['low', 'high', 'auto']);
    expect(runtime.supportsCustomAspectRatio).toBe(true);
    expect(runtime.supportsCustomResolution).toBe(true);
    expect(runtime.capabilitySource).toBe('upstream');
  });

  it('does not resurrect text-to-image for an explicit image-edit-only contract', () => {
    const runtime = runtimeImageModelFromOption(
      model({ supportedModes: ['imageToImage'] }),
    );

    expect(runtime.supportedModes).toEqual(['image_to_image']);
    expect(runtime.resolveRequest({ referenceImageCount: 0 }).modeLabel).toBe('图生图');
    expect(runtime.resolveRequest({ referenceImageCount: 1 }).modeLabel).toBe('图生图');
  });

  it('exposes model-declared advanced parameters to the node panel', () => {
    const runtime = runtimeImageModelFromOption(
      model({
        capabilitySource: 'profile',
        advancedParamsSchema: [
          {
            key: 'stylize',
            label: '风格化程度',
            type: 'number',
            min: 0,
            max: 1000,
            step: 50,
            defaultValue: 100,
          },
          {
            key: 'personalisation',
            label: '个性化风格',
            type: 'string',
            defaultValue: '',
          },
        ],
        advancedParamDefaults: { stylize: 100, personalisation: '' },
      }),
    );

    expect(runtime.extraParamsSchema?.map((item) => item.key)).toEqual([
      'stylize',
      'personalisation',
    ]);
    expect(runtime.extraParamsSchema?.[0].step).toBe(50);
    expect(runtime.defaultExtraParams).toEqual({ stylize: 100, personalisation: '' });
  });

  it('leaves the advanced panel closed when the model declares no parameters', () => {
    expect(runtimeImageModelFromOption(model()).extraParamsSchema).toBeUndefined();
    expect(
      runtimeImageModelFromOption(model({ advancedParamsSchema: [] })).extraParamsSchema,
    ).toBeUndefined();
  });
});
