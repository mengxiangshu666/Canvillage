export type AgentComposerPrimaryAction = "send" | "steer" | "stop";

export function agentComposerPrimaryAction({
  busy,
  canvasProduct,
}: {
  busy: boolean;
  canvasProduct: boolean;
}): AgentComposerPrimaryAction {
  if (!busy) return "send";
  return canvasProduct ? "steer" : "stop";
}

export function agentComposerPrimaryActionDisabled(
  action: AgentComposerPrimaryAction,
  canSend: boolean,
): boolean {
  return action === "stop" ? false : !canSend;
}

export function agentComposerCanSteer({
  text,
  attachmentCount,
  pinnedNodeCount,
}: {
  text: string;
  attachmentCount: number;
  pinnedNodeCount: number;
}): boolean {
  return text.trim().length > 0 && attachmentCount === 0 && pinnedNodeCount === 0;
}
