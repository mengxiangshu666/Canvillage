// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 「逐帧拉片」抽帧结果 → 画布节点的纯换算。
 *
 * `ExtractFramesDialog` 长期是个孤儿（只有它自己与一句注释提到它，没有任何真实入口），
 * 视频节点上只有「解析」那条路 —— 解析出的是**分镜表**（VideoStoryNode 的 16 列），
 * 而拉片出的是**帧本身**：能当参考图喂下游、能逐张挑。两条产物不同，所以两个入口都要有。
 *
 * 这里只放纯函数：把帧清单换成落节点要的参数（标签、比例），不碰 store ——
 * 落节点由 SelectedNodeOverlay 调用既有的 `addPanoCaptureGroup` 完成
 * （它就是「从源节点批量截一批图、宫格摆到右侧、连回源节点、算一步撤销」那条路，
 * 全景截图与导演世界导出都在用；抽帧与它们是同一类动作：从源节点截出来的图）。
 */

/** 抽帧结果里与落节点有关的那部分（对齐 `ExtractedFrame`）。 */
export interface ExtractedFrameLike {
  url: string;
  index: number;
  analysis?: {
    shot_type?: string;
    angle?: string;
    camera_movement?: string;
    subject_action?: string;
    mood?: string;
    color_tone?: string;
    suggested_prompt?: string;
  } | null;
}

/** 落成画布图片节点时要的一份素材。 */
export interface ExtractedFrameCapture {
  url: string;
  label: string;
  metadata?: Record<string, unknown>;
}

/** Requested capture position is provenance, not a measured frame timestamp. */
export function videoFrameCaptureMetadata(sourceNodeId: string, sourceVideoUrl: string, mode: 'first' | 'last' | 'current', requestedSeconds: number) {
  return {
    source_kind: 'video_frame_capture',
    source_node_id: sourceNodeId,
    source_video_url: sourceVideoUrl,
    capture_mode: mode,
    requested_seconds: Number.isFinite(requestedSeconds) && requestedSeconds >= 0 && requestedSeconds !== Number.MAX_SAFE_INTEGER ? requestedSeconds : null,
  };
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

/**
 * 视频节点上的 `videoUrl` 能不能直接喂给后端抽帧。
 *
 * `resolve_static_url_to_path` 只认项目内地址：`/static/...`、`/api/v1/projects/...`
 * 或项目相对路径。跨源 CDN / `blob:` / `data:` 地址会被它当成项目内相对路径解析，
 * 最终 400 或 404 —— 换句话说**不是我们这边判断保守，是后端确实吃不下**。
 * 这种视频就退回「本地选文件」，别让用户点了按钮才看到一句后端报错。
 *
 * 同源绝对地址会被折回 pathname 再交出去（后端同样只解析 path）。
 */
export function resolveExtractableVideoUrl(raw: string | null | undefined): string | null {
  const trimmed = typeof raw === 'string' ? raw.trim() : '';
  if (!trimmed) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) {
    let parsed: URL;
    try {
      parsed = new URL(trimmed);
    } catch {
      return null;
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    if (typeof window !== 'undefined' && parsed.origin !== window.location.origin) return null;
    return `${parsed.pathname}${parsed.search}`;
  }
  return trimmed;
}

/**
 * 抽帧出来的图与源视频同画幅，所以直接拿视频自己的像素尺寸约分成 `W:H`。
 *
 * 三个来源依次回落：节点的真实像素尺寸（最准，视频探测拿到过）→ 节点上已有的比例
 * 字段（只在它是 `W:H` 形式时可信）→ `16:9`。约分是为了让比例字符串尽量落在
 * 已知的画幅档位上（1920:1080 → 16:9），节点的自适应尺寸按这个字符串查表。
 */
export function resolveFrameAspectRatio(params: {
  widthPx?: number | null;
  heightPx?: number | null;
  fallback?: string | null;
}): string {
  const width = Number(params.widthPx);
  const height = Number(params.heightPx);
  if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
    const divisor = gcd(Math.round(width), Math.round(height)) || 1;
    return `${Math.round(width / divisor)}:${Math.round(height / divisor)}`;
  }

  const fallback = (params.fallback ?? '').trim();
  if (/^\d+(\.\d+)?:\d+(\.\d+)?$/.test(fallback)) {
    const [rawWidth, rawHeight] = fallback.split(':');
    const parsedWidth = Number(rawWidth);
    const parsedHeight = Number(rawHeight);
    if (parsedWidth > 0 && parsedHeight > 0) {
      const divisor = gcd(Math.round(parsedWidth), Math.round(parsedHeight)) || 1;
      return `${Math.round(parsedWidth / divisor)}:${Math.round(parsedHeight / divisor)}`;
    }
  }

  return '16:9';
}

/**
 * 帧清单 → 落节点素材。
 *
 * 打开镜头分析时，Vision 给的 `shot_type / angle` 会拼进节点标题 —— 一屏十几个
 * 「帧 #7」看不出哪张是特写，带上景别才选得动。完整分析存进 `captureMetadata`，
 * 下游要读（例如拿 `suggested_prompt` 当出图提示词）时还在。
 */
export function framesToCaptures(frames: readonly ExtractedFrameLike[]): ExtractedFrameCapture[] {
  return frames
    .filter((frame) => typeof frame.url === 'string' && frame.url.length > 0)
    .map((frame) => {
      const analysis = frame.analysis ?? null;
      const shotNumber = frame.index + 1;
      const shotType = analysis?.shot_type?.trim();
      const angle = analysis?.angle?.trim();
      const parts = [shotType, angle].filter((part): part is string => Boolean(part));
      const metadata: Record<string, unknown> = {
        source_kind: 'extracted_frame',
        frame_index: frame.index,
      };
      if (analysis) {
        metadata.shot_analysis = analysis;
        if (analysis.suggested_prompt) {
          metadata.suggested_prompt = analysis.suggested_prompt;
        }
      }
      return {
        url: frame.url,
        label: parts.length > 0 ? `帧 #${shotNumber} · ${parts.join(' / ')}` : `帧 #${shotNumber}`,
        metadata,
      };
    });
}

/**
 * `addPanoCaptureGroup` 只拿 captures 的 width/height 约分出画幅比例（`ratioOf`），
 * 不拿它定尺寸。所以这里按比例给一对合理的像素值就够了 —— 用 1920 作长边基准，
 * 约分后与传入的比例串一致。
 */
export function resolveFrameCaptureSize(aspectRatio: string): {
  width: number;
  height: number;
} {
  const [rawWidth = '16', rawHeight = '9'] = aspectRatio.split(':');
  const width = Number(rawWidth);
  const height = Number(rawHeight);
  if (!(width > 0) || !(height > 0)) {
    return { width: 1920, height: 1080 };
  }
  const longEdge = 1920;
  if (width >= height) {
    return { width: longEdge, height: Math.max(1, Math.round((longEdge * height) / width)) };
  }
  return { width: Math.max(1, Math.round((longEdge * width) / height)), height: longEdge };
}
