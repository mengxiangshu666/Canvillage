import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('canvas viewport virtualization contract', () => {
  it('keeps React Flow virtualization enabled while low-detail LOD is active', () => {
    // The ReactFlow element now lives in CanvasFlowSurface and receives its props
    // from Canvas; the virtualization contract itself is unchanged.
    const source = readFileSync(
      resolve(process.cwd(), 'src/features/canvas/Canvas.tsx'),
      'utf8',
    );

    expect(source).toContain(
      'onlyRenderVisibleElements: CANVAS_ONLY_RENDER_VISIBLE_ELEMENTS,',
    );
    expect(source).not.toContain('onlyRenderVisibleElements: !lowDetail');
    expect(source).not.toContain('onlyRenderVisibleElements={!lowDetailActive}');
  });

  it('keeps the full nodes/edges subscription out of the Canvas shell', () => {
    // Regression guard for the render-topology refactor: React Flow replaces
    // both arrays on every drag frame, so a full-array subscription in the 5k
    // line Canvas body would re-run every panel on every frame again.
    const canvas = readFileSync(
      resolve(process.cwd(), 'src/features/canvas/Canvas.tsx'),
      'utf8',
    );
    const flowSurface = readFileSync(
      resolve(process.cwd(), 'src/features/canvas/CanvasFlowSurface.tsx'),
      'utf8',
    );

    expect(canvas).not.toContain('useCanvasStore((state) => state.nodes)');
    expect(canvas).not.toContain('useCanvasStore((state) => state.edges)');
    expect(flowSurface).toContain('useCanvasStore((state) => state.nodes)');
    expect(flowSurface).toContain('useCanvasStore((state) => state.edges)');
  });
});
