// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { NodeTypes } from '@xyflow/react';

import { withLodShell } from './LodShellNode';
import { AudioNode } from './AudioNode';
import { BeatContextNode } from './BeatContextNode';
import { GroupNode } from './GroupNode';
import { ImageEditNode } from './ImageEditNode';
import { ImageGenNode } from './ImageGenNode';
import { ImageNode } from './ImageNode';
import { Pano360ViewerNode } from './Pano360ViewerNode';
import { ScriptNode } from './ScriptNode';
import { SkillNode } from './SkillNode';
import { StoryboardGenNode } from './StoryboardGenNode';
import { StoryboardNode } from './StoryboardNode';
import { TextAnnotationNode } from './TextAnnotationNode';
import { ThreeDWorldNode } from './ThreeDWorldNode';
import { UploadNode } from './UploadNode';
import { VideoComposeNode } from './VideoComposeNode';
import { VideoNode } from './VideoNode';
import { VideoStoryNode } from './VideoStoryNode';

export const nodeTypes: NodeTypes = {
  audioNode: withLodShell('audioNode', AudioNode),
  beatContextNode: BeatContextNode,
  exportImageNode: withLodShell('exportImageNode', ImageNode),
  groupNode: GroupNode,
  imageGenNode: withLodShell('imageGenNode', ImageGenNode),
  imageNode: withLodShell('imageNode', ImageEditNode),
  pano360ViewerNode: Pano360ViewerNode,
  scriptNode: withLodShell('scriptNode', ScriptNode),
  skillNode: SkillNode,
  storyboardGenNode: withLodShell('storyboardGenNode', StoryboardGenNode),
  storyboardNode: withLodShell('storyboardNode', StoryboardNode),
  textAnnotationNode: withLodShell('textAnnotationNode', TextAnnotationNode),
  threeDWorldNode: withLodShell('threeDWorldNode', ThreeDWorldNode),
  uploadNode: withLodShell('uploadNode', UploadNode),
  videoComposeNode: withLodShell('videoComposeNode', VideoComposeNode),
  videoNode: withLodShell('videoNode', VideoNode),
  videoStoryNode: withLodShell('videoStoryNode', VideoStoryNode),
};

export { AudioNode, BeatContextNode, GroupNode, ImageEditNode, ImageGenNode, ImageNode, Pano360ViewerNode, ScriptNode, SkillNode, StoryboardGenNode, StoryboardNode, TextAnnotationNode, ThreeDWorldNode, UploadNode, VideoComposeNode, VideoNode, VideoStoryNode };
