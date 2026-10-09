// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from "zustand";

export type NodePowerTool = "expression";

interface NodePowerHubState {
  activeTool: NodePowerTool | null;
  nodeId: string | null;
  open: (tool: NodePowerTool, nodeId: string) => void;
  close: () => void;
}

export const useNodePowerHubStore = create<NodePowerHubState>((set) => ({
  activeTool: null,
  nodeId: null,
  open: (activeTool, nodeId) => set({ activeTool, nodeId }),
  close: () => set({ activeTool: null, nodeId: null }),
}));
