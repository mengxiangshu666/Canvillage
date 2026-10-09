// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
const VILLAGE_AGENT_COMPANION_EDGE_OVERLAP = 42;
const VILLAGE_AGENT_COMPANION_TOP_OFFSET = 52;
const VILLAGE_AGENT_COMPANION_MARGIN = 8;
const VILLAGE_AGENT_COMPANION_WIDTH = 66;
const VILLAGE_AGENT_COMPANION_HEIGHT = 80;

export function resolveVillageAgentCompanionAnchor(
  frame: Pick<DOMRect, "left" | "top">,
  viewport: { width: number; height: number },
): { left: number; top: number } {
  const maxLeft = Math.max(
    VILLAGE_AGENT_COMPANION_MARGIN,
    viewport.width - VILLAGE_AGENT_COMPANION_WIDTH - VILLAGE_AGENT_COMPANION_MARGIN,
  );
  const maxTop = Math.max(
    58,
    viewport.height - VILLAGE_AGENT_COMPANION_HEIGHT - VILLAGE_AGENT_COMPANION_MARGIN,
  );
  return {
    left: Math.round(Math.min(
      Math.max(VILLAGE_AGENT_COMPANION_MARGIN, frame.left - VILLAGE_AGENT_COMPANION_EDGE_OVERLAP),
      maxLeft,
    )),
    top: Math.round(Math.min(
      Math.max(58, frame.top + VILLAGE_AGENT_COMPANION_TOP_OFFSET),
      maxTop,
    )),
  };
}
