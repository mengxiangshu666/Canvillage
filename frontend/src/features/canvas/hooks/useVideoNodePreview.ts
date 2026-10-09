import { useEffect, useMemo, useSyncExternalStore } from "react";
import { useStore } from "@xyflow/react";

import { isLowDetailZoom } from "@/features/canvas/application/canvasLod";
import {
  getLodStill,
  requestLodStill,
  subscribeLodStills,
} from "@/features/canvas/application/videoFrameCapture";
import { resolveImageDisplayUrl } from "@/features/canvas/application/imageData";

export function useVideoNodePreview(
  videoUrl: unknown,
  transientPreviewUrl: string | null,
  upstreamPreviewUrl: string | null,
) {
  const videoSource = useMemo(() => {
    if (typeof videoUrl === "string" && videoUrl) {
      return resolveImageDisplayUrl(videoUrl);
    }
    if (transientPreviewUrl) return transientPreviewUrl;
    if (upstreamPreviewUrl) return resolveImageDisplayUrl(upstreamPreviewUrl);
    return null;
  }, [videoUrl, transientPreviewUrl, upstreamPreviewUrl]);

  const lowDetailZoom = useStore(
    (state: { transform: [number, number, number] }) =>
      isLowDetailZoom(state.transform[2]),
  );
  const lodStill = useSyncExternalStore(
    subscribeLodStills,
    () => getLodStill(videoSource),
    () => null,
  );

  useEffect(() => {
    requestLodStill(videoSource);
  }, [videoSource]);

  const videoPosterSource = useMemo(() => {
    if (!videoSource) return null;
    return videoSource.includes("#t=") ? videoSource : `${videoSource}#t=0.1`;
  }, [videoSource]);

  return { videoSource, lowDetailZoom, lodStill, videoPosterSource };
}
