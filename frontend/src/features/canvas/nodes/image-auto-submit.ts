export type ImageAutoSubmitDecision = 'idle' | 'wait' | 'server-owned' | 'submit';

export function resolveImageAutoSubmitDecision(input: {
  queued: boolean;
  isGenerating: boolean;
  imageModelsLoading: boolean;
  hasAuthoritativeImageModel: boolean;
  submitDisabled: boolean;
  hasServerTask: boolean;
}): ImageAutoSubmitDecision {
  if (!input.queued) return 'idle';
  if (input.hasServerTask) return 'server-owned';
  if (
    input.isGenerating
    || input.imageModelsLoading
    || !input.hasAuthoritativeImageModel
    || input.submitDisabled
  ) {
    return 'wait';
  }
  return 'submit';
}
