"use client";

import { useEffect, useRef, useState } from "react";
import { Download, X } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "../dialog";

function useVideoPoster(
  src?: string,
  explicitPoster?: string,
): string | undefined {
  const [poster, setPoster] = useState<string | undefined>(explicitPoster);
  const attempted = useRef(false);

  useEffect(() => {
    if (explicitPoster) {
      setPoster(explicitPoster);
      return;
    }
    if (!src || attempted.current) return;
    attempted.current = true;

    const video = document.createElement("video");
    video.crossOrigin = "anonymous";
    video.muted = true;
    video.preload = "metadata";
    video.src = src;

    video.addEventListener("loadeddata", () => {
      video.currentTime = 0.1;
    });

    video.addEventListener("seeked", () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.drawImage(video, 0, 0);
          setPoster(canvas.toDataURL("image/jpeg", 0.8));
        }
      } catch {
        /* ignore */
      }
    });
  }, [src, explicitPoster]);

  return poster;
}

export type VideoDetailSection = {
  title: string;
  body?: string;
  items?: string[];
};

type VideoDetailModalProps = {
  src?: string;
  poster?: string;
  title?: string;
  description?: string;
  sections?: VideoDetailSection[];
  open: boolean;
  setOpen: (open: boolean) => void;
};

function deriveFileName(url: string): string {
  try {
    const pathname = new URL(url, window.location.href).pathname;
    const name = pathname.split("/").pop()?.trim();
    return name || "video";
  } catch {
    return "video";
  }
}

async function triggerDownload(url: string): Promise<void> {
  try {
    const response = await fetch(url, { mode: "cors" });
    if (!response.ok) {
      throw new Error(`download failed (${response.status})`);
    }
    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = blobUrl;
    link.download = deriveFileName(url);
    link.rel = "noopener noreferrer";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1_000);
    return;
  } catch {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

export function VideoDetailModal({
  src,
  poster,
  title,
  description,
  sections = [],
  open,
  setOpen,
}: VideoDetailModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [isDownloading, setIsDownloading] = useState(false);

  const mediaSrc = src?.trim() ? src : undefined;
  const autoPoster = useVideoPoster(mediaSrc, poster?.trim() || undefined);
  const posterSrc = autoPoster ?? (poster?.trim() || undefined);
  const downloadSrc = mediaSrc ?? posterSrc ?? "";
  const isVideoMedia =
    typeof mediaSrc === "string" &&
    /\.(mp4|webm|mov|m4v|ogg)(\?.*)?$/i.test(mediaSrc);
  const normalizedSections =
    sections.length > 0
      ? sections
      : description
        ? [{ title: "视频说明", body: description }]
        : [];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        showCloseButton={false}
        className="fixed inset-0 top-0 left-0 flex h-screen w-screen max-w-none translate-x-0 translate-y-0 items-center justify-center rounded-none border-none bg-black/20 p-0 text-white backdrop-blur-xl sm:max-w-none"
      >
        <DialogTitle className="sr-only">视频详情</DialogTitle>

        <div className="absolute right-8 top-6 z-50 flex items-center gap-6">
          <button
            type="button"
            className="text-white/40 transition hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!downloadSrc || isDownloading}
            onClick={async () => {
              if (!downloadSrc) return;
              setIsDownloading(true);
              try {
                await triggerDownload(downloadSrc);
              } finally {
                setIsDownloading(false);
              }
            }}
            aria-label="下载视频"
          >
            <Download className="h-6 w-6" />
          </button>
          <DialogClose className="text-white/40 transition hover:text-white outline-none">
            <X className="h-7 w-7" />
          </DialogClose>
        </div>

        <div className="flex w-full max-w-6xl justify-center p-6">
          <div className="grid w-full max-w-5xl grid-cols-[360px_minmax(0,1fr)] items-center gap-16">
            <div className="relative overflow-hidden rounded-[28px] bg-black/40 shadow-[0_30px_80px_rgba(0,0,0,0.45)]">
              {isVideoMedia && mediaSrc ? (
                <video
                  ref={videoRef}
                  className="h-[640px] w-full object-cover"
                  src={mediaSrc}
                  {...(posterSrc ? { poster: posterSrc } : {})}
                  controls
                  playsInline
                  preload="auto"
                />
              ) : posterSrc ? (
                <img
                  className="h-[640px] w-full object-cover"
                  src={posterSrc}
                  alt={title ?? "视频封面"}
                />
              ) : null}
            </div>

            <div className="flex min-w-0 flex-col justify-center self-center">
              {title && (
                <h2 className="text-[34px] font-semibold tracking-tight text-white/95">
                  {title}
                </h2>
              )}

              <div className="mt-6 space-y-0">
                {normalizedSections.map((section, index) => (
                  <section
                    key={`${section.title}-${index}`}
                    className="border-t border-white/10 py-7 first:border-t"
                  >
                    <h3 className="mb-5 text-[15px] font-medium text-white/55">
                      {section.title}
                    </h3>
                    {section.items && section.items.length > 0 && (
                      <ul className="space-y-5 text-[16px] leading-8 text-white/88">
                        {section.items.map((item, itemIndex) => (
                          <li
                            key={`${section.title}-${itemIndex}`}
                            className="flex gap-3"
                          >
                            <span className="mt-[11px] h-1.5 w-1.5 shrink-0 rounded-full bg-white/65" />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {section.body && (
                      <p className="whitespace-pre-wrap text-[16px] leading-8 text-white/88">
                        {section.body}
                      </p>
                    )}
                  </section>
                ))}
              </div>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default VideoDetailModal;
