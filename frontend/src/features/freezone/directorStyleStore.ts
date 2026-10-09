// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from "zustand";
import { persist } from "zustand/middleware";

import {
  DEFAULT_DIRECTOR_CONTROLS,
  type DirectorControls,
} from "./directorPromptEngine";

interface DirectorStyleState extends DirectorControls {
  activeScope: string;
  scopedControls: Record<string, DirectorControls>;
  activateScope: (projectId: string, canvasId: string) => void;
  update: (patch: Partial<DirectorControls>) => void;
  reset: () => void;
}

const DEFAULT_SCOPE = "__default__";

function directorControlsFromState(state: DirectorStyleState): DirectorControls {
  return {
    styleId: state.styleId,
    shot: state.shot,
    lens: state.lens,
    lighting: state.lighting,
    paletteOverride: state.paletteOverride,
    textureOverride: state.textureOverride,
    identityLock: state.identityLock,
    continuityLock: state.continuityLock,
  };
}

function directorScopeKey(projectId: string, canvasId: string): string {
  const project = projectId.trim() || "__project__";
  const canvas = canvasId.trim() || "default";
  return `${project}::${canvas}`;
}

export const useDirectorStyleStore = create<DirectorStyleState>()(
  persist(
    (set) => ({
      ...DEFAULT_DIRECTOR_CONTROLS,
      activeScope: DEFAULT_SCOPE,
      scopedControls: {},
      activateScope: (projectId, canvasId) => set((state) => {
        const nextScope = directorScopeKey(projectId, canvasId);
        if (state.activeScope === nextScope) return state;
        const next = state.scopedControls[nextScope] ?? DEFAULT_DIRECTOR_CONTROLS;
        return { ...next, activeScope: nextScope };
      }),
      update: (patch) => set((state) => {
        const next = { ...directorControlsFromState(state), ...patch };
        return {
          ...next,
          scopedControls: {
            ...state.scopedControls,
            [state.activeScope]: next,
          },
        };
      }),
      reset: () => set((state) => ({
        ...DEFAULT_DIRECTOR_CONTROLS,
        scopedControls: {
          ...state.scopedControls,
          [state.activeScope]: DEFAULT_DIRECTOR_CONTROLS,
        },
      })),
    }),
    { name: "village-canvas-personal-director-style-v2" },
  ),
);

export function currentDirectorControls(projectId?: string, canvasId?: string): DirectorControls {
  const state = useDirectorStyleStore.getState();
  if (projectId !== undefined || canvasId !== undefined) {
    const scoped = state.scopedControls[directorScopeKey(projectId ?? "", canvasId ?? "default")];
    return scoped ? { ...scoped } : { ...DEFAULT_DIRECTOR_CONTROLS };
  }
  return directorControlsFromState(state);
}
