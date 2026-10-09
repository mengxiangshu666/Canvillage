import { describe, expect, it } from "vitest";
import {
  applyAudioCompletionContract,
  computeCounts,
} from "@/hooks/use-beat-states";
import type { BeatStates } from "@/types/beat-state";
import type { Beat } from "@/types/episode";

function beat(beatNumber: number, audioType?: string): Beat {
  return {
    beat_number: beatNumber,
    narration_segment: audioType === "narration" ? "旁白" : "",
    visual_description: `镜头 ${beatNumber}`,
    audio_type: audioType,
  };
}

function states(...beatNumbers: number[]): BeatStates {
  return Object.fromEntries(
    beatNumbers.map((beatNumber) => [
      beatNumber,
      { script: "ready", sketch: "ready", audio: "missing", video: "ready" },
    ]),
  );
}

describe("beat audio completion contract", () => {
  it("marks silence ready without an audio URL inside a mixed episode", () => {
    const beats = [beat(1, "silence"), beat(2, "narration")];
    const result = applyAudioCompletionContract(states(1, 2), beats);

    expect(result[1].audio).toBe("ready");
    expect(result[2].audio).toBe("missing");
  });

  it("keeps the legacy action type equivalent to silence", () => {
    const result = applyAudioCompletionContract(states(1), [beat(1, "action")]);

    expect(result[1].audio).toBe("ready");
  });

  it("marks an empty narration beat ready because the audio worker skips it", () => {
    const emptyNarration = beat(1, "narration");
    emptyNarration.narration_segment = "";

    const result = applyAudioCompletionContract(states(1), [emptyNarration]);

    expect(result[1].audio).toBe("ready");
  });

  it("lets an all-silent episode pass the audio and compose gates", () => {
    const beats = [beat(1, "silence"), beat(2, "action")];
    const normalized = applyAudioCompletionContract(states(1, 2), beats);
    const counts = computeCounts(normalized, beats, true);

    expect(counts.audio).toMatchObject({ ready: 2, total: 2 });
    expect(counts.compose).toEqual({ ready: true, missing: [] });
  });
});
