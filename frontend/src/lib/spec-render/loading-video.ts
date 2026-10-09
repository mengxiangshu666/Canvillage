/**
 * 资源加载占位视频 URL。
 *
 * 使用 `new URL(..., import.meta.url)` 模式，让下游 bundler（webpack / turbopack /
 * vite）把 dist/assets/loading.mp4 正确识别为 asset 引用，拷到消费端产物目录。
 *
 * CJS 环境没有 `import.meta.url`，此时返回空字符串 —— `SpecRendererProvider` 会在
 * 读到空字符串时回退到 CSS 闪烁占位，不会破坏运行时。消费端也可以显式传
 * `loadingVideoUrl` 覆盖默认值。
 */
export const LOADING_VIDEO_URL: string = (() => {
  try {
    return new URL("./assets/loading.mp4", import.meta.url).href;
  } catch {
    return "";
  }
})();
