import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('canvas viewport virtualization contract', () => {
  it('keeps React Flow virtualization enabled while low-detail LOD is active', () => {
    const source = readFileSync(
      resolve(process.cwd(), 'src/features/canvas/Canvas.tsx'),
      'utf8',
    );

    expect(source).toContain(
      'onlyRenderVisibleElements={CANVAS_ONLY_RENDER_VISIBLE_ELEMENTS}',
    );
    expect(source).not.toContain('onlyRenderVisibleElements={!lowDetailActive}');
  });
});
