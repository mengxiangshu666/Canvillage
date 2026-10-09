import {
  buildCanvasAgentModelCatalog,
  buildCanvasDirectorState,
  buildCanvasNodeIdentity,
  type CanvasAgentModelCatalogSnapshot,
} from "@/features/superchat/canvas-agent-director-state";
import { useCanvasStore } from "@/stores/canvasStore";



export function currentCanvasAgentContext(
  canvasId?: string,
  projectId?: string,
  revision?: number | null,
  projectStyleId?: string | null,
  modelCatalog?: CanvasAgentModelCatalogSnapshot,
): string {
  const state = useCanvasStore.getState();
  const counts: Record<string, number> = {};
  for (const node of state.nodes) counts[node.type] = (counts[node.type] ?? 0) + 1;
  const selected = state.nodes.find((node) => node.id === state.selectedNodeId);
  const data = selected?.data as Record<string, unknown> | undefined;
  const viewport = state.currentViewport;
  const viewportWidth = state.canvasViewportSize.width;
  const viewportHeight = state.canvasViewportSize.height;
  const viewportZoom = Number.isFinite(viewport.zoom) && viewport.zoom > 0 ? viewport.zoom : 1;
  const hasViewport = viewportWidth > 0 && viewportHeight > 0;
  const viewportMinX = (0 - viewport.x) / viewportZoom;
  const viewportMinY = (0 - viewport.y) / viewportZoom;
  const viewportMaxX = (viewportWidth - viewport.x) / viewportZoom;
  const viewportMaxY = (viewportHeight - viewport.y) / viewportZoom;
  const compactNumber = (value: number) => Math.round(value * 100) / 100;
  const viewportCenterX = (viewportMinX + viewportMaxX) / 2;
  const viewportCenterY = (viewportMinY + viewportMaxY) / 2;
  const resolvedCanvasId =
    canvasId?.trim()
    || new URLSearchParams(window.location.search).get("canvas")
    || "default";
  const relevantNodes = state.nodes.map((node) => {
    const x = Number.isFinite(node.position.x) ? node.position.x : 0;
    const y = Number.isFinite(node.position.y) ? node.position.y : 0;
    const width = Number.isFinite(node.measured?.width)
      ? Number(node.measured?.width)
      : Number.isFinite(node.width)
        ? Number(node.width)
        : 320;
    const height = Number.isFinite(node.measured?.height)
      ? Number(node.measured?.height)
      : Number.isFinite(node.height)
        ? Number(node.height)
        : 240;
    const inViewport = node.hidden !== true
      && hasViewport
      && x + width >= viewportMinX
      && x <= viewportMaxX
      && y + height >= viewportMinY
      && y <= viewportMaxY;
    const centerX = x + width / 2;
    const centerY = y + height / 2;
    return {
      node,
      x,
      y,
      width,
      height,
      inViewport,
      selected: node.id === state.selectedNodeId || node.selected === true,
      distanceFromViewport: Math.hypot(centerX - viewportCenterX, centerY - viewportCenterY),
    };
  });
  const selectedNodes = relevantNodes.filter((item) => item.selected);
  const viewportNodes = relevantNodes
    .filter((item) => !item.selected && item.inViewport)
    .sort((left, right) => left.distanceFromViewport - right.distanceFromViewport);
  const offscreenNodes = relevantNodes.filter((item) => !item.selected && !item.inViewport);
  const orderedNodes = [...selectedNodes, ...viewportNodes, ...offscreenNodes];
  const visibleNodeCount = relevantNodes.reduce(
    (total, item) => total + (item.inViewport ? 1 : 0),
    0,
  );
  const canvasOutline = orderedNodes.slice(0, 80).map((item) => {
    const nodeData = item.node.data as Record<string, unknown> | undefined;
    const identity = buildCanvasNodeIdentity(item.node, resolvedCanvasId);
    return {
      id: item.node.id,
      node_uri: identity.node_uri,
      type: item.node.type,
      display_name: nodeData?.displayName ?? nodeData?.label ?? "",
      asset_id: identity.asset_id,
      asset_uri: identity.asset_uri,
      parent_id: identity.parent_id,
      role: typeof nodeData?.reference_role === "string"
        ? nodeData.reference_role
        : typeof nodeData?.referenceRole === "string"
          ? nodeData.referenceRole
          : typeof nodeData?.output_role === "string"
            ? nodeData.output_role
            : typeof nodeData?.role === "string"
              ? nodeData.role
              : null,
      position: { x: compactNumber(item.x), y: compactNumber(item.y) },
      size: { width: compactNumber(item.width), height: compactNumber(item.height) },
      selected: item.selected,
      in_viewport: item.inViewport,
      has_prompt: typeof nodeData?.prompt === "string" && Boolean(nodeData.prompt.trim()),
      has_image: Boolean(
        nodeData?.imageUrl
        ?? nodeData?.image_url
        ?? nodeData?.previewImageUrl
        ?? nodeData?.preview_image_url
        ?? nodeData?.outputImageUrl
        ?? nodeData?.output_image_url
        ?? nodeData?.referenceImageUrl
        ?? nodeData?.reference_image_url
        ?? nodeData?.committedSlotUrl
        ?? nodeData?.committed_slot_url
        ?? nodeData?.mediaUrl
        ?? nodeData?.media_url
        ?? nodeData?.fileUrl
        ?? nodeData?.file_url,
      ),
      has_video: Boolean(
        nodeData?.videoUrl
        ?? nodeData?.video_url
        ?? nodeData?.outputVideoUrl
        ?? nodeData?.output_video_url
        ?? nodeData?.resultVideoUrl
        ?? nodeData?.result_video_url
        ?? nodeData?.previewVideoUrl
        ?? nodeData?.preview_video_url
        ?? nodeData?.sourceVideoUrl
        ?? nodeData?.source_video_url,
      ),
      has_audio: Boolean(
        nodeData?.audioUrl
        ?? nodeData?.audio_url
        ?? nodeData?.outputAudioUrl
        ?? nodeData?.output_audio_url
        ?? nodeData?.resultAudioUrl
        ?? nodeData?.result_audio_url
        ?? nodeData?.previewAudioUrl
        ?? nodeData?.preview_audio_url
        ?? nodeData?.sourceAudioUrl
        ?? nodeData?.source_audio_url,
      ),
      model_id: typeof nodeData?.model === "string" ? nodeData.model : null,
      generation_status: typeof nodeData?.generationStatus === "string"
        ? nodeData.generationStatus
        : null,
      reference_order: Array.isArray(nodeData?.referenceOrder)
        ? nodeData.referenceOrder.filter((value): value is string => typeof value === "string").slice(0, 24)
        : [],
    };
  });
  const edgeOutline = state.edges.slice(0, 160).map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
  }));
  const directorState = buildCanvasDirectorState(
    state.nodes,
    state.edges,
    state.selectedNodeId,
    projectStyleId,
    resolvedCanvasId,
  );
  const referenceManifest = directorState.reference_manifest;
  const selectedIdentity = selected ? buildCanvasNodeIdentity(selected, resolvedCanvasId) : null;
  return JSON.stringify({
    project_id: projectId?.trim() || "",
    canvas_id: resolvedCanvasId,
    revision: Number.isSafeInteger(revision) && (revision as number) > 0 ? revision : null,
    project_style_id: projectStyleId?.trim() || null,
    node_count: state.nodes.length,
    edge_count: state.edges.length,
    node_type_counts: counts,
    canvas_outline: canvasOutline,
    canvas_outline_policy: "selected_then_viewport_then_canvas_order",
    identity_policy: {
      node_uri: "canvas://{canvas_id}/nodes/{node_id}",
      label_is_display_only: true,
      bind_by: ["node_id", "asset_id", "asset_uri", "node_uri"],
      parent_id_is_group_context: true,
      do_not_guess_from_filename: true,
    },
    visible_node_count: visibleNodeCount,
    edge_outline: edgeOutline,
    outline_truncated: {
      nodes: state.nodes.length > canvasOutline.length,
      edges: state.edges.length > edgeOutline.length,
    },
    director_state: directorState,
    reference_manifest: referenceManifest,
    model_catalog: modelCatalog ?? buildCanvasAgentModelCatalog(),
    selected_node_id: selected?.id ?? null,
    selected_node: selected ? {
      id: selected.id,
      node_uri: selectedIdentity?.node_uri ?? null,
      type: selected.type,
      display_name: data?.displayName ?? data?.label ?? "",
      asset_id: selectedIdentity?.asset_id ?? null,
      asset_uri: selectedIdentity?.asset_uri ?? null,
      parent_id: selectedIdentity?.parent_id ?? null,
      role: typeof data?.reference_role === "string"
        ? data.reference_role
        : typeof data?.referenceRole === "string"
          ? data.referenceRole
          : typeof data?.output_role === "string"
            ? data.output_role
            : typeof data?.role === "string"
              ? data.role
              : null,
      has_prompt: typeof data?.prompt === "string" && Boolean(data.prompt.trim()),
      has_image: Boolean(
        data?.imageUrl
        ?? data?.image_url
        ?? data?.previewImageUrl
        ?? data?.preview_image_url
        ?? data?.outputImageUrl
        ?? data?.output_image_url
        ?? data?.referenceImageUrl
        ?? data?.reference_image_url
        ?? data?.committedSlotUrl
        ?? data?.committed_slot_url
        ?? data?.mediaUrl
        ?? data?.media_url
        ?? data?.fileUrl
        ?? data?.file_url,
      ),
      has_video: Boolean(
        data?.videoUrl
        ?? data?.video_url
        ?? data?.outputVideoUrl
        ?? data?.output_video_url
        ?? data?.resultVideoUrl
        ?? data?.result_video_url
        ?? data?.previewVideoUrl
        ?? data?.preview_video_url
        ?? data?.sourceVideoUrl
        ?? data?.source_video_url,
      ),
      model_id: typeof data?.model === "string" ? data.model : null,
      generation_mode: typeof data?.generationMode === "string" ? data.generationMode : null,
      capability_id: typeof data?.capabilityId === "string" ? data.capabilityId : null,
      style_template_id: typeof data?.styleTemplateId === "string" ? data.styleTemplateId : null,
    } : null,
    placement_contract: {
      preferred: { anchor: "viewport_center", layout: "grid" },
      supported_anchors: ["viewport_center", "selected_node", "absolute"],
      supported_layouts: ["stack", "grid", "row", "column"],
      explicit_xy_has_priority: true,
      omit_xy_uses_current_viewport_center: true,
      snapshot_not_required_for_viewport_placement: true,
    },
    viewport_context: {
      source: "browser_xyflow_live",
      available: hasViewport,
      x: compactNumber(viewport.x),
      y: compactNumber(viewport.y),
      zoom: compactNumber(viewportZoom),
      width: viewportWidth,
      height: viewportHeight,
      world_center: hasViewport ? {
        x: compactNumber((viewportMinX + viewportMaxX) / 2),
        y: compactNumber((viewportMinY + viewportMaxY) / 2),
      } : null,
      world_bounds: hasViewport ? {
        min_x: compactNumber(viewportMinX),
        min_y: compactNumber(viewportMinY),
        max_x: compactNumber(viewportMaxX),
        max_y: compactNumber(viewportMaxY),
      } : null,
    },
    tool_contract: {
      structure_only: ["freezone_get_canvas_viewport", "freezone_get_canvas_snapshot", "freezone_emit_canvas_command"],
      generation_proposal_only: ["freezone_propose_generation"],
      node_execution: ["freezone_run_node", "freezone_retry_node", "freezone_stop_task"],
      forbidden: ["python", "terminal", "shell", "search", "web_search", "navigate", "browser"],
    },
  });
}
