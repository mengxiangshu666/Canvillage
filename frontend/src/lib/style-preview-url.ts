// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { p } from "@/lib/api-path";

// Bump whenever bundled previews are replaced. The API marks preset assets as
// immutable, so reusing the old token can leave an open browser showing the
// pre-replacement portrait/square image even after the file is corrected.
const STYLE_PREVIEW_ASSET_VERSION = "village-style-16x9-v3";

export function stylePreviewUrl(styleId: string, project?: string): string {
  const params = new URLSearchParams({ v: STYLE_PREVIEW_ASSET_VERSION });
  if (project) params.set("project", project);
  return `/${p`api/v1/styles/${styleId}/preview`}?${params.toString()}`;
}
