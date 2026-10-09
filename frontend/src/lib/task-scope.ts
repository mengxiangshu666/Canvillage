/**
 * Frontend mirror of the backend task-scope hashing in
 * `novelvideo/task_identity.py` + `novelvideo/task_scopes.py`.
 *
 * The backend derives a task's `scope` as:
 *   hashed_scope(label, json.dumps(config, sort_keys=True,
 *                                  ensure_ascii=False, separators=(",", ":")))
 *   -> `${label}__${sha1(payloadUtf8).hex.slice(0, 12)}`
 *
 * `useTaskController` reconciles a card against the live `/tasks` list by
 * comparing `key.scope` to the row's stored `scope`. That stored scope is the
 * hashed value above, so the FE reconcile key MUST reproduce the exact same
 * hash — a human-readable placeholder like `scene:大学宿舍:pano` never matches,
 * which silently drops the loading state after a refresh.
 */

import { sha1Hex } from "@/lib/sha1";

/**
 * Canonical JSON matching Python's
 * `json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.
 * Keys are sorted; `JSON.stringify` on each string yields the same escaping and
 * keeps non-ASCII characters raw (matching `ensure_ascii=False`).
 */
function canonicalJson(config: Record<string, string>): string {
  const keys = Object.keys(config).sort();
  const parts = keys.map((k) => `${JSON.stringify(k)}:${JSON.stringify(config[k])}`);
  return `{${parts.join(",")}}`;
}

/** Mirror of `task_config_scope(label, config)`. */
export function taskConfigScope(label: string, config: Record<string, string>): string {
  const payload = new TextEncoder().encode(canonicalJson(config));
  return `${label}__${sha1Hex(payload).slice(0, 12)}`;
}

/** Mirror of `scene_reference_asset_scope` — kinds: "master", "reverse". */
export function sceneReferenceAssetScope(sceneName: string, kind: string): string {
  return taskConfigScope("scene_ref", { scene: sceneName, kind });
}

/**
 * Mirror of `stage_asset_scope` — steps: "pano_from_master", "pano_from_text",
 * "single_face_sharp", "pano_sharp".
 */
export function stageAssetScope(sceneName: string, step: string): string {
  return taskConfigScope("stage_asset", { scene: sceneName, step });
}

/** Mirror of `prop_reference_asset_scope`. */
export function propReferenceAssetScope(propName: string): string {
  return taskConfigScope("prop_ref", { prop: propName });
}
