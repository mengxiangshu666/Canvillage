// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

const GENERIC_IMAGE_TITLES = new Set(['上传图片', '上传资源']);
const OPAQUE_MEDIA_STEMS = [
  /^[a-f\d]{24,}$/i,
  /^img_v\d+_\d+_[a-f\d-]{24,}[a-z]?$/i,
  /^(?:image|img|photo|pic|screenshot)(?:[_ -]?\d+)?$/i,
];

/**
 * 服务端存储的媒体常以 hash 命名。它对追溯有用，对创作画布没有信息价值，
 * 因此不应被当作图片节点的可见标题。
 */
export function isOpaqueMediaFilename(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  const fileName = value.trim().split(/[\\/]/).pop() ?? '';
  const extensionStart = fileName.lastIndexOf('.');
  const stem = extensionStart > 0 ? fileName.slice(0, extensionStart) : fileName;
  return OPAQUE_MEDIA_STEMS.some((pattern) => pattern.test(stem));
}

export function shouldShowUploadNodeHeader({
  hasMediaContent,
  imageOnly,
  title,
}: {
  hasMediaContent: boolean;
  imageOnly: boolean;
  title: string;
}): boolean {
  if (!hasMediaContent) return true;

  const normalizedTitle = title.trim();
  if (isOpaqueMediaFilename(normalizedTitle)) return false;

  // 图片节点在没有人工命名时以缩略图本身作为唯一标签，避免重复的“上传图片”。
  return !(imageOnly && GENERIC_IMAGE_TITLES.has(normalizedTitle));
}
