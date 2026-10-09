// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo } from 'react';

import {
  type DirectModelConfig,
  type DirectModelKind,
  useModelGatewayConfig,
} from '@/lib/queries/model-gateway';
import { isRunnableDirectModel } from '@/features/canvas/domain/directModelReadiness';

export type DirectCanvasModel = DirectModelConfig & { catalogId: string };

export type DirectCanvasModelSelectionStatus = 'default' | 'selected' | 'stale';

/**
 * The unified chat family serves Agent and vision work only when the operator
 * declared that capability; text/chat work accepts every chat row.
 */
export function supportsDirectModelKind(
  kind: DirectModelKind,
  item: Pick<DirectModelConfig, 'supportsTools' | 'supportsVision'>,
): boolean {
  if (kind === 'agent') return item.supportsTools !== false;
  if (kind === 'vision') return item.supportsVision !== false;
  return true;
}

export interface DirectCanvasModelSelection {
  model: DirectCanvasModel | null;
  modelId: string;
  requestedId: string;
  status: DirectCanvasModelSelectionStatus;
}

export function runnableDirectCanvasModels(
  items: readonly DirectModelConfig[],
  requiredMode?: string,
  kind?: DirectModelKind,
): DirectCanvasModel[] {
  return items
    .filter(isRunnableDirectModel)
    .filter((item) => !kind || supportsDirectModelKind(kind, item))
    .filter((item) => !requiredMode || item.supportedModes?.includes(requiredMode) === true)
    .map((item) => ({ ...item, catalogId: `direct/${item.id}` }));
}

export function resolveDirectCanvasModelId(
  value: string | null | undefined,
  models: readonly DirectCanvasModel[],
): string {
  return resolveDirectCanvasModelSelection(value, models).modelId;
}

/**
 * Resolve a persisted direct-model binding without ever replacing an
 * explicit, missing binding with the model-center default.  Empty values are
 * the only case where a default is selected (new nodes); a non-empty value
 * that no longer exists is returned as `stale` for a UI/submit gate to handle.
 */
export function resolveDirectCanvasModelSelection(
  value: string | null | undefined,
  models: readonly DirectCanvasModel[],
): DirectCanvasModelSelection {
  const requestedId = typeof value === 'string' ? value.trim() : '';
  if (requestedId) {
    // Older nodes persisted the registry id without the `direct/` namespace,
    // and some pre-catalog nodes stored the upstream model id directly.
    // Resolve all three forms to the one opaque catalog id used by execution.
    const raw = requestedId;
    const normalized = raw.startsWith('direct/') ? raw : `direct/${raw}`;
    const bareId = raw.replace(/^direct\//i, '').toLowerCase();
    const match = models.find((item) => (
      item.catalogId.toLowerCase() === raw.toLowerCase()
      || item.catalogId.toLowerCase() === normalized.toLowerCase()
      || item.id.toLowerCase() === bareId
      // Retired agent/text/vision ids were folded into the chat row.
      || (item.aliases ?? []).some((alias) => alias.toLowerCase() === bareId)
      || item.modelId.toLowerCase() === raw.toLowerCase()
    ));
    if (match) {
      return {
        model: match,
        modelId: match.catalogId,
        requestedId,
        status: 'selected',
      };
    }
    return { model: null, modelId: '', requestedId, status: 'stale' };
  }
  const model = models.find((item) => item.isDefault) ?? models[0] ?? null;
  return {
    model,
    modelId: model?.catalogId ?? '',
    requestedId,
    status: 'default',
  };
}

/**
 * One cached server-side model directory for every non-video canvas domain.
 *
 * Node data stores ``direct/<id>`` only.  The endpoint and key stay in the
 * local backend registry, which also means model-center edits are reflected in
 * every picker without localStorage drift.
 */
export function useDirectModelCatalog(kind: DirectModelKind, requiredMode?: string) {
  const query = useModelGatewayConfig();
  const models = useMemo<DirectCanvasModel[]>(() => (
    runnableDirectCanvasModels(query.data?.data.directModels?.[kind] ?? [], requiredMode, kind)
  ), [kind, query.data?.data.directModels, requiredMode]);
  const defaultModel = models.find((item) => item.isDefault) ?? models[0] ?? null;
  return {
    models,
    defaultModel,
    isLoading: query.isLoading,
    isError: query.isError,
  };
}
