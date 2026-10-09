// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * Whether a direct model may enter a canvas execution surface.
 *
 * The backend owns this decision: `/model-gateway/config` returns `usable`
 * plus the reason per row, so the picker, the Agent projection and the model
 * center all render one verdict.  The legacy field-by-field derivation stays
 * only as a fallback for payloads produced before that verdict existed.
 */
export interface DirectModelReadinessFields {
  enabled?: boolean;
  configured?: boolean;
  disabled?: boolean;
  usable?: boolean;
  usableReason?: string;
  runtimeReady?: boolean;
  runtimeProbeRequired?: boolean;
  runtimeProbeComplete?: boolean;
}

export function isRunnableDirectModel(
  model: DirectModelReadinessFields,
): boolean {
  if (typeof model.usable === 'boolean') {
    return model.usable;
  }

  if (
    model.enabled !== true
    || model.configured !== true
    || model.disabled === true
    || model.runtimeReady !== true
  ) {
    return false;
  }

  return model.runtimeProbeRequired !== true || model.runtimeProbeComplete === true;
}
