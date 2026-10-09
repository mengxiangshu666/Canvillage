// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useSyncExternalStore } from "react";

import {
  fetchFreezoneVideoModelsDetailed,
  type FreezoneVideoChannelStatus,
  type FreezoneVideoModelInfo,
  VIDEO_CHANNEL_OFFLINE_REASON,
} from "@/api/ops";
import { readUrl } from "@/lib/url-params";
import {
  VIDEO_MODELS,
  type ModelOption,
} from "@/features/canvas/ui/ProviderModelPicker";

export interface UseFreezoneVideoModelsResult {
  models: ModelOption[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
  /** Channel honesty: false when Freezone video generation is intentionally offline. */
  channelEnabled: boolean;
  channelDisabledReason: string;
}

// Module-level shared store, mirrors useFreezoneImageModels but keyed under
// a separate namespace so image and video fetches don't collide. One fetch
// per project per tab lifetime.
const states = new Map<string, UseFreezoneVideoModelsResult>();
const requestVersions = new Map<string, number>();
const listeners = new Map<string, Set<() => void>>();
const DIRECT_MODEL_REGISTRY_CHANGED_EVENT = "village:direct-model-registry-changed";

// Lazy singleton — circular import with `ProviderModelPicker.tsx` means we
// can't touch `VIDEO_MODELS` at module top level (TDZ).
let noProjectStateMemo: UseFreezoneVideoModelsResult | null = null;
function getNoProjectState(): UseFreezoneVideoModelsResult {
  if (!noProjectStateMemo) {
    noProjectStateMemo = {
      models: VIDEO_MODELS,
      isLoading: false,
      isFallback: true,
      error: null,
      channelEnabled: false,
      channelDisabledReason: VIDEO_CHANNEL_OFFLINE_REASON,
    };
  }
  return noProjectStateMemo;
}

function emit(project: string) {
  listeners.get(project)?.forEach((fn) => fn());
}

function writeState(project: string, next: UseFreezoneVideoModelsResult) {
  states.set(project, next);
  emit(project);
}

function toModelOptions(models: FreezoneVideoModelInfo[]): ModelOption[] {
  return models.map((model) => ({
    ...model,
    providerId: model.providerId as ModelOption["providerId"],
  }));
}

function ensureLoaded(project: string, force = false) {
  const previous = states.get(project);
  if (previous && !force) return;
  const requestVersion = (requestVersions.get(project) ?? 0) + 1;
  requestVersions.set(project, requestVersion);

  // Keep the last real catalog visible while a refresh is in flight. A
  // transient API failure must not make configured models disappear from the
  // picker or reset the channel to the offline fallback.
  states.set(project, {
    models: previous?.models ?? VIDEO_MODELS,
    isLoading: true,
    isFallback: previous?.isFallback ?? true,
    error: null,
    channelEnabled: previous?.channelEnabled ?? false,
    channelDisabledReason:
      previous?.channelDisabledReason ?? VIDEO_CHANNEL_OFFLINE_REASON,
  });
  fetchFreezoneVideoModelsDetailed(project)
    .then((response) => {
      if (requestVersions.get(project) !== requestVersion) return;
      if (response.models.length === 0) {
        writeState(project, {
          models: VIDEO_MODELS,
          isLoading: false,
          isFallback: true,
          error: null,
          channelEnabled: response.channel.enabled,
          channelDisabledReason: response.channel.disabledReason || VIDEO_CHANNEL_OFFLINE_REASON,
        });
        return;
      }
      writeState(project, {
        models: toModelOptions(response.models),
        isLoading: false,
        isFallback: false,
        error: null,
        channelEnabled: response.channel.enabled,
        channelDisabledReason: response.channel.disabledReason || "",
      });
    })
    .catch((error: unknown) => {
      if (requestVersions.get(project) !== requestVersion) return;
      const normalized =
        error instanceof Error ? error : new Error(String(error));
      console.warn(
        "[freezone] video models refresh failed, preserving last known catalog:",
        normalized.message,
      );
      const current = states.get(project);
      if (current && !current.isFallback) {
        writeState(project, {
          ...current,
          isLoading: false,
          error: normalized,
        });
        return;
      }
      writeState(project, {
        models: VIDEO_MODELS,
        isLoading: false,
        isFallback: true,
        error: normalized,
        channelEnabled: false,
        channelDisabledReason: VIDEO_CHANNEL_OFFLINE_REASON,
      });
    });
}

/**
 * Trigger the shared video-model fetch eagerly for a project. Idempotent —
 * safe to call from FreezoneShell mount alongside the image-model prefetch.
 */
export function prefetchFreezoneVideoModels(project: string): void {
  if (!project) return;
  ensureLoaded(project);
}

function refreshLoadedVideoModels(): void {
  for (const project of [...states.keys()]) {
    ensureLoaded(project, true);
    emit(project);
  }
}

if (typeof window !== "undefined") {
  window.addEventListener(DIRECT_MODEL_REGISTRY_CHANGED_EVENT, (event) => {
    const detail = (event as CustomEvent<{ kind?: string }>).detail;
    if (detail?.kind === "video") refreshLoadedVideoModels();
  });
}

function subscribe(project: string | null, callback: () => void) {
  if (!project) return () => {};
  let bucket = listeners.get(project);
  if (!bucket) {
    bucket = new Set();
    listeners.set(project, bucket);
  }
  bucket.add(callback);
  return () => {
    bucket!.delete(callback);
    if (bucket!.size === 0) listeners.delete(project);
  };
}

/**
 * Read the video model list from a shared module-level store.
 *
 * Mirrors `useFreezoneImageModels` but hits
 * `GET /api/v1/projects/{project}/freezone/video/models`. Failures fall back
 * to the hardcoded `VIDEO_MODELS` catalog marked offline so the picker never
 * pretends generation is live without channel status.
 */
export function useFreezoneVideoModels(
  projectOverride?: string | null,
): UseFreezoneVideoModelsResult {
  const project =
    projectOverride !== undefined ? projectOverride : readUrl().project;

  if (project) ensureLoaded(project);

  return useSyncExternalStore(
    (callback) => subscribe(project ?? null, callback),
    () =>
      project ? states.get(project) ?? getNoProjectState() : getNoProjectState(),
    () => getNoProjectState(),
  );
}

export type { FreezoneVideoChannelStatus };
