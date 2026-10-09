// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

function read(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), 'utf8');
}

describe('canvas actions are operational or explicitly disabled', () => {
  it('does not leave text-node submit logging in place of an operation', () => {
    const source = read('src/features/canvas/nodes/TextAnnotationNode.tsx');

    expect(source).not.toContain('[text-node] submit stub');
    expect(source).toContain("void runTextToVideo()");
    expect(source).toContain("void runImageToPrompt()");
  });

  it('disables unavailable video actions instead of emitting fake action logs', () => {
    const source = read('src/features/canvas/ui/NodeActionToolbar.tsx');

    expect(source).not.toContain('handleVideoStub');
    expect(source).not.toContain('stub action triggered');
    expect(source).not.toContain('stubButtonClass');
    expect(source).toContain('disabled={!hasVideo}');
    expect(source).toContain('disabled={!hasVideo || isAnalyzing}');
    expect(source).toContain('disabled={!hasVideo || isSeparatingAv}');
  });
});
