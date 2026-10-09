// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export interface CanvasCommandRevisionEnvelope {
  revision?: number;
  server_applied?: boolean;
  /** Internal proposals are explicitly marked so they can still apply locally. */
  canvas_command_emitted?: boolean;
  turn_id?: string;
  run_id?: string;
  snapshot_required?: boolean;
  ui_reconcile_required?: boolean;
  commands?: Array<{ type?: string | null }>;
}

export type CanvasCommandArbitration =
  | { decision: "apply"; reason: "client_only_command" }
  | { decision: "skip"; reason: "authoritative_server_commit" }
  | { decision: "skip"; reason: "stale_revision" };

/**
 * Decide whether a structural command may mutate the optimistic canvas.
 *
 * A server-applied command, server revision, or Agent run identity marks an
 * invalidation notice, not a second mutation. Compatibility frames can omit
 * `server_applied` and `revision`, but they retain `turn_id` or `run_id`.
 * Browser-only proposals carry none of those authoritative signals and remain
 * eligible for optimistic execution.
 *
 * `currentRevision` is the browser's live canvas revision. An authoritative
 * envelope whose revision is already below it describes a canvas state the
 * client has superseded, so applying it would resurrect an older snapshot.
 * Only the stale revision is rejected here; the longer-standing
 * `authoritative_server_commit` branch keeps its exact semantics and ordering.
 */
export function arbitrateCanvasCommand(
  envelope: CanvasCommandRevisionEnvelope,
  currentRevision: number | null | undefined,
): CanvasCommandArbitration {
  const commandRevision = envelope.revision;
  const fromAgentRun = (
    (typeof envelope.turn_id === "string" && envelope.turn_id.trim().length > 0)
    || (typeof envelope.run_id === "string" && envelope.run_id.trim().length > 0)
  );
  const isClientProposal = envelope.canvas_command_emitted === true;
  if (
    !isClientProposal
    && (
      envelope.server_applied === true
      || (Number.isSafeInteger(commandRevision) && (commandRevision as number) > 0)
      || fromAgentRun
    )
  ) {
    // A frame carrying an explicit revision that is older than the current
    // canvas is a late echo from a superseded state. Report it as stale so a
    // caller can tell "this is obsolete" apart from "the server already wrote
    // this"; both are skipped, but only the stale echo is a replayed conflict.
    if (
      envelope.server_applied !== true
      && Number.isSafeInteger(commandRevision)
      && (commandRevision as number) > 0
      && Number.isSafeInteger(currentRevision)
      && (commandRevision as number) < (currentRevision as number)
    ) {
      return { decision: "skip", reason: "stale_revision" };
    }
    return {
      decision: "skip",
      reason: "authoritative_server_commit",
    };
  }
  return { decision: "apply", reason: "client_only_command" };
}
