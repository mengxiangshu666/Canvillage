"use client";

import type { ReactNode } from "react";
import type { Spec } from "./spec";
import type { RenderContext, ComponentFn, RootRendererFn } from "./types";
import { COMPONENTS } from "./components";

export function renderElement(
  context: RenderContext,
  elementId: string,
): ReactNode {
  const { spec, components } = context;
  const element = spec.elements[elementId];
  if (!element) return null;

  const childIds = element.children ?? [];
  const children = childIds.map((id) => (
    <RenderNode key={id} context={context} elementId={id} />
  ));

  const Component = components?.[element.type] ?? COMPONENTS[element.type];
  if (!Component) {
    return <>{children}</>;
  }
  return <Component element={element}>{children}</Component>;
}

export function RenderNode({
  context,
  elementId,
}: {
  context: RenderContext;
  elementId: string;
}) {
  return <>{renderElement(context, elementId)}</>;
}

export const DefaultRootRenderer: RootRendererFn = ({ spec, context }) => {
  return <RenderNode context={context} elementId={spec.root} />;
};

export function renderRootWithOverrides(
  spec: Spec,
  context: RenderContext,
  overrides: Partial<Record<string, ComponentFn>> = {},
): ReactNode {
  const nextContext: RenderContext = {
    ...context,
    components: { ...context.components, ...overrides },
  };
  return <RenderNode context={nextContext} elementId={spec.root} />;
}

export function renderElementWithOverrides(
  _spec: Spec,
  context: RenderContext,
  elementId: string,
  overrides: Partial<Record<string, ComponentFn>> = {},
): ReactNode {
  const nextContext: RenderContext = {
    ...context,
    components: { ...context.components, ...overrides },
  };
  return <RenderNode context={nextContext} elementId={elementId} />;
}
