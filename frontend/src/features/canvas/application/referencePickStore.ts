// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Viewport } from '@xyflow/react';
import { create } from 'zustand';

import type { CanvasNodeType } from '../domain/canvasNodes';
import type { ReferencePickCandidate } from './referencePick';

export interface ReferencePickRequest {
  targetNodeId: string;
  targetNodeType: CanvasNodeType;
  originViewport: Viewport | null;
  candidates: Map<string, ReferencePickCandidate>;
  rejections: Map<string, string>;
}

interface ReferencePickState {
  request: ReferencePickRequest | null;
  start: (request: ReferencePickRequest) => void;
  stop: () => void;
}

export const useReferencePickStore = create<ReferencePickState>((set) => ({
  request: null,
  start: (request) => set({ request }),
  stop: () => set({ request: null }),
}));

