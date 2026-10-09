import type { ChatAttachment } from "@/features/superchat/types";

export type AttachmentBlob = {
  blob: Blob;
  filename: string;
};

export function dataUrlToAttachmentBlob(
  attachment: ChatAttachment,
): AttachmentBlob | null {
  const content = attachment.content;
  if (!content?.startsWith("data:")) return null;
  const comma = content.indexOf(",");
  if (comma < 0) return null;
  const meta = content.slice(0, comma);
  const base64 = content.slice(comma + 1);
  const mime =
    attachment.mimeType ||
    /data:([^;]+)/.exec(meta)?.[1] ||
    "application/octet-stream";
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return {
    blob: new Blob([bytes], { type: mime }),
    filename: attachment.fileName || "novel.txt",
  };
}

export function dataUrlToText(attachment: ChatAttachment): string | null {
  const content = attachment.content;
  if (!content?.startsWith("data:")) return null;
  const comma = content.indexOf(",");
  if (comma < 0) return null;
  const meta = content.slice(0, comma);
  const payload = content.slice(comma + 1);
  try {
    if (!/;base64/i.test(meta)) {
      return decodeURIComponent(payload);
    }
    const binary = atob(payload);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  } catch {
    return null;
  }
}
