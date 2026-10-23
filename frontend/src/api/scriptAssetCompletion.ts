// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from './client';
import type { FreezoneStoryDirectorPlan, FreezoneStoryScriptRow } from './scriptContract';

export interface ScriptAssetCompletionReceipt {
  asset_key: string;
  status?: 'completed' | 'failed';
  changed_fields?: string[];
  error?: string;
  media_repair_triggered?: boolean;
  paid_generation_triggered?: boolean;
}

export interface ScriptAssetCompletionResponse {
  director_plan: FreezoneStoryDirectorPlan;
  receipts: ScriptAssetCompletionReceipt[];
}

/** 只补全结构化资产定义，不生成媒体；调用方负责写回节点并提示费用边界。 */
export async function completeFreezoneScriptAssetContents(
  project: string,
  payload: {
    directorPlan: FreezoneStoryDirectorPlan;
    rows: FreezoneStoryScriptRow[];
    sourceText?: string;
    model?: string | null;
    maxAssets?: number;
  },
): Promise<ScriptAssetCompletionResponse> {
  return await apiCall<ScriptAssetCompletionResponse>(
    `projects/${encodeURIComponent(project)}/freezone/script/assets/complete`,
    {
      method: 'POST',
      json: {
        director_plan: payload.directorPlan,
        rows: payload.rows,
        source_text: payload.sourceText ?? '',
        model: payload.model ?? '',
        max_assets: payload.maxAssets ?? 6,
      },
    },
  );
}
