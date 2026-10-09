// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * This checkout is the standalone Village Infinite Canvas product.
 * Product identity is a source invariant, not a build-time switch: a wrong
 * Vite mode must never silently load another product shell or API target.
 */
export const canvasOnlyProduct = true;
export const appBrandName = "村长无限画布";
