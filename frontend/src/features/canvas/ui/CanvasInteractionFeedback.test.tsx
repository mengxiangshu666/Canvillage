// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it } from 'vitest';

import { CanvasInteractionFeedback, createClickBubbles } from './CanvasInteractionFeedback';

function FeedbackHarness() {
  const ref = createRef<HTMLDivElement>();
  return (
    <div ref={ref}>
      <div className="react-flow" data-testid="flow">
        <div className="react-flow__pane" data-testid="pane" />
        <div className="react-flow__background" data-testid="background" />
        <div className="react-flow__node" data-testid="node" />
      </div>
      <button type="button" data-testid="control">Control</button>
      <CanvasInteractionFeedback containerRef={ref} />
    </div>
  );
}

describe('CanvasInteractionFeedback', () => {
  it('creates the white-dot burst across blank canvas surfaces only after a real click', () => {
    const { container, getByTestId } = render(<FeedbackHarness />);

    fireEvent.pointerDown(getByTestId('control'), {
      button: 0,
      pointerId: 1,
      clientX: 10,
      clientY: 10,
    });
    fireEvent.pointerUp(getByTestId('control'), { pointerId: 1, clientX: 10, clientY: 10 });
    expect(container.querySelector('.village-canvas-click-burst')).toBeNull();

    fireEvent.pointerDown(getByTestId('background'), {
      button: 0,
      pointerId: 2,
      clientX: 30,
      clientY: 40,
    });
    expect(container.querySelector('.village-canvas-click-burst')).toBeNull();
    fireEvent.pointerUp(getByTestId('background'), {
      pointerId: 2,
      clientX: 31,
      clientY: 41,
    });
    const burst = container.querySelector('.village-canvas-click-burst');
    expect(burst).not.toBeNull();
    expect(burst?.children.length).toBeGreaterThanOrEqual(7);
    expect(burst?.children.length).toBeLessThanOrEqual(9);
    const bubbleSize = Number.parseFloat(
      (burst?.children[0] as HTMLElement).style.getPropertyValue('--bubble-size'),
    );
    expect(bubbleSize).toBeGreaterThanOrEqual(5);
    expect(bubbleSize).toBeLessThanOrEqual(8);

    fireEvent.pointerDown(getByTestId('node'), {
      button: 0,
      pointerId: 3,
      clientX: 50,
      clientY: 50,
    });
    fireEvent.pointerUp(getByTestId('node'), { pointerId: 3, clientX: 50, clientY: 50 });
    expect(container.querySelectorAll('.village-canvas-click-burst')).toHaveLength(1);
  });

  it('does not fire the click burst after a canvas drag', () => {
    const { container, getByTestId } = render(<FeedbackHarness />);
    fireEvent.pointerDown(getByTestId('pane'), {
      button: 0,
      pointerId: 4,
      clientX: 20,
      clientY: 20,
    });
    fireEvent.pointerUp(getByTestId('pane'), {
      pointerId: 4,
      clientX: 60,
      clientY: 60,
    });
    expect(container.querySelector('.village-canvas-click-burst')).toBeNull();
  });

  it('creates a substantial, varied burst for each click', () => {
    const first = createClickBubbles(() => 0.2);
    const second = createClickBubbles(() => 0.8);

    expect(first.length).toBe(7);
    expect(second.length).toBe(9);
    expect(first.every((bubble) => bubble.size >= 5 && bubble.size <= 8)).toBe(true);
    expect(first.every((bubble) => bubble.delay >= 0 && bubble.delay <= 8)).toBe(true);
    expect(first.every((bubble) => bubble.opacity >= 0.8 && bubble.opacity <= 0.96)).toBe(true);
    expect(first).not.toEqual(second);
    expect(first.some((bubble) => Math.hypot(bubble.x, bubble.y) >= 16)).toBe(true);
  });
});
