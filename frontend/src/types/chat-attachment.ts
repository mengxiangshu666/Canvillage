// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export type ChatAttachment = {
  id?: string;
  type?: string;
  kind?: string;
  mimeType?: string;
  fileName?: string;
  fileSize?: number;
  content?: string;
  url?: string;
  path?: string;
  label?: string;
  /** Canvas node id when this attachment is a real freezone node media reference. */
  nodeId?: string;
  /** paste | canvas_node | upload */
  source?: string;
};
