"""Turn-scoped canvas command identity.

One turn must replay a canvas command under the same id, and two different
turns must never share an id even when they reuse the same business name.  The
original scope was the first 16 characters of the turn id, so turns such as
``eval-exact_reuse-1-...`` and ``eval-exact_reuse-2-...`` collapsed into one
namespace; the gateway then rejected the second turn's write as a
``execution_context`` conflict even though nothing was stale.  The scope is now
a digest of the whole turn id, and only a value carrying exactly that scope is
treated as pre-scoped.
"""

from __future__ import annotations

import hashlib

MAX_COMMAND_ID_CHARS = 512
TURN_SCOPE_DIGEST_CHARS = 10


def turn_command_scope(source_turn_id: object) -> str:
    """Return the shared, bounded scope token for one Agent turn."""

    turn = str(source_turn_id or "").strip()
    if not turn:
        return ""
    digest = hashlib.sha256(turn.encode("utf-8")).hexdigest()[:TURN_SCOPE_DIGEST_CHARS]
    return f"turn-{digest}"


def turn_scoped_command_id(source_turn_id: object, command_id: object) -> str:
    """Scope one command id to its turn without re-scoping a scoped id."""

    raw = str(command_id or "").strip()
    scope = turn_command_scope(source_turn_id)
    if not scope:
        return raw[:MAX_COMMAND_ID_CHARS]
    prefix = f"{scope}:"
    if raw.startswith(prefix):
        return raw[:MAX_COMMAND_ID_CHARS]
    return f"{prefix}{raw}"[:MAX_COMMAND_ID_CHARS]


__all__ = [
    "MAX_COMMAND_ID_CHARS",
    "TURN_SCOPE_DIGEST_CHARS",
    "turn_command_scope",
    "turn_scoped_command_id",
]
