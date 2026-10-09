import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { claimHomeAutoSend, hasUnresolvedStoreSkills, useRetainedComposer, type ComposerHandoffSnapshot } from "./use-retained-composer";

describe("responsive composer handoff", () => {
  it("retains edits and only claims automatic submission once across remounts", () => {
    const shared = { current: { draft: "idea", attachments: [], autoSendClaimed: false } as ComposerHandoffSnapshot };
    const first = renderHook(() => useRetainedComposer(shared.current, shared));
    expect(claimHomeAutoSend(shared.current, false)).toBe(false);
    act(() => first.result.current.setDraft("edited idea"));
    expect(claimHomeAutoSend(shared.current, true)).toBe(true);
    first.unmount();
    const second = renderHook(() => useRetainedComposer(shared.current, shared));
    expect(second.result.current.draft).toBe("edited idea");
    expect(claimHomeAutoSend(shared.current, true)).toBe(false);
    second.unmount();
  });

  it("a pending successful send clears the newly mounted composer", () => {
    const shared = { current: { draft: "idea", attachments: [{ name: "reference" }], autoSendClaimed: false } as unknown as ComposerHandoffSnapshot };
    const first = renderHook(() => useRetainedComposer(shared.current, shared));
    const pendingSend = first.result.current;
    first.unmount();
    const second = renderHook(() => useRetainedComposer(shared.current, shared));
    act(() => { pendingSend.setDraft(""); pendingSend.setAttachments([]); });
    expect(second.result.current.draft).toBe("");
    expect(second.result.current.attachments).toEqual([]);
    second.unmount();
  });

  it("explicit store skills wait for installed catalog entries; builtins do not", () => {
    expect(hasUnresolvedStoreSkills(["builtin"], [])).toBe(false);
    expect(hasUnresolvedStoreSkills(["store:director"], [])).toBe(true);
    expect(hasUnresolvedStoreSkills(["store:director"], [{ id: "store:director" }])).toBe(false);
    expect(hasUnresolvedStoreSkills(["store:removed"], [{ id: "store:director" }])).toBe(true);
  });
});
