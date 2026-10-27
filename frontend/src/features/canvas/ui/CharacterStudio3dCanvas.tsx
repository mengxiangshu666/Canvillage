// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";

import type {
  StudioBodyProportions,
  StudioHairSpec,
  StudioOutfitSpec,
} from "@/features/canvas/domain/characterStudio3dSpecs";
import {
  createCharacterStudioApp,
  type CharacterStudioApp,
} from "@/features/viewer-kit/three-d/characterStudio/studioApp";

export interface CharacterStudio3dHandle {
  /** 把当前 3D 画面截成 PNG data URL（应用到当前节点用）。 */
  capture(): string | null;
}

export interface CharacterStudio3dCanvasProps {
  hair: StudioHairSpec | null;
  outfit: StudioOutfitSpec | null;
  body: StudioBodyProportions;
  /** WebGL / 白模不可用时，让上层切回图片预览。 */
  onUnsupported?: (reason: string) => void;
  /** 全幅模式：铺满父容器、去边框圆角，控制钮改成视口内浮层（编辑器版式用）。 */
  bare?: boolean;
}

/**
 * 3D 白模试衣画布。
 *
 * PlayCanvas 只在打开 3D 页签时动态加载，避免把 3D 引擎并进画布主包；
 * 引擎实例与 React 生命周期分开管理，卸载时一并销毁 GPU 资源。
 */
export const CharacterStudio3dCanvas = forwardRef<
  CharacterStudio3dHandle,
  CharacterStudio3dCanvasProps
>(function CharacterStudio3dCanvas(
  { hair, outfit, body, onUnsupported, bare },
  ref,
) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const appRef = useRef<CharacterStudioApp | null>(null);
  const lookRef = useRef({ hair, outfit, body });
  lookRef.current = { hair, outfit, body };
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [autoRotate, setAutoRotate] = useState(true);
  const autoRotateRef = useRef(autoRotate);
  autoRotateRef.current = autoRotate;

  useImperativeHandle(
    ref,
    () => ({
      capture() {
        const canvas = canvasRef.current;
        if (!canvas || status !== "ready") return null;
        try {
          return canvas.toDataURL("image/png");
        } catch {
          return null;
        }
      },
    }),
    [status],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let cancelled = false;
    setStatus("loading");
    setError("");

    void Promise.resolve()
      .then(() =>
        createCharacterStudioApp({
          canvas,
          look: { ...lookRef.current, autoRotate: autoRotateRef.current },
        }),
      )
      .then((created) => {
        if (cancelled) {
          created.dispose();
          return;
        }
        appRef.current = created;
        created.update({ ...lookRef.current, autoRotate: autoRotateRef.current });
        setStatus("ready");
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        const message =
          reason instanceof Error ? reason.message : "3D 预览加载失败";
        setStatus("error");
        setError(message);
        onUnsupported?.(message);
      });

    return () => {
      cancelled = true;
      appRef.current?.dispose();
      appRef.current = null;
    };
    // 引擎只初始化一次；外观变化走下面的 update。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const lookKey = JSON.stringify({ hair, outfit, body, autoRotate });
  useEffect(() => {
    appRef.current?.update({ hair, outfit, body, autoRotate });
    // lookKey 是 hair/outfit/body/autoRotate 的稳定序列化。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookKey]);

  const viewControls = (
    <>
      <button
        type="button"
        onClick={() => setAutoRotate((value) => !value)}
        aria-pressed={autoRotate}
        className={`h-7 rounded-md border px-2.5 transition-colors ${
          autoRotate
            ? "border-white/25 bg-white/[0.1] text-white/85"
            : "border-white/10 text-white/45 hover:text-white/75"
        }`}
      >
        自动旋转
      </button>
      <button
        type="button"
        onClick={() => appRef.current?.resetView()}
        className="inline-flex h-7 items-center gap-1 rounded-md border border-white/10 px-2.5 text-white/45 transition-colors hover:bg-white/5 hover:text-white/75"
      >
        <RotateCcw className="size-3" />
        重置视角
      </button>
    </>
  );

  return (
    <div className="relative flex h-full min-h-0 w-full flex-col">
      <div
        className={`relative min-h-0 flex-1 overflow-hidden bg-[radial-gradient(circle_at_50%_38%,rgba(255,255,255,0.09),transparent_58%),linear-gradient(180deg,rgba(255,255,255,0.03),rgba(0,0,0,0.25))] ${
          bare ? "" : "rounded-[14px] border border-white/[0.08]"
        }`}
      >
        <canvas
          ref={canvasRef}
          className="size-full touch-none"
          aria-label="3D 白模试衣预览"
        />
        {status !== "ready" ? (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-[12px] text-white/45">
            {status === "loading" ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                正在加载 3D 白模…
              </>
            ) : (
              <>
                <span>3D 预览不可用</span>
                <span className="max-w-[70%] text-center text-[10.5px] leading-4 text-white/30">
                  {error}
                </span>
              </>
            )}
          </div>
        ) : null}
        {bare ? (
          <div className="absolute bottom-2.5 left-2.5 z-10 flex items-center gap-1.5">
            {viewControls}
            <span className="rounded-full bg-black/35 px-2 py-0.5 text-[10px] text-white/40 backdrop-blur-sm">
              拖动旋转 · 滚轮缩放 · 服装与发型为替身低模
            </span>
          </div>
        ) : null}
      </div>

      {bare ? null : (
        <div className="mt-3 flex flex-wrap items-center justify-center gap-2 text-[11px] text-white/38">
          {viewControls}
          <span className="text-[10.5px] text-white/25">
            拖动旋转 · 滚轮缩放 · 服装与发型为替身低模
          </span>
        </div>
      )}
    </div>
  );
});
