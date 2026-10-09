import type { FreezonePresetCanvasRequest } from "@/api/canvas";
import type { CanvasSyncStatus } from "./useCanvasSync";
import { SESSION_EXPIRED_EVENT } from "@/lib/session-expiry";
import { normalizePresetProjectionRequest } from "@/features/freezone/projections";

const PROJECTION_STATUS_REFRESH_MS = 30_000;

/**
 * Register every trigger that refreshes projection status and tear them all
 * down synchronously when the shared API layer confirms session expiry.
 */
export function startProjectionStatusRefresh(
  refresh: () => void,
  onSessionExpired: () => void,
): () => void {
  let stopped = false;
  const handleVisibilityChange = () => {
    if (document.visibilityState === "visible") refresh();
  };
  const stop = () => {
    if (stopped) return;
    stopped = true;
    window.removeEventListener("focus", refresh);
    document.removeEventListener("visibilitychange", handleVisibilityChange);
    window.removeEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
    window.clearInterval(timer);
  };
  const handleSessionExpired = () => {
    stop();
    onSessionExpired();
  };

  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", handleVisibilityChange);
  window.addEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
  const timer = window.setInterval(refresh, PROJECTION_STATUS_REFRESH_MS);
  return stop;
}

export function shouldClearProjectionStatuses({
  canvasId,
  hydratedCanvasId,
  projectionKeyCount,
}: {
  canvasId: string;
  hydratedCanvasId: string | null;
  projectionKeyCount: number;
}): boolean {
  return hydratedCanvasId !== canvasId || projectionKeyCount === 0;
}

export function shouldFetchProjectionStatuses({
  canvasId,
  hydratedCanvasId,
  projectionKeyCount,
  revision,
  sessionExpired = false,
  syncStatus,
}: {
  canvasId: string;
  hydratedCanvasId: string | null;
  projectionKeyCount: number;
  revision: number | null;
  sessionExpired?: boolean;
  syncStatus: CanvasSyncStatus;
}): boolean {
  if (sessionExpired) return false;
  if (shouldClearProjectionStatuses({ canvasId, hydratedCanvasId, projectionKeyCount })) {
    return false;
  }
  return syncStatus === "ready" && revision != null;
}

export function shouldSkipProjectionStatusRevision({
  canvasId,
  revision,
  refreshToken,
  lastChecked,
}: {
  canvasId: string;
  revision: number;
  refreshToken: number;
  lastChecked: { canvasId: string; revision: number; refreshToken: number } | null;
}): boolean {
  if (lastChecked?.canvasId !== canvasId) return false;
  return lastChecked.revision === revision && lastChecked.refreshToken === refreshToken;
}

export function projectionKeysFromMetadata(metadata: Record<string, unknown> | null | undefined): string[] {
  const projections = metadata?.projections;
  if (!projections || typeof projections !== "object") return [];
  return Object.keys(projections).filter((key) => key.trim());
}

function fallbackProjectionRequest(
  projection: Record<string, unknown>,
  projectionKey: string,
): Record<string, unknown> | null {
  const scope = typeof projection.scope === "string"
    ? projection.scope
    : scopeFromProjectionKey(projectionKey);
  if (scope === "beat") {
    const parsed = parseBeatProjectionKey(projectionKey);
    return {
      scope,
      episode: numberOrUndefined(projection.episode) ?? parsed?.episode,
      beat: numberOrUndefined(projection.beat) ?? parsed?.beat,
      primary_slot: typeof projection.primary_slot === "string"
        ? projection.primary_slot
        : "render",
    };
  }
  if (scope === "episode") {
    return {
      scope,
      episode: numberOrUndefined(projection.episode) ?? parseEpisodeProjectionKey(projectionKey),
    };
  }
  if (scope === "asset") {
    const parsed = parseAssetProjectionKey(projectionKey);
    return {
      scope,
      asset_kind: stringOrUndefined(projection.asset_kind) ?? parsed?.asset_kind,
      asset_id: stringOrUndefined(projection.asset_id) ?? parsed?.asset_id,
      character: stringOrUndefined(projection.character),
      identity_id: stringOrUndefined(projection.identity_id),
    };
  }
  if (scope === "blank") return { scope };
  return null;
}

export function requestFromProjectionMetadata(
  metadata: Record<string, unknown> | null | undefined,
  projectionKey: string,
): Omit<FreezonePresetCanvasRequest, "canvas_id" | "overwrite_existing" | "base_revision"> | null {
  const projections = metadata?.projections;
  if (!projections || typeof projections !== "object") return null;
  const projection = (projections as Record<string, unknown>)[projectionKey];
  if (!projection || typeof projection !== "object") return null;
  const projectionRecord = projection as Record<string, unknown>;
  const request = projectionRecord.request && typeof projectionRecord.request === "object"
    ? projectionRecord.request as Record<string, unknown>
    : fallbackProjectionRequest(projectionRecord, projectionKey);
  if (!request) return null;
  const scope = (request as { scope?: unknown }).scope;
  if (scope !== "episode" && scope !== "beat" && scope !== "asset" && scope !== "blank") {
    return null;
  }
  return normalizePresetProjectionRequest({
    scope,
    episode: typeof (request as { episode?: unknown }).episode === "number"
      ? (request as { episode: number }).episode
      : undefined,
    beat: typeof (request as { beat?: unknown }).beat === "number"
      ? (request as { beat: number }).beat
      : undefined,
    primary_slot: typeof (request as { primary_slot?: unknown }).primary_slot === "string"
      ? (request as { primary_slot: string }).primary_slot
      : undefined,
    asset_kind: typeof (request as { asset_kind?: unknown }).asset_kind === "string"
      ? (request as { asset_kind: string }).asset_kind
      : undefined,
    character: typeof (request as { character?: unknown }).character === "string"
      ? (request as { character: string }).character
      : undefined,
    identity_id: typeof (request as { identity_id?: unknown }).identity_id === "string"
      ? (request as { identity_id: string }).identity_id
      : undefined,
    asset_id: typeof (request as { asset_id?: unknown }).asset_id === "string"
      ? (request as { asset_id: string }).asset_id
      : undefined,
  });
}

function scopeFromProjectionKey(projectionKey: string): string | null {
  if (projectionKey.startsWith("beat:")) return "beat";
  if (projectionKey.startsWith("episode:")) return "episode";
  if (projectionKey.startsWith("asset:")) return "asset";
  if (projectionKey.startsWith("blank:")) return "blank";
  return null;
}

function parseBeatProjectionKey(projectionKey: string): { episode: number; beat: number } | null {
  const [, episodeRaw, beatRaw] = projectionKey.split(":");
  const episode = Number(episodeRaw);
  const beat = Number(beatRaw);
  if (!Number.isFinite(episode) || !Number.isFinite(beat)) return null;
  return { episode, beat };
}

function parseEpisodeProjectionKey(projectionKey: string): number | undefined {
  const [, episodeRaw] = projectionKey.split(":");
  const episode = Number(episodeRaw);
  return Number.isFinite(episode) ? episode : undefined;
}

function parseAssetProjectionKey(
  projectionKey: string,
): { asset_kind: string; asset_id: string } | null {
  const [, assetKind, ...assetParts] = projectionKey.split(":");
  const assetId = assetParts.join(":");
  if (!assetKind || !assetId) return null;
  return { asset_kind: assetKind, asset_id: assetId };
}

function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function stringOrUndefined(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}
