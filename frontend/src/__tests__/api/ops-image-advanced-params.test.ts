// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { coerceModelList } from '@/api/ops';
import { describe, expect, it } from 'vitest';

describe('image catalog advanced parameter coercion', () => {
  it('carries the model-declared advanced schema into the picker contract', () => {
    const [model] = coerceModelList([
      {
        id: 'direct/mj-v8.2',
        provider_id: 'direct',
        api_model: 'direct/mj-v8.2',
        label: 'MJ 8.2',
        advanced_params_schema: [
          { key: 'personalisation', label: '个性化风格', type: 'string', defaultValue: '' },
          {
            key: 'stylize',
            label: '风格化程度',
            type: 'slider',
            defaultValue: 100,
            min: 0,
            max: 1000,
            step: 50,
          },
          {
            key: 'weird',
            label: '怪异度',
            type: 'slider',
            defaultValue: 50,
            min: 0,
            max: 3000,
            step: 50,
          },
          { key: 'chaos', label: '多样性', type: 'slider', defaultValue: 5, min: 0, max: 100, step: 5 },
        ],
        advanced_param_defaults: { personalisation: '', stylize: 100, weird: 50, chaos: 5 },
      },
    ]);

    // `slider` is the backend's bounded numeric control; the shared parameter
    // panel has only one numeric input, so the catalog folds it into `number`.
    expect(model.advancedParamsSchema?.map((item) => [item.key, item.type])).toEqual([
      ['personalisation', 'string'],
      ['stylize', 'number'],
      ['weird', 'number'],
      ['chaos', 'number'],
    ]);
    expect(model.advancedParamsSchema?.[1].max).toBe(1000);
    expect(model.advancedParamDefaults).toEqual({
      personalisation: '',
      stylize: 100,
      weird: 50,
      chaos: 5,
    });
  });

  it('drops malformed advanced entries instead of rendering broken controls', () => {
    const [model] = coerceModelList([
      {
        id: 'direct/x',
        provider_id: 'direct',
        api_model: 'direct/x',
        label: 'X',
        advanced_params_schema: [
          { label: '无 key', type: 'string' },
          { key: 'ok', label: 'OK', type: 'string' },
          { key: 'bad-type', label: '类型不支持', type: 'color' },
        ],
      },
    ]);

    expect(model.advancedParamsSchema?.map((item) => item.key)).toEqual(['ok']);
  });

  it('does not invent an advanced contract the catalog never sent', () => {
    const [model] = coerceModelList([
      { id: 'direct/y', provider_id: 'direct', api_model: 'direct/y', label: 'Y' },
    ]);

    expect(model.advancedParamsSchema).toBeUndefined();
    expect(model.advancedParamDefaults).toBeUndefined();
  });

  it('treats an explicit empty advanced list as a declaration, not a gap', () => {
    const [model] = coerceModelList([
      {
        id: 'direct/z',
        provider_id: 'direct',
        api_model: 'direct/z',
        label: 'Z',
        advanced_params_schema: [],
        advanced_param_defaults: {},
      },
    ]);

    expect(model.advancedParamsSchema).toEqual([]);
    expect(model.advancedParamDefaults).toBeUndefined();
  });
});
