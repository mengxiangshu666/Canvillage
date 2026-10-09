import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  humanizeGenerationError,
  NodeGenerationErrorCard,
} from './NodeGenerationErrorCard';

describe('NodeGenerationErrorCard', () => {
  it('explains 524 before the generic timeout rule without suggesting result recovery', () => {
    expect(humanizeGenerationError('status_code: 524, body: {title: "Error 524: A timeout occurred"}'))
      .toBe('模型通道等待超时（524），请至少等 2 分钟后重试；反复失败请更换通道。');
  });
  it('turns raw provider failures into concise Chinese guidance', () => {
    expect(
      humanizeGenerationError(
        'direct image API HTTP 500',
        '{"error":{"message":"not supported model for image generation, only imagen models are supported"}}',
      ),
    ).toBe('当前模型不支持图片生成，请更换生图模型。');
    expect(humanizeGenerationError('HTTP 401 - Unauthorized')).toBe(
      '当前模型认证失败，请检查 API Key。',
    );
    expect(humanizeGenerationError('HTTP 404 - Not Found')).toBe(
      '当前提交路径或模型 ID 不存在，请检查模型配置。',
    );
  });

  it('keeps raw diagnostics collapsed and exposes real recovery actions', () => {
    const onRetry = vi.fn();
    const onChangeModel = vi.fn();
    render(
      <NodeGenerationErrorCard
        message="HTTP 403 - Forbidden"
        details='{"error":"permission denied"}'
        requestId="req-123"
        stage="submit"
        onRetry={onRetry}
        onChangeModel={onChangeModel}
      />,
    );

    expect(screen.getByText('当前渠道拒绝了本次请求，请检查模型权限或接口协议。')).toBeVisible();
    expect(screen.queryByText('{"error":"permission denied"}')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
    fireEvent.click(screen.getByRole('button', { name: '更换模型' }));
    fireEvent.click(screen.getByRole('button', { name: /查看详情/ }));

    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onChangeModel).toHaveBeenCalledTimes(1);
    expect(screen.getByText('{"error":"permission denied"}')).toBeVisible();
    expect(screen.getByText('req-123')).toBeVisible();
  });

  it('explains a relay safety wrapper as a relay problem, not a content verdict', () => {
    expect(
      humanizeGenerationError(
        'direct image API HTTP 400: {"error":{"message":"您的请求无法用于生成图像。该请求可能因安全政策被拦截，或不适合进行图像生成。","type":"invalid_request_error","param":"","code":400}}',
      ),
    ).toContain('不代表判定你的内容违规');

    // An explicit provider category is a real verdict and must not borrow the
    // reassuring relay wording.
    const explicit = humanizeGenerationError(
      'direct image API HTTP 400: {"error":{"code":"moderation_blocked","safety_violations":["sexual"]}}',
    );
    expect(explicit).toBe('上游内容审核明确拦截了这条请求。请调整画面描述或更换参考素材后重试。');
    expect(explicit).not.toContain('不代表判定你的内容违规');
  });

  it('renders the provider suggested action when the retry was already spent', () => {
    render(
      <NodeGenerationErrorCard
        message="direct image API HTTP 400: 您的请求无法用于生成图像。"
        details="direct image API HTTP 400: 您的请求无法用于生成图像。"
        stage="submit"
        suggestedAction="上游图像接口返回通用「安全政策」拒绝。已自动重试仍失败，可直接再点一次生成。"
        onRetry={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /查看详情/ }));
    expect(screen.getByText(/已自动重试仍失败/)).toBeVisible();
  });

  it('keeps the failure surface draggable while isolating real controls', () => {
    const onParentPointerDown = vi.fn();
    const onParentClick = vi.fn();
    const onRetry = vi.fn();
    render(
      <div onPointerDown={onParentPointerDown} onClick={onParentClick}>
        <NodeGenerationErrorCard
          message="HTTP 500"
          details="upstream failed"
          onRetry={onRetry}
        />
      </div>,
    );

    const surface = screen.getByTestId('node-generation-error-card');
    const retryButton = screen.getByRole('button', { name: '重新生成' });

    expect(surface).not.toHaveClass('nodrag');
    fireEvent.pointerDown(surface);
    expect(onParentPointerDown).toHaveBeenCalledTimes(1);

    fireEvent.pointerDown(retryButton);
    fireEvent.click(retryButton);
    expect(onParentPointerDown).toHaveBeenCalledTimes(1);
    expect(onParentClick).not.toHaveBeenCalled();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('explains a disabled retry instead of leaving a dead button', () => {
    render(
      <NodeGenerationErrorCard
        message="HTTP 400"
        retryDisabled
        retryDisabledReason="当前模型的当前模式最多支持 1 张图片"
        onRetry={vi.fn()}
      />,
    );

    const retry = screen.getByRole('button', { name: '重新生成' });
    expect(retry).toBeDisabled();
    expect(retry).toHaveAttribute('title', '当前模型的当前模式最多支持 1 张图片');
    expect(screen.getByText('当前模型的当前模式最多支持 1 张图片')).toBeVisible();
  });

  it('separates re-fetching an existing provider task from generating again', () => {
    const onRecover = vi.fn();
    const onRetry = vi.fn();
    render(
      <NodeGenerationErrorCard
        message="上游视频任务失败，但没有返回失败原因"
        stage="query"
        onRecover={onRecover}
        onRetry={onRetry}
      />,
    );

    const recover = screen.getByRole('button', { name: '重新获取' });
    expect(recover).toHaveAttribute('title', '重新获取已有结果，不会重新生成');
    fireEvent.click(recover);
    expect(onRecover).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '重新生成' })).toBeVisible();
  });

  it('dismisses through a real control without bubbling into the canvas node', () => {
    const onParentPointerDown = vi.fn();
    const onParentClick = vi.fn();
    const onDismiss = vi.fn();
    render(
      <div onPointerDown={onParentPointerDown} onClick={onParentClick}>
        <NodeGenerationErrorCard
          message="provider failed"
          onDismiss={onDismiss}
        />
      </div>,
    );

    const dismiss = screen.getByRole('button', { name: '关闭失败提示' });
    fireEvent.pointerDown(dismiss);
    fireEvent.click(dismiss);

    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onParentPointerDown).not.toHaveBeenCalled();
    expect(onParentClick).not.toHaveBeenCalled();
  });
});
