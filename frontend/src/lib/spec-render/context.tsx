"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { ImageCandidate } from "./modals/image-detail-modal";
import { LOADING_VIDEO_URL } from "./loading-video";

export type ToastLevel = "success" | "error" | "info";

export type SpecRendererContextValue = {
  /** 把内部路径（如 /static/{user}/{project}/xxx.png）解析为可访问 URL。未提供时原样返回。 */
  resolveMediaUrl?: (src: string) => Promise<string>;
  /**
   * 判断一个 src 是否需要解析，返回"内部路径"字符串表示需要解析（用作 key），
   * 返回 null 表示无需解析（直接使用）。未提供时所有 src 都不解析。
   */
  parseMediaUrl?: (src: string) => string | null;
  /** 用户从候选图中选图时的回调 */
  onCandidateSelect?: (candidate: ImageCandidate) => void;
  /** toast 提示回调 */
  onToast?: (message: string, level: ToastLevel) => void;
  /**
   * 资源加载过程中用作占位的视频 URL。
   * 未提供时默认使用包内打包的 loading.mp4；显式传 `""` 可强制关闭并回退到 CSS 闪烁。
   */
  loadingVideoUrl?: string;
};

const SpecRendererContext = createContext<SpecRendererContextValue>({
  loadingVideoUrl: LOADING_VIDEO_URL,
});

export function SpecRendererProvider({
  children,
  loadingVideoUrl,
  ...rest
}: SpecRendererContextValue & { children: ReactNode }) {
  const value: SpecRendererContextValue = {
    ...rest,
    loadingVideoUrl:
      loadingVideoUrl === undefined ? LOADING_VIDEO_URL : loadingVideoUrl,
  };
  return (
    <SpecRendererContext.Provider value={value}>
      {children}
    </SpecRendererContext.Provider>
  );
}

export function useSpecRendererContext(): SpecRendererContextValue {
  return useContext(SpecRendererContext);
}

export { LOADING_VIDEO_URL };
