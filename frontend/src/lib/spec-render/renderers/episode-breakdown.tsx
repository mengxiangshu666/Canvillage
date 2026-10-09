"use client";

import type { RootRendererFn } from "../types";
import { p, coerceText } from "../types";
import { RenderNode } from "../core";
import type { ImageCandidate } from "../modals/image-detail-modal";
import {
  extractCandidates,
  MediaTransitionShell,
  PreviewableImageFigure,
} from "../media-utils";
import { CastList } from "../cast-list";
import {
  AlertList,
  BadgeList,
  parseCastEntries,
  looksLikeEpisode,
} from "./shared";
import type { Spec } from "../spec";
import { useSpecRendererContext } from "../context";

type MediaItem = {
  type: "image" | "pending";
  src?: string;
  alt?: string;
  overlayTitle?: string;
  overlayDescription?: string;
  detailType?: string;
  detailTags?: string[];
  candidates?: ImageCandidate[];
  progress?: number;
};

function collectRoleInfo(spec: Spec, roleChildIds: string[]) {
  const descriptions: string[] = [];
  const mediaItems: MediaItem[] = [];

  const processChild = (childId: string) => {
    const child = spec.elements[childId];
    if (!child) return;
    if (child.type === "Text") {
      const content = coerceText(child.props?.content ?? child.props?.text);
      if (content.trim()) descriptions.push(content);
    } else if (child.type === "Image") {
      const src = (child.props?.src ?? child.props?.url ?? "") as string;
      const childProps = (child.props ?? {}) as Record<string, unknown>;
      if (src)
        mediaItems.push({
          type: "image",
          src,
          alt: (child.props?.alt ?? "") as string,
          overlayTitle: child.props?.overlayTitle as string | undefined,
          overlayDescription: child.props?.overlayDescription as
            | string
            | undefined,
          detailType: child.props?.detailType as string | undefined,
          detailTags: Array.isArray(child.props?.detailTags)
            ? child.props.detailTags.filter(
                (t): t is string =>
                  typeof t === "string" && t.trim().length > 0,
              )
            : undefined,
          candidates: extractCandidates(childProps),
        });
    } else if (
      child.type === "Progress" ||
      (child.type === "Card" &&
        (child.children ?? []).some(
          (c) => spec.elements[c]?.type === "Progress",
        ))
    ) {
      let progressVal = 58;
      if (child.type === "Progress")
        progressVal = (child.props?.value ?? 58) as number;
      else {
        for (const gc of child.children ?? []) {
          const gcEl = spec.elements[gc];
          if (gcEl?.type === "Progress") {
            progressVal = (gcEl.props?.value ?? 58) as number;
            break;
          }
        }
      }
      mediaItems.push({ type: "pending", progress: progressVal });
    } else if (child.type === "Stack") {
      for (const gc of child.children ?? []) processChild(gc);
    }
  };
  for (const cid of roleChildIds) processChild(cid);
  return { descriptions, mediaItems };
}

type MultiEpisodeData = {
  id: string;
  title: string;
  description?: string;
  castEntries: Array<{ text: string; costumeDescription?: string }>;
  badges: Array<{ label: string; variant?: string }>;
  alerts: Array<{ variant?: string; title?: string; message?: string }>;
};

function extractMultiEpisode(
  spec: Spec,
  cardIds: string[],
): MultiEpisodeData[] {
  return cardIds
    .map((epId) => {
      const ep = spec.elements[epId];
      if (!ep || ep.type !== "Card") return null;
      const data: MultiEpisodeData = {
        id: epId,
        title: coerceText(ep.props?.title),
        description: coerceText(ep.props?.description) || undefined,
        castEntries: [],
        badges: [],
        alerts: [],
      };
      for (const gc of ep.children ?? []) {
        const g = spec.elements[gc];
        if (!g) continue;
        if (g.type === "List")
          data.castEntries = parseCastEntries(
            (g.props?.items ?? []) as Array<unknown>,
          );
        else if (g.type === "Badge")
          data.badges.push({
            label: coerceText(g.props?.label),
            variant: g.props?.variant as string | undefined,
          });
        else if (g.type === "Alert")
          data.alerts.push({
            variant: g.props?.variant as string | undefined,
            title: coerceText(g.props?.title) || undefined,
            message: coerceText(g.props?.message) || undefined,
          });
      }
      return data;
    })
    .filter((d): d is MultiEpisodeData => d !== null);
}

function PendingMediaTile({ progress }: { progress: number }) {
  const { loadingVideoUrl } = useSpecRendererContext();
  return (
    <div className="relative h-[198px] w-[148px] shrink-0 overflow-hidden rounded-2xl bg-gradient-to-br from-sky-900/40 to-indigo-900/40">
      <div className="absolute inset-0 animate-pulse bg-foreground/5" />
      <div className="absolute left-2 top-2 z-10 rounded-md bg-background/50 px-2 py-1 text-xs text-foreground/90 backdrop-blur-sm">
        灵感即显 {progress}%
      </div>
      {loadingVideoUrl && (
        <div className="absolute inset-0">
          <video
            className="h-full w-full object-cover opacity-60"
            src={loadingVideoUrl}
            autoPlay
            loop
            muted
            playsInline
          />
        </div>
      )}
    </div>
  );
}

export const episodeBreakdownRenderer: RootRendererFn = ({ spec, context }) => {
  let rootElement = spec.elements[spec.root];
  let rootProps = rootElement ? p(rootElement) : {};

  let rootChildren = rootElement?.children ?? [];
  if (rootChildren.length === 1) {
    const onlyChild = spec.elements[rootChildren[0]];
    if (onlyChild?.type === "Card") {
      rootElement = onlyChild;
      rootProps = p(onlyChild);
      rootChildren = onlyChild.children ?? [];
    }
  }

  const title = coerceText(rootProps.title) || undefined;
  const description = coerceText(rootProps.description) || undefined;

  let episodeTitle = "";
  let castEntries: Array<{ text: string; costumeDescription?: string }> = [];
  let summaryContent = "";
  let roleSectionsId: string | null = null;
  let roleHeading = "";
  const kvItems: Array<{ key: string; value: string }> = [];
  let tableData: {
    columns: Array<{ key: string; label: string }>;
    rows: Array<Record<string, string>>;
  } | null = null;
  const alertItems: Array<{
    variant?: string;
    title?: string;
    message?: string;
  }> = [];

  for (const childId of rootChildren) {
    const child = spec.elements[childId];
    if (!child) continue;

    if (child.type === "Heading") {
      const content = coerceText(child.props?.content ?? child.props?.text);
      if (content.trim() && !episodeTitle) episodeTitle = content;
    }
    if (child.type === "Text" && !episodeTitle) {
      const variant = child.props?.variant as string | undefined;
      const content = coerceText(child.props?.content ?? child.props?.text);
      if (variant === "label" && content.trim()) episodeTitle = content;
    }
    if (child.type === "Text") {
      const variant = child.props?.variant as string | undefined;
      const content = coerceText(child.props?.content ?? child.props?.text);
      if (variant === "body" && content.trim() && !summaryContent)
        summaryContent = content;
    }
    if (child.type === "List" && castEntries.length === 0) {
      castEntries = parseCastEntries(
        (child.props?.items ?? []) as Array<unknown>,
      );
    }
    if (child.type === "KeyValue") {
      const items = (child.props?.items ?? []) as Array<
        Record<string, unknown>
      >;
      for (const rawItem of items) {
        const key = coerceText(rawItem?.key);
        const value = coerceText(rawItem?.value);
        if (!key && !value) continue;
        kvItems.push({ key, value });
        if (castEntries.length === 0 && /角色|人物|出场/.test(key))
          castEntries = value
            .split(/[、，,]/)
            .map((s) => s.trim())
            .filter(Boolean)
            .map((text) => ({ text }));
      }
    }
    if (child.type === "Table" && !tableData) {
      const columns = (child.props?.columns ?? []) as Array<{
        key: string;
        label: string;
      }>;
      const rows = (child.props?.rows ?? []) as Array<Record<string, string>>;
      if (columns.length > 0) tableData = { columns, rows };
    }
    if (child.type === "Alert")
      alertItems.push({
        variant: child.props?.variant as string | undefined,
        title: coerceText(child.props?.title) || undefined,
        message: coerceText(child.props?.message) || undefined,
      });
    if (child.type === "Card" && !summaryContent) {
      for (const gcId of child.children ?? []) {
        const gc = spec.elements[gcId];
        if (gc?.type === "Text") {
          const c = coerceText(gc.props?.content ?? gc.props?.text);
          if (c.trim()) {
            summaryContent = c;
            break;
          }
        }
      }
    }
    if (child.type === "Stack" && !roleSectionsId) {
      const firstChild = child.children?.[0]
        ? spec.elements[child.children[0]]
        : null;
      if (firstChild?.type === "Card" || firstChild?.type === "Image") {
        roleSectionsId = childId;
        const idx = rootChildren.indexOf(childId);
        if (idx > 0) {
          const prev = spec.elements[rootChildren[idx - 1]];
          if (prev?.type === "Heading")
            roleHeading = (prev.props?.content ?? "") as string;
        }
      }
    }
  }

  const roleIds = roleSectionsId
    ? (spec.elements[roleSectionsId]?.children ?? [])
    : [];

  let multiEpisodes: MultiEpisodeData[] = [];

  const directCardIds = rootChildren.filter(
    (id) => spec.elements[id]?.type === "Card",
  );
  const isMultiDirect =
    directCardIds.length >= 2 &&
    directCardIds.some((id) => looksLikeEpisode(spec, id));

  if (isMultiDirect) {
    multiEpisodes = extractMultiEpisode(spec, directCardIds);
  } else {
    const hasUsefulFlatData =
      castEntries.length > 0 || summaryContent || roleIds.length > 0;
    if (!hasUsefulFlatData) {
      for (const childId of rootChildren) {
        const child = spec.elements[childId];
        if (child?.type === "Stack") {
          const stackKids = (child.children ?? []).filter(
            (id) => spec.elements[id]?.type === "Card",
          );
          if (
            stackKids.length >= 2 &&
            stackKids.some((id) => looksLikeEpisode(spec, id))
          ) {
            multiEpisodes = extractMultiEpisode(spec, stackKids);
            break;
          }
        }
      }
    }
  }

  if (multiEpisodes.length > 0) {
    const totalExtracted = multiEpisodes.reduce(
      (sum, ep) =>
        sum + ep.castEntries.length + ep.badges.length + ep.alerts.length,
      0,
    );
    if (totalExtracted === 0) {
      return <RenderNode context={context} elementId={spec.root} />;
    }
    return (
      <div className="max-w-4xl space-y-4 text-foreground">
        {(title || description) && (
          <div className="overflow-hidden rounded-[20px] bg-transparent p-5">
            {title && (
              <div className="mb-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
                <span>🎬</span>
                <span>{title}</span>
              </div>
            )}
            {description && (
              <div className="text-sm text-muted-foreground">{description}</div>
            )}
          </div>
        )}
        {multiEpisodes.map((ep) => (
          <div
            key={ep.id}
            className="overflow-hidden rounded-[20px] bg-transparent p-5"
          >
            <div className="mb-1 flex items-center gap-2 text-lg font-semibold tracking-tight">
              <span>🎬</span>
              <span>{ep.title}</span>
            </div>
            {ep.description && (
              <div className="mb-3 text-sm text-muted-foreground">
                {ep.description}
              </div>
            )}
            {ep.castEntries.length > 0 && <CastList entries={ep.castEntries} />}
            <BadgeList badges={ep.badges} />
            <AlertList alerts={ep.alerts} />
          </div>
        ))}
      </div>
    );
  }

  const hasContent =
    castEntries.length > 0 ||
    summaryContent ||
    roleIds.length > 0 ||
    kvItems.length > 0 ||
    tableData ||
    alertItems.length > 0;
  if (!hasContent) {
    return <RenderNode context={context} elementId={spec.root} />;
  }

  return (
    <div className="max-w-4xl overflow-hidden rounded-[20px] bg-transparent p-5 text-foreground">
      {(episodeTitle || title) && (
        <div className="mb-3 flex items-center gap-2 text-xl font-semibold tracking-tight">
          <span>🎬</span>
          <span>
            {episodeTitle
              ? `《${episodeTitle.replace(/^《|》$/g, "")}》`
              : title}
          </span>
        </div>
      )}
      {description && (
        <div className="mb-4 text-sm text-muted-foreground">{description}</div>
      )}

      {kvItems.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {kvItems.map((item, i) => (
            <span key={i} className="text-muted-foreground">
              <span className="text-muted-foreground/70">{item.key}：</span>
              <span className="text-foreground/80">{item.value}</span>
            </span>
          ))}
        </div>
      )}

      {castEntries.length > 0 && kvItems.length === 0 && (
        <CastList entries={castEntries} />
      )}

      {tableData && (
        <div className="mb-5">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {tableData.columns.map((col) => (
                  <th
                    key={col.key}
                    className="px-3 py-2 text-left font-medium text-muted-foreground"
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableData.rows.map((row, ri) => (
                <tr key={ri} className="border-b border-border/50">
                  {tableData!.columns.map((col) => (
                    <td key={col.key} className="px-3 py-2 text-foreground/80">
                      {row[col.key] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {summaryContent && (
        <div className="mb-5">
          <div className="mb-2 text-ms font-medium text-muted-foreground">
            【本集剧情概览】
          </div>
          <p className="whitespace-pre-wrap text-sm leading-6 text-foreground/80">
            {summaryContent}
          </p>
        </div>
      )}

      {roleIds.length > 0 && (
        <div className="space-y-5">
          {roleHeading && (
            <div className="text-base font-semibold text-foreground/80">
              {roleHeading}
            </div>
          )}
          {roleIds.map((roleId) => {
            const roleElement = spec.elements[roleId];
            if (!roleElement) return null;
            if (roleElement.type === "Image") {
              const imgSrc = (roleElement.props?.src ??
                roleElement.props?.url ??
                "") as string;
              if (!imgSrc) return null;
              const childProps = (roleElement.props ?? {}) as Record<
                string,
                unknown
              >;
              return (
                <MediaTransitionShell
                  key={roleId}
                  className="h-[198px] w-[148px] shrink-0 inline-block"
                  loadingClassName="h-[198px] w-[148px] rounded-2xl object-cover"
                >
                  {({ setReady }) => (
                    <PreviewableImageFigure
                      src={imgSrc}
                      alt={(roleElement.props?.alt ?? "") as string}
                      overlayTitle={
                        roleElement.props?.overlayTitle as string | undefined
                      }
                      overlayDescription={
                        roleElement.props?.overlayDescription as
                          | string
                          | undefined
                      }
                      detailType={
                        roleElement.props?.detailType as string | undefined
                      }
                      detailTags={
                        Array.isArray(roleElement.props?.detailTags)
                          ? roleElement.props.detailTags.filter(
                              (t): t is string => typeof t === "string",
                            )
                          : undefined
                      }
                      detailSections={
                        Array.isArray(roleElement.props?.detailSections)
                          ? (roleElement.props.detailSections as Array<{
                              label: string;
                              value: string;
                            }>)
                          : undefined
                      }
                      candidates={extractCandidates(childProps)}
                      figureClassName="w-[148px] h-[198px] shrink-0 overflow-hidden rounded-2xl"
                      onLoad={() => setReady(true)}
                    />
                  )}
                </MediaTransitionShell>
              );
            }
            const roleName = (roleElement.props?.title ??
              roleElement.props?.name ??
              "") as string;
            const { descriptions, mediaItems } = collectRoleInfo(
              spec,
              roleElement.children ?? [],
            );
            if (mediaItems.length === 0 && descriptions.length === 0)
              return null;
            return (
              <div key={roleId} className="space-y-2">
                <div className="text-base font-medium text-foreground">
                  {roleName}
                </div>
                {descriptions.map((desc, i) => (
                  <p
                    key={i}
                    className="text-sm leading-6 text-muted-foreground pl-6"
                  >
                    {desc}
                  </p>
                ))}
                {mediaItems.length > 0 && (
                  <div className="flex flex-wrap gap-3 pl-6">
                    {mediaItems.map((item, idx) =>
                      item.type === "pending" ? (
                        <PendingMediaTile
                          key={idx}
                          progress={item.progress ?? 58}
                        />
                      ) : (
                        <MediaTransitionShell
                          key={idx}
                          className="h-[198px] w-[148px] shrink-0"
                          loadingClassName="h-[198px] w-[148px] rounded-2xl object-cover"
                        >
                          {({ setReady }) => (
                            <PreviewableImageFigure
                              src={item.src ?? ""}
                              alt={item.alt}
                              overlayTitle={item.overlayTitle}
                              overlayDescription={item.overlayDescription}
                              detailType={item.detailType}
                              detailTags={item.detailTags}
                              candidates={item.candidates}
                              figureClassName="w-[148px] h-[198px] shrink-0 overflow-hidden rounded-2xl"
                              onLoad={() => setReady(true)}
                            />
                          )}
                        </MediaTransitionShell>
                      ),
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <AlertList alerts={alertItems} className="mt-4 space-y-2" />
    </div>
  );
};
