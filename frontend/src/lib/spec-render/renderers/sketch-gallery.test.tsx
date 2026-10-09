import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { Spec } from '../spec';
import { sketchGalleryRenderer } from './sketch-gallery';

describe('sketch gallery layout', () => {
  it.each(['Image', 'Gallery'])('marks the %s media grid without inspecting inline styles', (type) => {
    const spec: Spec = {
      type: 'sketch_gallery', root: 'root',
      elements: {
        root: { type: 'Card', props: { title: 'Fixture' }, children: ['media'] },
        media: {
          type,
          props: type === 'Image'
            ? { src: '/fixture.png', alt: 'Fixture image' }
            : { images: [{ src: '/fixture.png', alt: 'Fixture image' }] },
        },
      },
    };
    const html = renderToStaticMarkup(sketchGalleryRenderer({
      spec, context: { spec, rendererKey: 'sketch_gallery' },
    }));
    const root = document.createElement('div');
    root.innerHTML = html;
    const grid = root.querySelector('.jr-sketch-gallery-grid');
    expect(grid).not.toBeNull();
    expect(grid?.querySelector('.jr-tilt-card')).not.toBeNull();
    expect(grid?.querySelector('img')?.getAttribute('alt')).toBe('Fixture image');
  });
});
