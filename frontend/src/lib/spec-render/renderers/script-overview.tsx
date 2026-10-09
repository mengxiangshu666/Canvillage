"use client";

import type { RootRendererFn } from "../types";
import { p, coerceText } from "../types";
import type { Spec } from "../spec";
import { DefaultRootRenderer } from "../core";
import { AlertList, collectAlerts } from "./shared";

function extractCardText(spec: Spec, cardId: string): string {
  const card = spec.elements[cardId];
  if (!card) return "";
  for (const cid of card.children ?? []) {
    const child = spec.elements[cid];
    if (child?.type === "Text") return coerceText(child.props?.content ?? child.props?.text);
  }
  return "";
}

type Beat = {
  id: string;
  title: string;
  sections: Array<{ title: string; content: string; variant?: string }>;
};
type MetaItem = { key: string; value: string };
type Episode = {
  id: string;
  title: string;
  description?: string;
  meta: MetaItem[];
  beats: Beat[];
  alerts: Array<{ variant?: string; title?: string; message?: string }>;
};

function processBeats(
  spec: Spec,
  childIds: string[],
  extractText: (id: string) => string,
): Beat[] {
  const beats: Beat[] = [];

  const extractSections = (elementIds: string[]): Beat["sections"] => {
    const sections: Beat["sections"] = [];
    for (const sectionId of elementIds) {
      const section = spec.elements[sectionId];
      if (!section) continue;

      if (section.type === "Card") {
        const sTitle = coerceText(section.props?.title);
        const sContent = extractText(sectionId);
        const textChild = (section.children ?? [])
          .map((c) => spec.elements[c])
          .find((c) => c?.type === "Text");
        const variant = (textChild?.props?.variant ?? "body") as string;
        if (sContent.trim()) sections.push({ title: sTitle, content: sContent, variant });
      } else if (section.type === "Stack") {
        sections.push(...extractSections(section.children ?? []));
      } else if (section.type === "KeyValue") {
        const items = (section.props?.items ?? []) as Array<Record<string, unknown>>;
        for (const rawItem of items) {
          const itemKey = coerceText(rawItem?.key);
          const itemValue = coerceText(rawItem?.value);
          if (itemValue.trim()) {
            sections.push({ title: itemKey, content: itemValue });
          }
        }
      } else if (section.type === "Text") {
        const content = coerceText(section.props?.content ?? section.props?.text);
        const variant = (section.props?.variant ?? "body") as string;
        if (content.trim()) {
          const prefixMap: Record<string, string> = {
            旁白: "解说词",
            画面: "画面描述",
            置景: "置景描述",
            对白: "对白",
            出场人物: "出场人物",
            画面描述: "画面描述",
            置景描述: "置景描述",
            解说词: "解说词",
          };
          const prefixMatch = content.match(
            /^(出场人物|画面描述|置景描述|解说词|旁白|画面|置景|对白(?:（[^）]+）)?)\s*[：:]\s*/,
          );
          const sTitle = prefixMatch
            ? (prefixMap[prefixMatch[1].replace(/（.*）/, "")] ?? prefixMatch[1])
            : "";
          const sContent = prefixMatch ? content.slice(prefixMatch[0].length) : content;
          sections.push({ title: sTitle, content: sContent, variant });
        }
      }
    }
    return sections;
  };

  for (const childId of childIds) {
    const child = spec.elements[childId];
    if (!child || child.type !== "Card") continue;
    const beatTitle = coerceText(child.props?.title);
    const sections = extractSections(child.children ?? []);
    beats.push({ id: childId, title: beatTitle, sections });
  }
  return beats;
}

function processEpisode(spec: Spec, epId: string): Episode | null {
  const ep = spec.elements[epId];
  if (!ep || ep.type !== "Card") return null;
  const epTitle = coerceText(ep.props?.title);
  const epDesc = coerceText(ep.props?.description) || undefined;
  const meta: MetaItem[] = [];
  const beats: Beat[] = [];
  const epAlerts = collectAlerts(spec, ep.children ?? []);
  const extractText = (id: string) => extractCardText(spec, id);

  for (const childId of ep.children ?? []) {
    const child = spec.elements[childId];
    if (!child) continue;
    if (child.type === "KeyValue") {
      const items = (child.props?.items ?? []) as Array<Record<string, unknown>>;
      for (const rawItem of items) {
        const key = coerceText(rawItem?.key);
        const value = coerceText(rawItem?.value);
        if (key || value) meta.push({ key, value });
      }
    } else if (child.type === "Stack") {
      beats.push(...processBeats(spec, child.children ?? [], extractText));
    } else if (child.type === "Card") {
      beats.push(...processBeats(spec, [childId], extractText));
    }
  }
  return { id: epId, title: epTitle, description: epDesc, meta, beats, alerts: epAlerts };
}

export const scriptOverviewRenderer: RootRendererFn = ({ spec }) => {
  const rootElement = spec.elements[spec.root];
  const rootProps = rootElement ? p(rootElement) : {};
  const rootTitle = coerceText(rootProps.title) || undefined;
  const rootDescription = coerceText(rootProps.description) || undefined;
  const rootChildren = rootElement?.children ?? [];

  const episodes: Episode[] = [];

  if (rootElement?.type === "Card") {
    let handledAsMultiEpisode = false;
    for (const childId of rootChildren) {
      const child = spec.elements[childId];
      if (child?.type !== "Stack") continue;
      const stackKids = child.children ?? [];
      const isNested = stackKids.some((sid) => {
        const s = spec.elements[sid];
        return (
          s?.type === "Card" &&
          (s.children ?? []).some((gc) => {
            const g = spec.elements[gc];
            return g?.type === "Stack" || g?.type === "Alert";
          })
        );
      });
      if (isNested) {
        handledAsMultiEpisode = true;
        for (const epId of stackKids) {
          const ep = processEpisode(spec, epId);
          if (ep) episodes.push(ep);
        }
        break;
      }
    }
    if (!handledAsMultiEpisode) {
      const ep = processEpisode(spec, spec.root);
      if (ep) episodes.push(ep);
    }
  } else {
    let foundEpisodes = false;
    for (const childId of rootChildren) {
      const ep = processEpisode(spec, childId);
      if (ep && ep.beats.length > 0) {
        episodes.push(ep);
        foundEpisodes = true;
      }
    }
    if (!foundEpisodes) {
      let headerCard: { title: string; description?: string; meta: MetaItem[] } | null = null;
      const allBeats: Beat[] = [];
      const extractText = (id: string) => extractCardText(spec, id);
      for (const childId of rootChildren) {
        const child = spec.elements[childId];
        if (!child) continue;
        if (
          child.type === "Card" &&
          !(child.children ?? []).some((cid) => {
            const c = spec.elements[cid];
            return c?.type === "Card";
          })
        ) {
          const meta: MetaItem[] = [];
          for (const cid of child.children ?? []) {
            const c = spec.elements[cid];
            if (c?.type === "KeyValue") {
              const items = (c.props?.items ?? []) as Array<Record<string, unknown>>;
              for (const rawItem of items) {
                const key = coerceText(rawItem?.key);
                const value = coerceText(rawItem?.value);
                if (key || value) meta.push({ key, value });
              }
            }
          }
          headerCard = {
            title: coerceText(child.props?.title),
            description: coerceText(child.props?.description) || undefined,
            meta,
          };
        } else if (child.type === "Stack") {
          allBeats.push(...processBeats(spec, child.children ?? [], extractText));
        } else if (child.type === "Card") {
          allBeats.push(...processBeats(spec, [childId], extractText));
        }
      }
      if (allBeats.length > 0) {
        episodes.push({
          id: spec.root,
          title: headerCard?.title || rootTitle || "",
          description: headerCard?.description || rootDescription,
          meta: headerCard?.meta ?? [],
          beats: allBeats,
          alerts: collectAlerts(spec, rootChildren),
        });
      }
    }
  }

  const rootAlerts = collectAlerts(spec, rootChildren);

  if (episodes.length === 0 && rootTitle) {
    episodes.push({
      id: spec.root,
      title: rootTitle,
      description: rootDescription,
      meta: [],
      beats: [],
      alerts: rootAlerts,
    });
  }

  const totalBeats = episodes.reduce((sum, ep) => sum + ep.beats.length, 0);
  if (totalBeats === 0) {
    return <DefaultRootRenderer spec={spec} context={{ spec, rendererKey: "script_overview" }} />;
  }

  const isMultiEpisode = episodes.length > 1 || episodes[0]?.id !== spec.root;

  return (
    <div className="max-w-4xl max-h-[70vh] overflow-y-auto space-y-6 text-foreground pr-1">
      {isMultiEpisode && (rootTitle || rootDescription) && (
        <div className="overflow-hidden rounded-[20px] bg-transparent p-5">
          {rootTitle && (
            <div className="mb-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
              <span>🎬</span>
              <span>{rootTitle}</span>
            </div>
          )}
          {rootDescription && (
            <div className="mb-2 text-sm text-muted-foreground">{rootDescription}</div>
          )}
          <AlertList alerts={rootAlerts} className="mt-3 space-y-2" />
        </div>
      )}

      {episodes.map((ep) => (
        <div key={ep.id} className="overflow-hidden rounded-[20px] bg-transparent p-5">
          <div className="mb-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
            <span>🎬</span>
            <span>{ep.title}</span>
          </div>
          {ep.description && (
            <div className="mb-2 text-sm text-muted-foreground">{ep.description}</div>
          )}
          {ep.meta.length > 0 && (
            <div className="mb-4 flex flex-wrap gap-3 text-xs text-muted-foreground">
              {ep.meta.map((m, i) => (
                <span key={i}>
                  <span className="text-muted-foreground/70">{m.key}:</span> {m.value}
                </span>
              ))}
            </div>
          )}

          {ep.alerts.length > 0 && ep.beats.length === 0 && <AlertList alerts={ep.alerts} />}

          {ep.beats.length > 0 && (
            <div className="relative ml-2">
              <div className="absolute left-[5px] top-2 bottom-0 w-px bg-border" />
              <div className="space-y-5">
                {ep.beats.map((beat, beatIdx) => (
                  <div key={beat.id} className="relative pl-7">
                    <div className="absolute left-0 top-[6px] size-[11px] rounded-full border-2 border-sky-400 bg-sky-400/30 dark:border-[#80E4FF] dark:bg-[#80E4FF]/30" />
                    <div className="mb-3 text-base font-semibold text-sky-400 dark:text-[#80E4FF]">
                      {beat.title || `Beat ${String(beatIdx + 1).padStart(2, "0")}`}
                    </div>
                    <div className="space-y-2">
                      {beat.sections.map((section, sIdx) => (
                        <div key={sIdx} className="rounded-xl bg-muted/50 px-4 py-3">
                          <div className="mb-1.5 text-[13px] font-medium text-muted-foreground">
                            【{section.title}】
                          </div>
                          <p
                            className={`whitespace-pre-wrap leading-6 ${
                              section.variant === "caption"
                                ? "text-[13px] italic text-muted-foreground"
                                : "text-sm text-foreground/80"
                            }`}
                          >
                            {section.content}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
};
