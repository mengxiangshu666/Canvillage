import type { Spec } from "./spec";
import type { ImageCandidate } from "./modals/image-detail-modal";
import type { VideoDetailSection } from "./modals/video-detail-modal";
import type { Props } from "./types";
import { p, coerceText } from "./types";

export function extractElementText(
  spec: Spec,
  elementId: string,
): string | undefined {
  const element = spec.elements[elementId];
  if (!element) return undefined;
  const props = p(element);
  const content = coerceText(
    props.content ?? props.text ?? props.description,
  ).trim();
  return content || undefined;
}

export function extractKeyValueItems(
  spec: Spec,
  elementId: string,
): Array<{ key: string; value: string }> {
  const element = spec.elements[elementId];
  if (!element) return [];
  const props = p(element);
  const items = Array.isArray(props.items) ? props.items : [];
  return items
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const entry = item as Record<string, unknown>;
      const key = coerceText(entry.key).trim();
      const value = coerceText(entry.value).trim();
      if (!key || !value) return null;
      return { key, value };
    })
    .filter((item): item is { key: string; value: string } => item !== null);
}

export function normalizeVideoDetailSections(
  props: Props,
  fallbackDescription?: string,
): VideoDetailSection[] {
  const sections: VideoDetailSection[] = [];

  const rawSections = props.detailSections;
  if (Array.isArray(rawSections)) {
    for (const rawSection of rawSections) {
      if (!rawSection || typeof rawSection !== "object") continue;
      const section = rawSection as Record<string, unknown>;
      const title = typeof section.title === "string" ? section.title : "";
      const body =
        typeof section.body === "string"
          ? section.body
          : typeof section.content === "string"
            ? section.content
            : undefined;
      const items = Array.isArray(section.items)
        ? section.items.filter(
            (item): item is string =>
              typeof item === "string" && item.trim().length > 0,
          )
        : undefined;
      if (!title) continue;
      sections.push({ title, body, items });
    }
  }

  const characters = Array.isArray(props.detailCharacters)
    ? props.detailCharacters.filter(
        (item): item is string =>
          typeof item === "string" && item.trim().length > 0,
      )
    : Array.isArray(props.characters)
      ? props.characters.filter(
          (item): item is string =>
            typeof item === "string" && item.trim().length > 0,
        )
      : [];
  if (characters.length > 0) {
    sections.push({ title: "【出场人物、身份】", items: characters });
  }

  const summary =
    typeof props.detailSummary === "string"
      ? props.detailSummary
      : typeof props.summary === "string"
        ? props.summary
        : typeof props.plotSummary === "string"
          ? props.plotSummary
          : undefined;
  if (summary) {
    sections.push({ title: "【剧情概览】", body: summary });
  }

  const narration =
    typeof props.detailNarration === "string"
      ? props.detailNarration
      : typeof props.narration === "string"
        ? props.narration
        : typeof props.voiceover === "string"
          ? props.voiceover
          : undefined;
  if (narration) {
    sections.push({ title: "【解说词】", body: narration });
  }

  if (sections.length === 0 && fallbackDescription) {
    sections.push({ title: "【视频说明】", body: fallbackDescription });
  }

  return sections;
}

export type SketchTag = { label: string; color?: string };

export type SketchGalleryItem = {
  id: string;
  title: string;
  description?: string;
  src: string;
  alt: string;
  type?: string;
  candidates?: ImageCandidate[];
  tags?: SketchTag[];
};

function extractSketchCandidates(props: Props): ImageCandidate[] | undefined {
  if (!Array.isArray(props.candidates)) return undefined;
  const out = (props.candidates as Array<Record<string, unknown>>)
    .filter(
      (c) =>
        c &&
        typeof c === "object" &&
        typeof c.id === "string" &&
        typeof c.src === "string",
    )
    .map((c) => ({
      id: c.id as string,
      src: c.src as string,
      label: typeof c.label === "string" ? c.label : undefined,
    }));
  return out.length > 0 ? out : undefined;
}

export function extractSketchGalleryItems(spec: Spec): SketchGalleryItem[] {
  const items: SketchGalleryItem[] = [];

  const visit = (elementId: string, parentTitle?: string) => {
    const element = spec.elements[elementId];
    if (!element) return;

    if (element.type === "Image") {
      const props = p(element);
      const src = String(props.src ?? props.url ?? "");
      if (!src) return;
      const title = String(props.overlayTitle ?? parentTitle ?? elementId);
      const description = props.overlayDescription as string | undefined;
      const alt = String(props.alt ?? title);
      const candidates = extractSketchCandidates(props);
      const rawTags = Array.isArray(props.tags) ? props.tags : [];
      const tags: SketchTag[] = rawTags
        .filter(
          (t): t is Record<string, unknown> =>
            t != null && typeof t === "object",
        )
        .map((t) => ({
          label: String(t.label ?? ""),
          color: typeof t.color === "string" ? t.color : undefined,
        }))
        .filter((t) => t.label.length > 0);
      items.push({
        id: elementId,
        title,
        description,
        src,
        alt,
        type: spec.type,
        candidates,
        tags: tags.length > 0 ? tags : undefined,
      });
      return;
    }

    const cardTitle =
      element.type === "Card"
        ? (typeof element.props?.title === "string" &&
            element.props.title.trim()) ||
          parentTitle
        : parentTitle;

    for (const childId of element.children ?? []) {
      visit(childId, cardTitle);
    }
  };

  visit(spec.root);
  return items;
}

export type KeyframeGalleryItem = {
  id: string;
  title: string;
  description?: string;
  imageSrc: string;
  alt: string;
  badge?: string;
  videoSrc?: string;
};

export function extractKeyframeGalleryItems(
  spec: Spec,
): KeyframeGalleryItem[] {
  const root = spec.elements[spec.root];
  const rowIds = root?.children ?? [];
  const items: KeyframeGalleryItem[] = [];

  const candidateIds: string[] = [];
  const collectCandidates = (ids: string[]) => {
    for (const id of ids) {
      const el = spec.elements[id];
      if (!el) continue;
      if (el.type === "Video" || el.type === "Image") {
        candidateIds.push(id);
      } else if (el.type === "Stack") {
        collectCandidates(el.children ?? []);
      } else if (el.type === "Card") {
        const hasMedia = (el.children ?? []).some((cid) => {
          const c = spec.elements[cid];
          return c?.type === "Image" || c?.type === "Video";
        });
        const hasNestedStack = (el.children ?? []).some((cid) => {
          const c = spec.elements[cid];
          return c?.type === "Stack";
        });
        if (hasMedia) {
          candidateIds.push(id);
        } else if (hasNestedStack) {
          collectCandidates(el.children ?? []);
        } else {
          candidateIds.push(id);
        }
      }
    }
  };
  collectCandidates(rowIds);

  for (const cardId of candidateIds) {
    const card = spec.elements[cardId];
    if (!card) continue;

    if (card.type === "Video") {
      const videoProps = p(card);
      const videoSrc = String(videoProps.src ?? videoProps.url ?? "");
      if (!videoSrc) continue;
      const title = String(
        videoProps.overlayTitle ?? videoProps.caption ?? cardId,
      );
      const description = videoProps.overlayDescription as string | undefined;
      const poster = videoProps.poster as string | undefined;
      items.push({
        id: cardId,
        title,
        description,
        imageSrc: poster ?? videoSrc,
        alt: title,
        videoSrc,
      });
      continue;
    }

    if (card.type === "Image") {
      const imgProps = p(card);
      const src = String(imgProps.src ?? imgProps.url ?? "");
      if (!src) continue;
      const title = String(imgProps.overlayTitle ?? imgProps.alt ?? cardId);
      const description = imgProps.overlayDescription as string | undefined;
      items.push({
        id: cardId,
        title,
        description,
        imageSrc: src,
        alt: String(imgProps.alt ?? title),
      });
      continue;
    }

    const cardProps = p(card);
    const title =
      (typeof cardProps.title === "string" && cardProps.title.trim()) || cardId;

    let imageSrc = "";
    let alt = title;
    let description: string | undefined;
    let badge: string | undefined;
    let videoSrc: string | undefined;

    for (const childId of card.children ?? []) {
      const child = spec.elements[childId];
      if (!child) continue;
      const childProps = p(child);

      if (child.type === "Image" && !imageSrc) {
        imageSrc = String(childProps.src ?? childProps.url ?? "");
        alt = String(childProps.alt ?? title);
        continue;
      }

      if (child.type === "Video" && !videoSrc) {
        videoSrc = String(childProps.src ?? childProps.url ?? "");
        if (!imageSrc) {
          imageSrc = (childProps.poster as string) ?? videoSrc;
          alt = String(childProps.alt ?? title);
        }
        continue;
      }

      if (child.type === "Text" && !description) {
        description = extractElementText(spec, childId);
        continue;
      }

      if (child.type === "Badge" && !badge) {
        const label = childProps.label ?? childProps.text;
        if (typeof label === "string" && label.trim()) {
          badge = label.trim();
        }
      }
    }

    if (!imageSrc && !videoSrc) continue;
    items.push({
      id: cardId,
      title,
      description,
      imageSrc: imageSrc || videoSrc || "",
      alt,
      badge,
      videoSrc,
    });
  }

  if (items.length === 0) {
    const VIDEO_EXT_RE = /\.(mp4|webm|mov|m4v|ogg)(\?.*)?$/i;
    for (const el of Object.values(spec.elements)) {
      if (el.type !== "Table") continue;
      const rows = (el.props?.rows ?? []) as Array<Record<string, unknown>>;
      const columns = (el.props?.columns ?? []) as Array<{ key: string }>;
      const videoColKey = columns.find((c) =>
        /video|视频|src|url|mp4/i.test(c.key),
      )?.key;
      if (!videoColKey) continue;
      const textColKey = columns.find((c) =>
        /text|内容|描述|narration|旁白|对白/i.test(c.key),
      )?.key;
      const beatColKey = columns.find((c) =>
        /beat|序号|编号/i.test(c.key),
      )?.key;

      for (let ri = 0; ri < rows.length; ri++) {
        const row = rows[ri];
        const videoSrc = String(row[videoColKey] ?? "");
        if (!videoSrc || !VIDEO_EXT_RE.test(videoSrc)) continue;
        const beatNum = beatColKey
          ? String(row[beatColKey] ?? ri + 1)
          : String(ri + 1);
        const description = textColKey
          ? String(row[textColKey] ?? "")
          : undefined;
        items.push({
          id: `table_beat_${ri}`,
          title: `Beat ${beatNum.padStart(2, "0")}`,
          description,
          imageSrc: videoSrc,
          alt: `Beat ${beatNum}`,
          videoSrc,
        });
      }
      if (items.length > 0) break;
    }
  }

  return items;
}
