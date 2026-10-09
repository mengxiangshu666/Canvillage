import { useStore } from '@xyflow/react';

import { nodeBodyRequiredEdge } from '@/features/canvas/application/imageData';
import { MEDIA_VARIANT_MAX_EDGE, pickMediaVariant, type MediaVariant } from '@/lib/media-url';
import { useDevicePixelRatio } from '@/lib/useDevicePixelRatio';

export function useNodeBodyVariant(display: { width: number; height: number }): MediaVariant | null {
  const devicePixelRatio = useDevicePixelRatio();
  return useStore((state) =>
    pickMediaVariant(nodeBodyRequiredEdge(display, state.transform[2], devicePixelRatio)),
  );
}

export function useNodeBodyVariantBudget(display: { width: number; height: number }): number {
  const variant = useNodeBodyVariant(display);
  return variant === null ? Number.POSITIVE_INFINITY : MEDIA_VARIANT_MAX_EDGE[variant];
}
