const RESEARCH_TOGGLE_PREFIX = "village-canvas:agent-research:";

function researchToggleKey(projectId?: string, canvasId?: string): string {
  return `${RESEARCH_TOGGLE_PREFIX}${encodeURIComponent(projectId?.trim() || "home")}:${encodeURIComponent(canvasId?.trim() || "default")}`;
}

export function loadAigcResearchEnabled(projectId?: string, canvasId?: string): boolean {
  try {
    return localStorage.getItem(researchToggleKey(projectId, canvasId)) === "true";
  } catch {
    return false;
  }
}

export function saveAigcResearchEnabled(
  projectId: string | undefined,
  canvasId: string | undefined,
  enabled: boolean,
): void {
  try {
    localStorage.setItem(researchToggleKey(projectId, canvasId), enabled ? "true" : "false");
  } catch {
    // The toggle remains usable for the current render if storage is unavailable.
  }
}
