import type { Spec, UIElement } from "./spec";
import type { ReactNode } from "react";

export type Props = Record<string, unknown>;

export type RenderContext = {
  spec: Spec;
  rendererKey: string;
  components?: Partial<Record<string, ComponentFn>>;
};

export type ComponentFn = (props: {
  element: UIElement;
  children: ReactNode[];
}) => ReactNode;

export type RootRendererProps = {
  spec: Spec;
  context: RenderContext;
};

export type RootRendererFn = (props: RootRendererProps) => ReactNode;

export function p(element: UIElement): Props {
  return (element.props ?? {}) as Props;
}

export function coerceText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (Array.isArray(value)) {
    return value
      .map((v) => coerceText(v))
      .filter(Boolean)
      .join("\n");
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if (typeof obj.text === "string") return obj.text;
    if (typeof obj.content === "string") return obj.content;
    if (typeof obj.value === "string") return obj.value;
    if (typeof obj.label === "string") return obj.label;
  }
  return "";
}
