import type { Detection } from "@mediapipe/tasks-vision";

import type { NormalizedRegionRect } from "./expressionRegionSelection";

let detectorPromise: Promise<import("@mediapipe/tasks-vision").FaceDetector> | null = null;

export function detectionToNormalizedRegion(
  detection: Pick<Detection, "boundingBox">,
  naturalWidth: number,
  naturalHeight: number,
): NormalizedRegionRect | null {
  const box = detection.boundingBox;
  if (!box || naturalWidth <= 0 || naturalHeight <= 0) return null;
  // Expand a face box into a practical head-selection box like the reference UI.
  const padX = box.width * 0.18;
  const padTop = box.height * 0.28;
  const padBottom = box.height * 0.16;
  const x = Math.max(0, box.originX - padX) / naturalWidth;
  const y = Math.max(0, box.originY - padTop) / naturalHeight;
  const right = Math.min(naturalWidth, box.originX + box.width + padX) / naturalWidth;
  const bottom = Math.min(naturalHeight, box.originY + box.height + padBottom) / naturalHeight;
  return {
    x,
    y,
    width: Math.max(0.035, right - x),
    height: Math.max(0.035, bottom - y),
  };
}

async function getFaceDetector() {
  if (!detectorPromise) {
    detectorPromise = import("@mediapipe/tasks-vision").then(async ({ FaceDetector, FilesetResolver }) => {
      const vision = await FilesetResolver.forVisionTasks("/mediapipe/wasm");
      return FaceDetector.createFromOptions(vision, {
        baseOptions: {
          modelAssetPath: "/mediapipe/blaze_face_short_range.tflite",
          delegate: "CPU",
        },
        runningMode: "IMAGE",
        minDetectionConfidence: 0.45,
        minSuppressionThreshold: 0.3,
      });
    });
  }
  return detectorPromise;
}

export async function detectFaceRegions(image: HTMLImageElement): Promise<NormalizedRegionRect[]> {
  if (!image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0) return [];
  const detector = await getFaceDetector();
  const result = detector.detect(image);
  return result.detections
    .map((detection) => detectionToNormalizedRegion(detection, image.naturalWidth, image.naturalHeight))
    .filter((region): region is NormalizedRegionRect => Boolean(region))
    .slice(0, 12);
}
