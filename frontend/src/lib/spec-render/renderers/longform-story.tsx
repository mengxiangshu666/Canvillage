"use client";

import type { RootRendererFn } from "../types";
import { p } from "../types";
import { renderElementWithOverrides } from "../core";
import { extractKeyValueItems } from "../spec-helpers";
import { StoryText, StoryHeading } from "../showcase-components";

export const longformStoryRenderer: RootRendererFn = ({ spec, context }) => {
  const rootElement = spec.elements[spec.root];
  const rootProps = rootElement ? p(rootElement) : {};
  const title = rootProps.title as string | undefined;
  const description = rootProps.description as string | undefined;
  const rootChildren = rootElement?.children ?? [];

  const metaId = rootChildren.find((id) => spec.elements[id]?.type === "KeyValue");
  const separatorId = rootChildren.find((id) => spec.elements[id]?.type === "Separator");
  const paragraphsId = rootChildren.find((id) => spec.elements[id]?.type === "Stack");

  const metaItems = metaId ? extractKeyValueItems(spec, metaId) : [];
  const paragraphIds = paragraphsId
    ? (spec.elements[paragraphsId]?.children ?? [])
    : rootChildren.filter((id) => {
        const el = spec.elements[id];
        return el?.type === "Text" || el?.type === "Heading";
      });

  return (
    <div className="max-w-[860px] rounded-[30px] bg-transparent px-5 py-5 text-foreground">
      <div className="rounded-[26px] bg-muted/50 px-5 py-5">
        {title && (
          <div className="mb-4 flex items-center gap-3">
            <span className="text-[22px] text-muted-foreground">🎬</span>
            <div className="min-w-0">
              <div className="truncate text-[18px] font-semibold">{title}</div>
              {description && (
                <div className="mt-1 text-[13px] text-muted-foreground">{description}</div>
              )}
            </div>
          </div>
        )}

        {metaItems.length > 0 && (
          <div className="mb-4 flex flex-wrap gap-3">
            {metaItems.map((item) => (
              <div
                key={item.key}
                className="rounded-2xl bg-muted px-3 py-2 text-sm text-foreground/80"
              >
                <span className="text-muted-foreground">{item.key}</span>
                <span className="mx-2 text-border">/</span>
                <span>{item.value}</span>
              </div>
            ))}
          </div>
        )}

        {separatorId && (
          <div className="mb-4">{renderElementWithOverrides(spec, context, separatorId, {})}</div>
        )}

        <div className="max-h-[430px] overflow-y-auto pr-2">
          <div className="space-y-6">
            {paragraphIds.map((childId) => (
              <div key={childId}>
                {renderElementWithOverrides(spec, context, childId, {
                  Heading: StoryHeading,
                  Text: StoryText,
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
