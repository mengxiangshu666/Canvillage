import { describe, expect, it } from 'vitest';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import {
  currentKeyframeReview,
  keyframeFingerprint,
} from './scriptKeyframeReviewReceipt';

function node(overrides: Record<string, unknown> = {}): CanvasNode {
  return {
    id: 'kf-1',
    type: 'imageGenNode',
    position: { x: 0, y: 0 },
    data: {
      imageUrl: '/static/kf-1.png',
      scriptShotKeyframeSourceNodeId: 'script-1',
      scriptShotKeyframeRowKey: 'S1',
      scriptShotKeyframeIndex: 0,
      ...overrides,
    },
  } as CanvasNode;
}

const pass = {
  state: 'PASS',
  note: '脸和领口与定妆一致，站位符合分镜',
  identityMatchesReference: true,
  matchesShotRequirement: true,
  imageUrl: '/static/kf-1.png',
  frameFingerprint: '',
  frameSha256: 'a'.repeat(64),
  referenceUrl: '/static/c01.png',
  referenceSha256: 'b'.repeat(64),
  reviewedAt: '2026-10-25T00:00:00Z',
};

describe('keyframe review receipt', () => {
  it('accepts a PASS bound to the current image, reference and fingerprint', () => {
    const target = node();
    const review = { ...pass, frameFingerprint: keyframeFingerprint(target) };
    expect(currentKeyframeReview(node({ scriptKeyframeReview: review }))).not.toBeNull();
  });

  it('invalidates a PASS when the image changes', () => {
    const target = node();
    const review = { ...pass, frameFingerprint: keyframeFingerprint(target) };
    expect(currentKeyframeReview(node({ imageUrl: '/static/new.png', scriptKeyframeReview: review }))).toBeNull();
  });

  it('invalidates a PASS without both hashes or observations', () => {
    const target = node();
    const review = { ...pass, frameFingerprint: keyframeFingerprint(target) };
    expect(currentKeyframeReview(node({ scriptKeyframeReview: { ...review, frameSha256: '' } }))).toBeNull();
    expect(currentKeyframeReview(node({ scriptKeyframeReview: { ...review, identityMatchesReference: false } }))).toBeNull();
    expect(currentKeyframeReview(node({ scriptKeyframeReview: { ...review, note: '' } }))).toBeNull();
  });

  it('requires a reason for REJECT and UNREVIEWED', () => {
    const target = node();
    const review = {
      ...pass,
      state: 'REJECT',
      note: '',
      frameFingerprint: keyframeFingerprint(target),
    };
    expect(currentKeyframeReview(node({ scriptKeyframeReview: review }))).toBeNull();
    expect(
      currentKeyframeReview(
        node({ scriptKeyframeReview: { ...review, note: '右手多一根手指' } }),
      ),
    ).not.toBeNull();
  });

  it('invalidates while the image is generating or failed', () => {
    const target = node();
    const review = { ...pass, frameFingerprint: keyframeFingerprint(target) };
    expect(currentKeyframeReview(node({ isGenerating: true, scriptKeyframeReview: review }))).toBeNull();
    expect(currentKeyframeReview(node({ generationError: '失败', scriptKeyframeReview: review }))).toBeNull();
  });
});
