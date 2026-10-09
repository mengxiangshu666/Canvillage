import { useCallback, useRef, useSyncExternalStore, type MutableRefObject, type SetStateAction } from "react";
import type { ChatAttachment } from "./types";

export interface ComposerHandoffSnapshot {
  draft: string;
  attachments: ChatAttachment[];
  autoSendClaimed: boolean;
  skillIds?: string[];
  listeners?: Set<() => void>;
}

// The dock owns this snapshot so pending sends can update a remounted composer.
export function useRetainedComposer(initial: ComposerHandoffSnapshot, shared?: MutableRefObject<ComposerHandoffSnapshot>) {
  const local = useRef(initial);
  const store = shared ?? local;
  const subscribe = useCallback((listener: () => void) => {
    const listeners = store.current.listeners ??= new Set();
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, [store]);
  const read = useCallback(() => store.current, [store]);
  const snapshot = useSyncExternalStore(subscribe, read, read);
  const setDraft = useCallback((value: SetStateAction<string>) => {
    store.current = { ...store.current, draft: typeof value === "function" ? value(store.current.draft) : value };
    store.current.listeners?.forEach((notify) => notify());
  }, [store]);
  const setAttachments = useCallback((value: SetStateAction<ChatAttachment[]>) => {
    store.current = { ...store.current, attachments: typeof value === "function" ? value(store.current.attachments) : value };
    store.current.listeners?.forEach((notify) => notify());
  }, [store]);
  return { draft: snapshot.draft, attachments: snapshot.attachments, setDraft, setAttachments };
}

export function claimHomeAutoSend(snapshot: ComposerHandoffSnapshot, ready: boolean): boolean {
  if (!ready || snapshot.autoSendClaimed) return false;
  snapshot.autoSendClaimed = true;
  return true;
}

export function hasUnresolvedStoreSkills(selected: readonly string[], available: readonly { id: string }[]): boolean {
  return selected.some((id) => id.startsWith("store:") && !available.some((skill) => skill.id === id));
}
