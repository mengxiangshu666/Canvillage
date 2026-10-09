"use client";

import { useState, useEffect } from "react";
import { cn } from "./utils";
import { ImageDetailModal } from "./modals/image-detail-modal";
import type { ImageCandidate } from "./modals/image-detail-modal";
import type { ComponentFn } from "./types";
import { p, coerceText } from "./types";
import { useSpecRendererContext } from "./context";
import {
  MEDIA_SIZES,
  mediaStyle,
  mediaWidth,
  extractCandidates,
  useSendCandidateSelection,
  PreviewableImageFigure,
  UnresolvedMediaPlaceholder,
} from "./media-utils";

/** 判断 src 是否为内部路径但还未被 resolveMediaUrl 解析 */
function useIsUnresolved(src: string): boolean {
  const { parseMediaUrl } = useSpecRendererContext();
  if (!parseMediaUrl) return false;
  return Boolean(parseMediaUrl(src)) && !src.startsWith("http");
}

const Card: ComponentFn = ({ element, children }) => {
  const props = p(element);
  const title = coerceText(props.title);
  const description = coerceText(props.description);
  return (
    <div className="jr-card">
      {title && <div className="jr-card__title">{title}</div>}
      {description && <div className="jr-card__desc">{description}</div>}
      {children}
    </div>
  );
};

const Text: ComponentFn = ({ element }) => {
  const props = p(element);
  const content = coerceText(props.content ?? props.text);
  const variant = (props.variant ?? "body") as string;
  return <p className={`jr-text jr-text--${variant}`}>{content}</p>;
};

const Heading: ComponentFn = ({ element }) => {
  const props = p(element);
  const content = coerceText(props.content ?? props.text);
  const level = Math.min(Math.max(Number(props.level) || 3, 1), 6);
  const tag = `h${level}`;
  return (
    <div
      className={`jr-heading jr-heading--${tag}`}
      role="heading"
      aria-level={level}
    >
      {content}
    </div>
  );
};

const Table: ComponentFn = ({ element }) => {
  const props = p(element);
  const columns = (props.columns ?? []) as Array<{
    key: string;
    label?: string;
  }>;
  const rows = (props.rows ?? []) as Array<Record<string, unknown>>;
  return (
    <div className="jr-table-wrap">
      <table className="jr-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key}>{coerceText(col.label) || col.key}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={col.key}>{coerceText(row[col.key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const List: ComponentFn = ({ element }) => {
  const props = p(element);
  const items = (props.items ?? []) as unknown[];
  const ordered = props.ordered === true;
  const inner = items.map((item, i) => <li key={i}>{coerceText(item)}</li>);
  return ordered ? (
    <ol className="jr-list">{inner}</ol>
  ) : (
    <ul className="jr-list">{inner}</ul>
  );
};

const Badge: ComponentFn = ({ element }) => {
  const props = p(element);
  const label = coerceText(props.label ?? props.text);
  const variant = (props.variant ?? "default") as string;
  return <span className={`jr-badge jr-badge--${variant}`}>{label}</span>;
};

const Alert: ComponentFn = ({ element, children }) => {
  const props = p(element);
  const title = coerceText(props.title);
  const message = coerceText(props.message ?? props.content);
  const variant = (props.variant ?? "info") as string;
  return (
    <div className={`jr-alert jr-alert--${variant}`}>
      {title && <div className="jr-alert__title">{title}</div>}
      {message && <div className="jr-alert__message">{message}</div>}
      {children}
    </div>
  );
};

const ImageComponent: ComponentFn = ({ element }) => {
  const props = p(element);
  const src = (props.src ?? props.url ?? "") as string;
  const alt = (props.alt ?? "") as string;
  const caption = props.caption as string | undefined;
  const fit =
    (props.fit as React.CSSProperties["objectFit"] | undefined) ?? "contain";
  const overlayTitle = props.overlayTitle as string | undefined;
  const overlayDescription = props.overlayDescription as string | undefined;
  const detailType = props.detailType as string | undefined;
  const detailTags = Array.isArray(props.detailTags)
    ? (props.detailTags as unknown[]).filter(
        (t): t is string => typeof t === "string" && t.trim().length > 0,
      )
    : undefined;
  const detailSections = Array.isArray(props.detailSections)
    ? (props.detailSections as Array<{ label: string; value: string }>)
    : undefined;
  const candidates = extractCandidates(props);
  const rawClickAction = (
    (props.clickAction ?? props.imageAction ?? "none") as string
  ).toLowerCase();
  const clickAction =
    rawClickAction === "edit"
      ? "edit"
      : rawClickAction === "preview" || rawClickAction === "view"
        ? "preview"
        : "none";
  const isInteractive = clickAction !== "none";
  const overlayText = overlayDescription ?? caption;
  const hasOverlay = Boolean(overlayTitle || overlayText);
  const [modalOpen, setModalOpen] = useState(false);
  const [displaySrc, setDisplaySrc] = useState(src);
  useEffect(() => {
    setDisplaySrc(src);
  }, [src]);
  const sendSelection = useSendCandidateSelection(setModalOpen);
  const handleSelectCandidate = candidates
    ? (candidate: ImageCandidate) => {
        setDisplaySrc(candidate.src);
        sendSelection(candidate);
      }
    : undefined;
  const overlayWidth = mediaWidth(props) ?? "min(220px, 100%)";
  const isUnresolved = useIsUnresolved(src);

  if (!src) return null;
  if (isUnresolved) {
    return (
      <UnresolvedMediaPlaceholder
        width={mediaWidth(props)}
        style={{ ...mediaStyle(props), objectFit: fit }}
      />
    );
  }
  return (
    <>
      <figure
        className={cn(
          "jr-figure jr-figure--overlay relative",
          isInteractive && "cursor-pointer",
        )}
        style={overlayWidth ? { width: overlayWidth } : undefined}
        onClick={isInteractive ? () => setModalOpen(true) : undefined}
        role={isInteractive ? "button" : undefined}
        tabIndex={isInteractive ? 0 : undefined}
        onKeyDown={(e) => {
          if (!isInteractive) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setModalOpen(true);
          }
        }}
      >
        <img
          className={cn(
            "jr-image jr-image--overlay block rounded-xl",
            isInteractive && "jr-image--interactive",
          )}
          src={displaySrc}
          alt={alt}
          loading="lazy"
          style={{
            ...mediaStyle(props),
            objectFit: fit,
            width: "100%",
            height: "auto",
            borderRadius: "0.75rem",
          }}
        />
        {hasOverlay && (
          <div
            className={cn(
              "jr-image__overlay jr-image__overlay--bottom",
              "jr-image__overlay--gradient-dark",
            )}
          >
            {overlayTitle && (
              <div className="jr-image__overlay-title">{overlayTitle}</div>
            )}
            {overlayText && (
              <div className="jr-image__overlay-description">{overlayText}</div>
            )}
          </div>
        )}
      </figure>
      <ImageDetailModal
        src={displaySrc}
        hasOverlay
        overlayDescription={overlayDescription}
        overlayTitle={overlayTitle}
        detailType={detailType}
        detailTags={detailTags}
        detailSections={detailSections}
        candidates={candidates}
        onSelectCandidate={candidates ? handleSelectCandidate : undefined}
        mode={clickAction === "edit" ? "edit" : "preview"}
        open={modalOpen}
        setOpen={setModalOpen}
      />
    </>
  );
};

const Audio: ComponentFn = ({ element }) => {
  const props = p(element);
  const src = (props.src ?? props.url ?? "") as string;
  const caption = props.caption as string | undefined;
  const autoplay = props.autoplay === true;
  const loop = props.loop === true;
  const muted = props.muted === true;
  const controls = props.controls !== false;
  const poster = props.poster as string | undefined;

  if (!src) return null;
  const size = props.size as string | undefined;
  const sizeClass = size && MEDIA_SIZES[size] ? ` jr-video--${size}` : "";

  return (
    <figure className="jr-figure jr-figure--audio">
      <video
        className={`jr-video${sizeClass}`}
        src={src}
        autoPlay={autoplay}
        loop={loop}
        muted={muted}
        controls={controls}
        playsInline
        poster={poster}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        style={{
          ...mediaStyle(props),
          width: "100%",
          height: poster ? "auto" : "54px",
          minHeight: "54px",
          outline: "none",
        }}
      />
      {caption && (
        <figcaption className="jr-figure__caption">{caption}</figcaption>
      )}
    </figure>
  );
};

const Video: ComponentFn = ({ element }) => {
  const props = p(element);
  const src = (props.src ?? props.url ?? "") as string;
  const caption = props.caption as string | undefined;
  const autoplay = props.autoplay === true;
  const loop = props.loop === true;
  const muted = props.muted !== false;
  const controls = props.controls !== false;
  const poster = props.poster as string | undefined;
  const size = props.size as string | undefined;
  const fit =
    (props.fit as React.CSSProperties["objectFit"] | undefined) ?? "contain";
  const overlayTitle = props.overlayTitle as string | undefined;
  const overlayDescription = props.overlayDescription as string | undefined;
  const overlayIcon = props.overlayIcon as string | undefined;
  const hasOverlay = Boolean(overlayTitle || overlayDescription || overlayIcon);
  const resolvedFit = hasOverlay ? "contain" : fit;
  if (!src) return null;
  const sizeClass = size && MEDIA_SIZES[size] ? ` jr-video--${size}` : "";
  const overlayWidth = mediaWidth(props) ?? "min(220px, 100%)";
  return (
    <figure
      className={`jr-figure${hasOverlay ? " jr-figure--overlay relative" : ""}`}
      style={hasOverlay ? { width: overlayWidth } : undefined}
    >
      <video
        className={`jr-video${sizeClass}${hasOverlay ? " jr-video--overlay" : ""}`}
        src={src}
        autoPlay={autoplay}
        loop={loop}
        muted={muted}
        controls={controls}
        playsInline
        poster={poster}
        style={{
          ...mediaStyle(props),
          objectFit: resolvedFit,
          width: hasOverlay ? "100%" : undefined,
        }}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      />
      {hasOverlay && (
        <div className="jr-video__meta">
          {overlayTitle && (
            <div className="jr-video__meta-title">{overlayTitle}</div>
          )}
          {overlayDescription && (
            <div className="jr-video__meta-description">
              {overlayDescription}
            </div>
          )}
        </div>
      )}
      {!hasOverlay && caption && (
        <figcaption className="jr-figure__caption">{caption}</figcaption>
      )}
    </figure>
  );
};

const Stack: ComponentFn = ({ element, children }) => {
  const props = p(element);
  const direction = (props.direction ?? "column") as string;
  const gap = props.gap as string | number | undefined;
  const style = gap
    ? { gap: typeof gap === "number" ? `${gap}px` : gap }
    : undefined;
  return (
    <div className={`jr-stack jr-stack--${direction}`} style={style}>
      {children}
    </div>
  );
};

const Separator: ComponentFn = () => {
  return <hr className="jr-separator" />;
};

const Progress: ComponentFn = ({ element }) => {
  const props = p(element);
  const value = Math.min(Math.max(Number(props.value) || 0, 0), 100);
  const label = coerceText(props.label);
  return (
    <div className="jr-progress">
      {label && <div className="jr-progress__label">{label}</div>}
      <div className="jr-progress__track">
        <div className="jr-progress__bar" style={{ width: `${value}%` }} />
      </div>
    </div>
  );
};

const Link: ComponentFn = ({ element, children }) => {
  const props = p(element);
  const href = (props.href ?? props.url ?? "#") as string;
  const label = (props.label ?? props.text) as string | undefined;
  return (
    <a
      className="jr-link"
      href={href}
      onClick={(e) => {
        e.preventDefault();
        window.open(href, "_blank", "noopener,noreferrer");
      }}
      rel="noopener noreferrer"
    >
      {children.length > 0 ? children : (label ?? href)}
    </a>
  );
};

const Code: ComponentFn = ({ element }) => {
  const props = p(element);
  const code = coerceText(props.code ?? props.content);
  const language = coerceText(props.language);
  return (
    <div className="jr-code">
      <div className="jr-code__header">
        {language && <span className="jr-code__lang">{language}</span>}
        <button
          className="jr-code__copy"
          onClick={() => void navigator.clipboard.writeText(code)}
          title="Copy"
        >
          Copy
        </button>
      </div>
      <pre className="jr-code__pre">
        <code>{code}</code>
      </pre>
    </div>
  );
};

const Collapsible: ComponentFn = ({ element, children }) => {
  const props = p(element);
  const title = coerceText(props.title ?? props.label) || "Details";
  const open = props.open === true;
  return (
    <details className="jr-collapsible" open={open}>
      <summary className="jr-collapsible__summary">{title}</summary>
      <div className="jr-collapsible__body">{children}</div>
    </details>
  );
};

const KeyValue: ComponentFn = ({ element }) => {
  const props = p(element);
  const items = (props.items ?? []) as Array<Record<string, unknown>>;
  return (
    <dl className="jr-kv">
      {items.map((item, i) => (
        <div key={i} className="jr-kv__row">
          <dt className="jr-kv__key">{coerceText(item?.key)}</dt>
          <dd className="jr-kv__value">{coerceText(item?.value)}</dd>
        </div>
      ))}
    </dl>
  );
};

function GalleryImageItem({
  src,
  alt,
  caption,
}: {
  src: string;
  alt?: string;
  caption?: string;
}) {
  const isUnresolved = useIsUnresolved(src);
  if (!src || isUnresolved) {
    return (
      <figure className="jr-gallery__item">
        <div
          className="jr-gallery__img animate-pulse rounded-lg bg-muted-foreground/10"
          style={{ aspectRatio: "4 / 3", width: "100%" }}
        />
      </figure>
    );
  }
  return (
    <PreviewableImageFigure
      src={src}
      alt={alt}
      overlayDescription={caption}
      figureClassName="jr-gallery__item"
      imageClassName="jr-gallery__img"
      imageStyle={{
        aspectRatio: "4 / 3",
        objectFit: "cover",
        width: "100%",
        height: "auto",
      }}
    />
  );
}

const Gallery: ComponentFn = ({ element }) => {
  const props = p(element);
  const allImages = (props.images ?? []) as Array<{
    src: string;
    alt?: string;
    caption?: string;
  }>;
  const columns = Math.min(Math.max(Number(props.columns) || 3, 1), 6);
  return (
    <div
      className="jr-gallery"
      style={{ gridTemplateColumns: `repeat(${columns}, 1fr)` }}
    >
      {allImages.map((img, i) => (
        <GalleryImageItem
          key={i}
          src={img.src}
          alt={img.alt}
          caption={img.caption}
        />
      ))}
    </div>
  );
};

export const COMPONENTS: Record<string, ComponentFn> = {
  Card,
  Text,
  Heading,
  Table,
  List,
  Badge,
  Alert,
  Image: ImageComponent,
  Audio,
  Video,
  Stack,
  Separator,
  Progress,
  Link,
  Code,
  Collapsible,
  KeyValue,
  Gallery,
};
