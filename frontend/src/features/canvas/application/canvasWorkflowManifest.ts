// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { CANVAS_NODE_TYPES, type CanvasNodeType } from '@/features/canvas/domain/canvasNodes';
import { DEFAULT_NODE_DISPLAY_NAME } from '@/features/canvas/domain/nodeDisplay';
import {
  describeNodeCapability,
  getNodeTypeCapabilities,
  type NodeCapabilityContract,
} from '@/features/canvas/domain/nodeCapabilityCatalog';
import {
  CANVAS_STARTER_WORKFLOWS,
  type CanvasStarterWorkflowDefinition,
} from './starterWorkflows';
import { starterWorkflowPresentation } from './starterWorkflowCatalog';

export interface CanvasWorkflowNodeManifestItem {
  node_type: CanvasNodeType;
  label: string;
  manual_menu: boolean;
  capabilities: string[];
  /** Full node-to-executor mapping; `capabilities` remains for older Agent clients. */
  capability_contracts: NodeCapabilityContract[];
}

export interface CanvasWorkflowTemplateManifestItem {
  id: CanvasStarterWorkflowDefinition['id'];
  title: string;
  category: string;
  required_inputs: string;
  model_hint: string;
  template_kind: string;
  delivery_level: string;
  required_roles: string[];
  outputs: string[];
  does_not_produce: string[];
  quality_gates: string[];
}

const MANUAL_MENU_NODE_TYPES = new Set<CanvasNodeType>([
  CANVAS_NODE_TYPES.textAnnotation,
  CANVAS_NODE_TYPES.beatContext,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.video,
  CANVAS_NODE_TYPES.videoCompose,
  CANVAS_NODE_TYPES.audio,
  CANVAS_NODE_TYPES.script,
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.pano360Viewer,
  CANVAS_NODE_TYPES.threeDWorld,
]);

/** Compact contract sent to the canvas Agent so workflow planning sees the same powers as the UI. */
export function buildCanvasWorkflowManifest(): {
  commands: string[];
  node_types: CanvasWorkflowNodeManifestItem[];
  starter_workflows: CanvasWorkflowTemplateManifestItem[];
} {
  return {
    commands: [
      'create_canvas_node(node_type, display_name?, text?/prompt?, model?, x?, y?, created_node_id?)',
      'create_image_prompt_node(prompt, display_name?, model?, x?, y?, created_node_id?)',
      'create_video_prompt_node(prompt, display_name?, model?, generation_mode?, x?, y?, created_node_id?)',
      'create_shot_sequence(prompts, display_name?, model?, x?, y?, created_node_ids?)',
      'insert_starter_workflow(workflow_id, x?, y?)',
      'connect_nodes(source, target)',
      'remove_edge(source, target)',
      'duplicate_node(node_id, created_node_id?)',
      'update_node_prompt(node_id, prompt)',
      'update_node_label(node_id, display_name)',
      'move_node(node_id, x?, y?)',
      'delete_node(node_id)',
      'focus_node(node_id)',
      'select_node(node_id)',
    ],
    node_types: Object.values(CANVAS_NODE_TYPES).map((nodeType) => ({
      node_type: nodeType,
      label: DEFAULT_NODE_DISPLAY_NAME[nodeType],
      manual_menu: MANUAL_MENU_NODE_TYPES.has(nodeType),
      capabilities: getNodeTypeCapabilities(nodeType).map((capability) => capability.id),
      capability_contracts: getNodeTypeCapabilities(nodeType).map(describeNodeCapability),
    })),
    starter_workflows: CANVAS_STARTER_WORKFLOWS.map((workflow) => {
      const presentation = starterWorkflowPresentation(workflow.id);
      return {
        id: workflow.id,
        title: workflow.title,
        category: presentation.category,
        required_inputs: presentation.inputSummary,
        model_hint: presentation.modelHint,
        template_kind: workflow.template_kind ?? 'atomic_capability',
        delivery_level: workflow.delivery_level ?? 'shot_draft',
        required_roles: [...(workflow.required_roles ?? [])],
        outputs: [...(workflow.outputs ?? [])],
        does_not_produce: [...(workflow.does_not_produce ?? [])],
        quality_gates: [...(workflow.quality_gates ?? [])],
      };
    }),
  };
}
