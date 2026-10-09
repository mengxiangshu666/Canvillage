// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useSyncExternalStore } from "react";

import {
  fetchFreezoneImageModels,
  syncFreezoneDirectImageModels,
  type DirectImageModelRegistration,
  type FreezoneImageModelInfo,
} from "@/api/ops";
import { readUrl } from "@/lib/url-params";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";

export interface UseFreezoneImageModelsResult {
  models: ModelOption[];
  isLoading: boolean;
  /** True only when the durable catalog could not be reached. */
  isFallback: boolean;
  error: Error | null;
}

interface LegacyDirectImageModel extends DirectImageModelRegistration {
  id?: string;
}

const LEGACY_DIRECT_IMAGE_STORAGE_KEY = "village_canvas_direct_image_models";

// Module-level shared store. One state snapshot per project, one fetch per
// project per tab lifetime. Every consumer reads the same reference via
// useSyncExternalStore, so a freshly mounted picker sees the cached result
// immediately (no per-component re-fetch, no loading flicker).
const states = new Map<string, UseFreezoneImageModelsResult>();
const loadedAt = new Map<string, number>();
const listeners = new Map<string, Set<() => void>>();
const migrations = new Map<string, Promise<void>>();
const DIRECT_MODEL_REGISTRY_CHANGED_EVENT = "village:direct-model-registry-changed";
const MODEL_REVALIDATE_AFTER_MS = 15_000;

// Revalidation side effects are installed once per tab; the interval handle is
// retained so `pagehide` can clear it instead of leaking a timer forever.
let sharedRevalidationInstalled = false;
let revalidationIntervalId: number | null = null;

const EMPTY_STATE: UseFreezoneImageModelsResult = {
  models: [],
  isLoading: false,
  isFallback: false,
  error: null,
};

function emit(project: string) {
  listeners.get(project)?.forEach((fn) => fn());
}

function writeState(project: string, next: UseFreezoneImageModelsResult) {
  states.set(project, next);
  if (!next.isLoading) {
    loadedAt.set(project, Date.now());
  }
  emit(project);
}

function supportsImageToImage(modelId: string): boolean {
  const value = String(modelId || "").toLowerCase();
  if (/dall[-_ ]?e[-_ ]?3/.test(value)) return false;
  return /gpt[-_ ]?image|image[-_ ]?2|dall[-_ ]?e[-_ ]?2|nano[-_ ]?banana|gemini.*image|seedream|wanx.*image|flux.*(?:kontext|edit)|qwen.*image|image.*(?:edit|reference)/.test(value);
}

function legacyModelOption(model: LegacyDirectImageModel, index: number): ModelOption | null {
  const label = String(model.label || "").trim();
  const upstreamModel = String(model.modelId || "").trim();
  const baseUrl = String(model.baseUrl || "").trim();
  const apiKey = String(model.apiKey || "").trim();
  if (!label || !upstreamModel || !baseUrl || !apiKey || model.enabled === false) return null;
  const id = String(model.id || `image-legacy-${index}`).trim();
  const imageToImage = supportsImageToImage(upstreamModel);
  return {
    id: `direct/${id}`,
    providerId: "direct",
    apiModel: `direct/${id}`,
    label,
    enabled: true,
    supportedModes: imageToImage ? ["textToImage", "imageToImage"] : ["textToImage"],
    useCase: imageToImage ? "已识别：文生图、图生图" : "已识别：文生图",
    channelLabel: "直连生图 API（正在迁移）",
  };
}

function readLegacyDirectImageModels(): LegacyDirectImageModel[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(LEGACY_DIRECT_IMAGE_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
      .map((item) => ({
        id: typeof item.id === "string" ? item.id : undefined,
        label: typeof item.label === "string" ? item.label : "",
        modelId: typeof item.modelId === "string" ? item.modelId : "",
        baseUrl: typeof item.baseUrl === "string" ? item.baseUrl : "",
        apiKey: typeof item.apiKey === "string" ? item.apiKey : "",
        enabled: typeof item.enabled === "boolean" ? item.enabled : true,
        isDefault: item.isDefault === true,
      }))
      .filter((model) => Boolean(model.label && model.modelId && model.baseUrl && model.apiKey));
  } catch {
    return [];
  }
}

function toModelOptions(models: FreezoneImageModelInfo[]): ModelOption[] {
  return models;
}

async function migrateLegacyDirectImageModels(
  project: string,
  legacy: LegacyDirectImageModel[],
): Promise<void> {
  if (legacy.length === 0) return;
  const existing = migrations.get(project);
  if (existing) return await existing;
  const migration = syncFreezoneDirectImageModels(legacy).catch((error) => {
    // A failed migration never drops the local configuration. The caller will
    // expose the error and retry on the next page load or settings save.
    throw error;
  });
  migrations.set(project, migration);
  try {
    await migration;
  } finally {
    migrations.delete(project);
  }
}

function ensureLoaded(project: string) {
  if (states.has(project)) return;
  const legacy = readLegacyDirectImageModels();
  const optimisticModels = legacy
    .map(legacyModelOption)
    .filter((model): model is ModelOption => Boolean(model));
  states.set(project, {
    models: optimisticModels,
    isLoading: true,
    isFallback: false,
    error: null,
  });

  void (async () => {
    try {
      await migrateLegacyDirectImageModels(project, legacy);
      const models = await fetchFreezoneImageModels(project);
      writeState(project, {
        models: toModelOptions(models),
        isLoading: false,
        isFallback: false,
        error: null,
      });
    } catch (error: unknown) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      console.warn("[freezone] image model catalog unavailable:", normalized.message);
      writeState(project, {
        models: optimisticModels,
        isLoading: false,
        isFallback: true,
        error: normalized,
      });
    }
  })();
}

function revalidateLoadedImageModels(): void {
  const now = Date.now();
  for (const [project, state] of states.entries()) {
    if (state.isLoading || now - (loadedAt.get(project) ?? 0) < MODEL_REVALIDATE_AFTER_MS) {
      continue;
    }
    // Keep the current controls visible while the authoritative catalog is
    // refreshed; a transient network error must not erase a usable model list.
    writeState(project, { ...state, isLoading: true, error: null });
    void fetchFreezoneImageModels(project)
      .then((models) => {
        writeState(project, {
          models: toModelOptions(models),
          isLoading: false,
          isFallback: false,
          error: null,
        });
      })
      .catch((error: unknown) => {
        const normalized = error instanceof Error ? error : new Error(String(error));
        writeState(project, { ...state, isLoading: false, error: normalized });
      });
  }
}

/** Trigger the shared model fetch eagerly for a project. */
export function prefetchFreezoneImageModels(project: string): void {
  if (project) ensureLoaded(project);
}

function refreshLoadedImageModels(): void {
  for (const project of [...states.keys()]) {
    states.delete(project);
    ensureLoaded(project);
    emit(project);
  }
}

if (typeof window !== "undefined") {
  installSharedModelRevalidation();
}

/**
 * Wire the tab-scoped revalidation side effects exactly once.
 *
 * This module owns a singleton store, so the periodic timer has no component
 * to unmount with; the tab teardown (`pagehide`) is its lifecycle boundary.
 * The handle is retained and cleared there, and the install guard keeps a Vite
 * HMR re-evaluation of this module from stacking a second interval (and a
 * duplicate set of listeners) on top of the first.
 */
function installSharedModelRevalidation(): void {
  if (sharedRevalidationInstalled) return;
  sharedRevalidationInstalled = true;

  window.addEventListener(DIRECT_MODEL_REGISTRY_CHANGED_EVENT, (event) => {
    const detail = (event as CustomEvent<{ kind?: string }>).detail;
    if (detail?.kind === "image") refreshLoadedImageModels();
  });
  window.addEventListener("focus", revalidateLoadedImageModels);
  window.addEventListener("pageshow", revalidateLoadedImageModels);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") revalidateLoadedImageModels();
  });
  // A running canvas tab may remain focused while the local API is rebuilt or
  // restarted. Periodic revalidation closes that gap without refetching on
  // every render or interrupting an in-progress generation.
  revalidationIntervalId = window.setInterval(revalidateLoadedImageModels, 30_000);

  const stopRevalidation = () => {
    if (revalidationIntervalId == null) return;
    window.clearInterval(revalidationIntervalId);
    revalidationIntervalId = null;
  };
  // A bfcache restore fires `pageshow` (and revalidates); the timer is only
  // stopped for a real teardown, where `pageshow` never fires again.
  window.addEventListener("pagehide", (event) => {
    if ((event as PageTransitionEvent).persisted) return;
    stopRevalidation();
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
 * Read the authoritative image model catalog. Browser-local legacy records are
 * migrated once before the catalog is read, so model labels never fall back to
 * the retired hard-coded Village Infinite Canvas Image entry.
 */
export function useFreezoneImageModels(
  projectOverride?: string | null,
): UseFreezoneImageModelsResult {
  const project = projectOverride !== undefined ? projectOverride : readUrl().project;
  if (project) ensureLoaded(project);

  return useSyncExternalStore(
    (callback) => subscribe(project ?? null, callback),
    () => (project ? states.get(project) ?? EMPTY_STATE : EMPTY_STATE),
    () => EMPTY_STATE,
  );
}
