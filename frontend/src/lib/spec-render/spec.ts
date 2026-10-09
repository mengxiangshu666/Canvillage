/**
 * Spec 类型定义 + 标准化（flat + nested）。
 *
 * Spec 是扁平 UI 树：{ type?, root, elements }。
 * 消费方把原始 JSON 丢进来，用 normalizeSpec / flattenNestedSpec 得到标准结构，
 * 然后交给 <SpecRenderer /> 渲染。
 */

export type Spec = {
  type?: string;
  root: string;
  elements: Record<string, UIElement>;
};

export type UIElement = {
  type: string;
  props?: Record<string, unknown>;
  children?: string[];
};

/** 文本与 spec 交替段（可选工具，调用方按需使用） */
export type ContentSegment =
  | { kind: "text"; text: string }
  | { kind: "spec"; spec: Spec };

// ─── 类型标准化映射表 ──────────────────────────────────────────

const ELEMENT_TYPE_MAP: Record<string, string> = {
  card: "Card",
  text: "Text",
  heading: "Heading",
  table: "Table",
  list: "List",
  badge: "Badge",
  alert: "Alert",
  image: "Image",
  audio: "Audio",
  video: "Video",
  stack: "Stack",
  separator: "Separator",
  progress: "Progress",
  link: "Link",
  code: "Code",
  collapsible: "Collapsible",
  gallery: "Gallery",
  keyvalue: "KeyValue",
};

const SPEC_TYPE_ALIASES: Record<string, string> = {
  character_showcase: "character_showcase",
  character_list: "character_showcase",
  character_gallery: "character_showcase",
  character_info: "character_showcase",
  character_display: "character_showcase",
  characters: "character_showcase",
  keyframe_video: "keyframe_video",
  video_preview: "keyframe_video",
  video_gallery: "keyframe_video",
  video_showcase: "keyframe_video",
  video_list: "keyframe_video",
  keyframe: "keyframe_video",
  sketch_gallery: "sketch_gallery",
  sketch: "sketch_gallery",
  image_gallery: "sketch_gallery",
  storyboard: "sketch_gallery",
  episode_breakdown: "episode_breakdown",
  episode: "episode_breakdown",
  episode_plan: "episode_breakdown",
  episode_detail: "episode_breakdown",
  episode_analysis: "episode_breakdown",
  scene_breakdown: "episode_breakdown",
  plot_breakdown: "episode_breakdown",
  script_overview: "script_overview",
  script: "script_overview",
  script_detail: "script_overview",
  screenplay: "script_overview",
  story_overview: "script_overview",
  longform_story: "longform_story",
  story: "longform_story",
  long_story: "longform_story",
  narrative: "longform_story",
  story_content: "longform_story",
};

function normalizeElementType(type: string): string {
  const key = type.toLowerCase().replace(/[-_\s]/g, "");
  return ELEMENT_TYPE_MAP[key] ?? type.charAt(0).toUpperCase() + type.slice(1);
}

export function normalizeSpecType(
  type: string | undefined,
): string | undefined {
  if (!type) return undefined;
  const key = type
    .toLowerCase()
    .replace(/[\s-]+/g, "_")
    .replace(/_+/g, "_");
  return SPEC_TYPE_ALIASES[key] ?? type;
}

// ─── Spec 校验 ─────────────────────────────────────────────────

export function isValidSpec(spec: unknown): boolean {
  if (!spec || typeof spec !== "object") return false;
  const s = spec as Record<string, unknown>;
  return (
    typeof s.root === "string" &&
    s.elements != null &&
    typeof s.elements === "object" &&
    (s.type === undefined || typeof s.type === "string")
  );
}

export function isNestedSpec(obj: Record<string, unknown>): boolean {
  if (typeof obj.type !== "string") return false;
  if ("elements" in obj && obj.elements != null) return false;
  return true;
}

// ─── 扁平 Spec 标准化 ─────────────────────────────────────────

function rescueStrayElements(spec: Spec): Record<string, UIElement> {
  const SPEC_KEYS = new Set(["type", "root", "elements"]);
  const elements: Record<string, UIElement> = { ...spec.elements };
  const rawSpec = spec as unknown as Record<string, unknown>;

  for (const [key, val] of Object.entries(rawSpec)) {
    if (SPEC_KEYS.has(key) || key in elements) continue;
    if (
      val &&
      typeof val === "object" &&
      typeof (val as Record<string, unknown>).type === "string"
    ) {
      elements[key] = val as UIElement;
    }
  }
  return elements;
}

function createIdGenerator(
  elements: Record<string, UIElement>,
): () => string {
  let counter = 0;
  return () => {
    while (`_n${counter}` in elements) counter++;
    return `_n${counter++}`;
  };
}

function flattenInlineChild(
  child: Record<string, unknown>,
  elements: Record<string, UIElement>,
  nextId: () => string,
): string {
  const id = typeof child.id === "string" ? child.id : nextId();
  const {
    type,
    children: rawChildren,
    id: _id,
    props: nodeProps,
    ...rest
  } = child;

  const childIds: string[] = [];
  if (Array.isArray(rawChildren)) {
    for (const c of rawChildren) {
      if (typeof c === "string") {
        childIds.push(c);
      } else if (
        c &&
        typeof c === "object" &&
        typeof (c as Record<string, unknown>).type === "string"
      ) {
        childIds.push(
          flattenInlineChild(c as Record<string, unknown>, elements, nextId),
        );
      }
    }
  }

  const mergedProps: Record<string, unknown> = {
    ...(nodeProps && typeof nodeProps === "object"
      ? (nodeProps as Record<string, unknown>)
      : {}),
    ...rest,
  };

  const el: UIElement = { type: normalizeElementType(type as string) };
  if (Object.keys(mergedProps).length > 0) el.props = mergedProps;
  if (childIds.length > 0) el.children = childIds;
  elements[id] = el;
  return id;
}

function processElement(
  el: UIElement,
  elements: Record<string, UIElement>,
  nextId: () => string,
): UIElement {
  const RESERVED = new Set(["type", "children", "props"]);
  const raw = el as unknown as Record<string, unknown>;

  const extraFields: Record<string, unknown> = {};
  for (const k of Object.keys(raw)) {
    if (!RESERVED.has(k)) extraFields[k] = raw[k];
  }
  const hasExtraFields = Object.keys(extraFields).length > 0;

  let resolvedChildren = el.children;
  let hasInlineChildren = false;
  if (Array.isArray(el.children)) {
    const newChildIds: string[] = [];
    for (const child of el.children as unknown[]) {
      if (typeof child === "string") {
        newChildIds.push(child);
      } else if (
        child &&
        typeof child === "object" &&
        typeof (child as Record<string, unknown>).type === "string"
      ) {
        hasInlineChildren = true;
        newChildIds.push(
          flattenInlineChild(
            child as Record<string, unknown>,
            elements,
            nextId,
          ),
        );
      }
    }
    if (hasInlineChildren) resolvedChildren = newChildIds;
  }

  if (!hasExtraFields && !hasInlineChildren) return el;

  return {
    type: el.type,
    props: hasExtraFields
      ? { ...extraFields, ...(el.props ?? {}) }
      : el.props,
    children: resolvedChildren,
  };
}

function normalizeAllElementTypes(
  elements: Record<string, UIElement>,
): void {
  for (const [key, el] of Object.entries(elements)) {
    const normalized = normalizeElementType(el.type);
    if (normalized !== el.type) {
      elements[key] = { ...el, type: normalized };
    }
  }
}

export function normalizeSpec(spec: Spec): Spec {
  const elements = rescueStrayElements(spec);
  const nextId = createIdGenerator(elements);

  for (const [key, el] of Object.entries(spec.elements)) {
    elements[key] = processElement(el, elements, nextId);
  }

  normalizeAllElementTypes(elements);

  let root = spec.root;
  if (!elements[root]) {
    const referenced = new Set<string>();
    for (const el of Object.values(elements)) {
      for (const childId of el.children ?? []) {
        referenced.add(childId);
      }
    }
    const orphans = Object.keys(elements).filter((id) => !referenced.has(id));
    if (orphans.length === 1) {
      root = orphans[0];
    } else if (orphans.length > 1) {
      root =
        orphans.find((id) => {
          const t = elements[id].type;
          return t === "Stack" || t === "Card";
        }) ?? orphans[0];
    }
  }

  return { ...spec, type: normalizeSpecType(spec.type), root, elements };
}

// ─── 嵌套树 → 扁平 Spec ───────────────────────────────────────

export function flattenNestedSpec(tree: Record<string, unknown>): Spec {
  const elements: Record<string, UIElement> = {};
  let counter = 0;
  const specType = typeof tree.type === "string" ? tree.type : undefined;

  function walk(node: Record<string, unknown>): string {
    const id = typeof node.id === "string" ? node.id : `e${counter++}`;
    const {
      type,
      children: rawChildren,
      id: _id,
      props: nodeProps,
      root: _r,
      ...rest
    } = node;

    const mergedProps: Record<string, unknown> = {
      ...(nodeProps && typeof nodeProps === "object"
        ? (nodeProps as Record<string, unknown>)
        : {}),
      ...rest,
    };

    const childIds: string[] = [];

    if (Array.isArray(rawChildren)) {
      for (const child of rawChildren) {
        if (child && typeof child === "object") {
          childIds.push(walk(child as Record<string, unknown>));
        }
      }
    }

    if (Array.isArray(mergedProps.items)) {
      const firstItem = mergedProps.items[0];
      const looksLikeElements =
        firstItem &&
        typeof firstItem === "object" &&
        "type" in (firstItem as Record<string, unknown>);
      if (looksLikeElements) {
        for (const item of mergedProps.items) {
          if (item && typeof item === "object") {
            childIds.push(walk(item as Record<string, unknown>));
          }
        }
        delete mergedProps.items;
      }
    }

    const el: UIElement = { type: normalizeElementType(type as string) };
    if (Object.keys(mergedProps).length > 0) el.props = mergedProps;
    if (childIds.length > 0) el.children = childIds;
    elements[id] = el;
    return id;
  }

  let rootId: string;
  if (typeof tree.root === "string" && Array.isArray(tree.children)) {
    const childIds: string[] = [];
    for (const child of tree.children as Array<unknown>) {
      if (child && typeof child === "object") {
        childIds.push(walk(child as Record<string, unknown>));
      }
    }
    elements["root"] = {
      type: "Stack",
      props: { direction: "column", gap: 12 },
      children: childIds,
    };
    rootId = "root";
  } else {
    rootId = walk(tree);
  }

  return { type: normalizeSpecType(specType), root: rootId, elements };
}
