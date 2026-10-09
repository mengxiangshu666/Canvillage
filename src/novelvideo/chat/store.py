"""Scoped chat persistence shared by NiceGUI and the React WebSocket API.

Lovart-style split:
    * home scope: user-level conversation before a project exists.
    * project scope: project/canvas conversation and iteration history.

The project chat DB path intentionally matches ``chat_service.py`` so existing
NiceGUI history remains readable by the future React UI.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from novelvideo.sqlite_pragmas import configure_sqlite_connection


DEFAULT_CHAT_CONVERSATION_ID = "main"
_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_LEGACY_DIRECTOR_QUESTION_PREFIXES = (
    "这支片主要给谁看、发布在哪里，还是只做内部样片？",
    "你要什么视觉风格和情绪基调？",
    "成片准备使用什么画幅和平台规格？",
    "声音怎么处理：对白、旁白、音乐和字幕分别要不要？",
)


def sanitize_chat_metadata(value: object) -> dict[str, Any]:
    """Keep chat metadata to one current clarification question.

    Older director-preflight turns persisted a question queue and a suggested
    answer beside the current question.  Strip those presentation-only fields
    at the persistence boundary and again on replay so legacy clients cannot
    resurrect the queue.
    """

    metadata = dict(value) if isinstance(value, dict) else {}
    metadata.pop("next_questions", None)
    metadata.pop("suggested_answer", None)
    for key in ("clarification", "director_clarification"):
        nested = metadata.get(key)
        if not isinstance(nested, dict):
            continue
        sanitized = dict(nested)
        sanitized.pop("next_questions", None)
        sanitized.pop("suggested_answer", None)
        metadata[key] = sanitized
    return metadata


def is_director_clarification_metadata(value: object) -> bool:
    """Identify an assistant row produced by the director preflight gate."""

    if not isinstance(value, dict):
        return False
    return str(value.get("backend") or "").strip().casefold() == "director-preflight"


def is_director_clarification_message(message: object) -> bool:
    """Identify old clarification rows even when a legacy cache lost metadata."""

    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    if is_director_clarification_metadata(message.get("metadata")):
        return True
    content = str(message.get("content") or "").lstrip()
    return any(content.startswith(prefix) for prefix in _LEGACY_DIRECTOR_QUESTION_PREFIXES)


def filter_stale_director_clarifications(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only the current blocking question in a replayed chat timeline.

    Older builds persisted every step of the fixed interview.  Those rows are
    historical implementation details, not conversation content.  A later
    non-preflight assistant reply proves that the interview was superseded;
    otherwise only the newest preflight row is still actionable.
    """

    clarification_indices = [
        index
        for index, message in enumerate(messages)
        if is_director_clarification_message(message)
    ]
    if not clarification_indices:
        return messages
    latest_non_clarification = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "assistant"
            and not is_director_clarification_message(message)
        ),
        default=-1,
    )
    keep_index = clarification_indices[-1]
    if latest_non_clarification > keep_index:
        keep_index = -1
    return [
        message
        for index, message in enumerate(messages)
        if index not in clarification_indices or index == keep_index
    ]


def normalize_conversation_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return DEFAULT_CHAT_CONVERSATION_ID
    if not _CONVERSATION_ID_RE.fullmatch(normalized):
        raise ValueError("invalid chat conversation id")
    return normalized


def normalize_canvas_id(value: object) -> str:
    """Return the stable canvas identity used by session routing (kept for store)."""
    normalized = str(value or "").strip()
    return normalized or "default"


_CONTEXT_COMPRESSION_FAILURE_MARKERS = (
    "compression_exhausted",
    "separator is found",
    "chunk is longer than limit",
    "长对话上下文压缩失败",
)


def _is_context_compression_failure(content: object, metadata: object) -> bool:
    values = [str(content or "").casefold()]
    if isinstance(metadata, dict):
        values.append(str(metadata.get("retry_reason") or "").casefold())
    return any(marker in value for value in values for marker in _CONTEXT_COMPRESSION_FAILURE_MARKERS)


def _assistant_prefix_candidates(previous_assistant: object) -> list[str]:
    if isinstance(previous_assistant, (list, tuple)):
        items = [
            str(item or "").strip()
            for item in previous_assistant
            if str(item or "").strip()
        ]
        return sorted(set(items), key=len, reverse=True)
    prefix = str(previous_assistant or "").strip()
    return [prefix] if prefix else []


def _semantic_operator(text: str, index: int, char: str) -> bool:
    if char in "<>!=+*/%&^~":
        return True
    previous = index - 1
    while previous >= 0 and text[previous].isspace():
        previous -= 1
    following = index + 1
    while following < len(text) and text[following].isspace():
        following += 1
    left = text[previous] if previous >= 0 else ""
    right = text[following] if following < len(text) else ""
    if char in "-|?:":
        return left.isalnum() and right.isalnum()
    if char == ".":
        return left.isdigit() and right.isdigit()
    return False


def _semantic_chars_with_end_positions(value: object) -> tuple[str, list[int]]:
    """Normalize replay text while retaining offsets into the original string.

    ACP history replay is not byte stable: markdown renderers may change table
    separators, whitespace and punctuation.  Comparing letters/numbers only is
    strict enough to avoid fuzzy substring guesses while tolerating those
    presentation-only mutations.
    """

    text = str(value or "")
    normalized: list[str] = []
    end_positions: list[int] = []
    for index, char in enumerate(text):
        for normalized_char in unicodedata.normalize("NFKC", char).casefold():
            if normalized_char.isalnum() or _semantic_operator(
                text, index, normalized_char
            ):
                normalized.append(normalized_char)
                end_positions.append(index + 1)
    return "".join(normalized), end_positions


def _trim_replay_boundary(text: str) -> str:
    index = 0
    while index < len(text) and not text[index].isalnum():
        index += 1
    return text[index:]


_INFRASTRUCTURE_ERROR_FRAME_PATTERNS = (
    re.compile(r"context length exceeded\b.*cannot compress further\.?", re.I | re.S),
    re.compile(r"api call failed after\s+\d+\s+retr(?:y|ies)\b.*", re.I | re.S),
    re.compile(r"hermes acp session compression exhausted\s*[.!。]?", re.I | re.S),
    re.compile(
        r"(?:http\s+5\d\d\s*[:：-]?\s*)?service temporarily unavailable\s*[.!。]?",
        re.I | re.S,
    ),
)


def _is_infrastructure_error_message(content: object) -> bool:
    text = str(content or "").strip()
    return bool(text) and any(
        pattern.fullmatch(text) for pattern in _INFRASTRUCTURE_ERROR_FRAME_PATTERNS
    )


def _strip_infrastructure_error_tail(content: object) -> str:
    text = str(content or "")
    if _is_infrastructure_error_message(text):
        return ""
    lines = text.splitlines(keepends=True)
    while lines:
        if not lines[-1].strip():
            lines.pop()
            continue
        if not _is_infrastructure_error_message(lines[-1]):
            break
        lines.pop()
    return "".join(lines).rstrip()


def _strip_replayed_assistant_prefix(content: str, previous_assistant: object) -> str:
    text = str(content or "")
    normalized_text: str | None = None
    end_positions: list[int] = []
    for prefix in _assistant_prefix_candidates(previous_assistant):
        if text.startswith(prefix):
            return text[len(prefix) :].lstrip()
        compact_prefix = "".join(prefix.split())
        if not compact_prefix:
            continue
        matched = 0
        end_index = 0
        for index, char in enumerate(text):
            if char.isspace():
                continue
            if matched >= len(compact_prefix) or char != compact_prefix[matched]:
                break
            matched += 1
            end_index = index + 1
            if matched == len(compact_prefix):
                return text[end_index:].lstrip()
        if normalized_text is None:
            normalized_text, end_positions = _semantic_chars_with_end_positions(text)
        normalized_prefix, _ = _semantic_chars_with_end_positions(prefix)
        if normalized_prefix and normalized_text.startswith(normalized_prefix):
            end_index = end_positions[len(normalized_prefix) - 1]
            return _trim_replay_boundary(text[end_index:])
    return text


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    columns: set[str],
    column: str,
    sql: str,
) -> None:
    if column in columns:
        return
    try:
        conn.execute(sql)
    except sqlite3.OperationalError as exc:
        # Two API/WebSocket workers may discover the same legacy schema at the
        # same time.  The loser of the ALTER race is already migrated.
        if "duplicate column name" not in str(exc).casefold():
            raise
        refreshed = {
            str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in refreshed:
            raise


def _drop_index_when_columns_changed(
    conn: sqlite3.Connection,
    index_name: str,
    expected_columns: tuple[str, ...],
) -> None:
    current_columns = tuple(
        str(row["name"] if isinstance(row, sqlite3.Row) else row[2])
        for row in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    )
    if current_columns and current_columns != expected_columns:
        conn.execute(f"DROP INDEX IF EXISTS {index_name}")


def ensure_chat_schema(conn: sqlite3.Connection) -> None:
    """Create/upgrade the single chat schema used by every chat entry point."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          role TEXT NOT NULL,
          content TEXT NOT NULL,
          media_json TEXT NOT NULL DEFAULT '[]',
          conversation_id TEXT NOT NULL DEFAULT 'main',
          turn_id TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        )
        """
    )
    columns = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()
    }
    _add_column_if_missing(
        conn,
        "chat_messages",
        columns,
        "conversation_id",
        "ALTER TABLE chat_messages ADD COLUMN conversation_id TEXT NOT NULL DEFAULT 'main'",
    )
    _add_column_if_missing(
        conn,
        "chat_messages",
        columns,
        "turn_id",
        "ALTER TABLE chat_messages ADD COLUMN turn_id TEXT",
    )
    _add_column_if_missing(
        conn,
        "chat_messages",
        columns,
        "metadata_json",
        "ALTER TABLE chat_messages ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_messages_turn_id
          ON chat_messages(turn_id, id)
        """
    )
    duplicate_assistant_turns = conn.execute(
        """
        SELECT conversation_id, turn_id, MAX(id) AS keep_id
          FROM chat_messages
         WHERE role='assistant' AND turn_id IS NOT NULL AND turn_id <> ''
         GROUP BY conversation_id, turn_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in duplicate_assistant_turns:
        conn.execute(
            "DELETE FROM chat_messages WHERE role='assistant' AND conversation_id=? AND turn_id=? AND id<>?",
            (
                str(row["conversation_id"]),
                str(row["turn_id"]),
                int(row["keep_id"]),
            ),
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
          ON chat_messages(conversation_id, id)
        """
    )
    _drop_index_when_columns_changed(
        conn,
        "uq_chat_messages_assistant_turn",
        ("conversation_id", "turn_id"),
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_messages_assistant_turn
          ON chat_messages(conversation_id, turn_id)
         WHERE role='assistant' AND turn_id IS NOT NULL AND turn_id <> ''
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_ui_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          turn_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          conversation_id TEXT NOT NULL DEFAULT 'main',
          receipt_id TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    ui_event_columns = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute("PRAGMA table_info(chat_ui_events)").fetchall()
    }
    receipt_column_missing = "receipt_id" not in ui_event_columns
    _add_column_if_missing(
        conn,
        "chat_ui_events",
        ui_event_columns,
        "conversation_id",
        "ALTER TABLE chat_ui_events ADD COLUMN conversation_id TEXT NOT NULL DEFAULT 'main'",
    )
    _add_column_if_missing(
        conn,
        "chat_ui_events",
        ui_event_columns,
        "receipt_id",
        "ALTER TABLE chat_ui_events ADD COLUMN receipt_id TEXT NOT NULL DEFAULT ''",
    )
    if receipt_column_missing:
        seen_receipts: set[tuple[str, str, str, str]] = set()
        for row in conn.execute(
            "SELECT id, turn_id, event_type, conversation_id, payload_json "
            "FROM chat_ui_events ORDER BY id ASC"
        ).fetchall():
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            receipt_id = str(
                payload.get("receiptId") or payload.get("event_id") or ""
            ).strip()
            if not receipt_id:
                continue
            key = (
                str(row["conversation_id"]),
                str(row["turn_id"]),
                str(row["event_type"]),
                receipt_id,
            )
            if key in seen_receipts:
                conn.execute("DELETE FROM chat_ui_events WHERE id=?", (int(row["id"]),))
                continue
            seen_receipts.add(key)
            conn.execute(
                "UPDATE chat_ui_events SET receipt_id=? WHERE id=?",
                (receipt_id, int(row["id"])),
            )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_ui_events_conversation
          ON chat_ui_events(conversation_id, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_ui_events_turn_id
          ON chat_ui_events(turn_id, id)
        """
    )
    _drop_index_when_columns_changed(
        conn,
        "uq_chat_ui_events_receipt",
        ("conversation_id", "turn_id", "event_type", "receipt_id"),
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_ui_events_receipt
          ON chat_ui_events(conversation_id, turn_id, event_type, receipt_id)
         WHERE receipt_id <> ''
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_conversations (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO chat_conversations(id, title, created_at, updated_at)
        VALUES (?, '默认对话', ?, ?)
        """,
        (DEFAULT_CHAT_CONVERSATION_ID, now, now),
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _state_root() -> Path:
    configured = os.environ.get("NOVELVIDEO_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return _repo_root() / "state"


@dataclass(frozen=True)
class ChatScope:
    kind: Literal["home", "project"]
    id: str | None = None
    canvas_id: str | None = None
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID

    def __post_init__(self) -> None:
        if self.kind not in {"home", "project"}:
            raise ValueError(f"unsupported chat scope: {self.kind}")
        if self.kind == "project":
            object.__setattr__(self, "canvas_id", normalize_canvas_id(self.canvas_id))
        else:
            object.__setattr__(self, "canvas_id", None)
        object.__setattr__(
            self,
            "conversation_id",
            normalize_conversation_id(self.conversation_id),
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ChatScope":
        payload = payload or {"kind": "home"}
        kind = str(payload.get("kind") or "home")
        if kind not in {"home", "project"}:
            raise ValueError(f"unsupported chat scope: {kind}")
        raw_id = payload.get("id")
        scope_id = str(raw_id).strip() if raw_id is not None else None
        if kind == "home":
            scope_id = None
        if kind != "home" and not scope_id:
            raise ValueError(f"scope id is required for {kind}")
        raw_canvas_id = payload.get("canvas_id", payload.get("canvasId"))
        canvas_id = normalize_canvas_id(raw_canvas_id) if kind == "project" else None
        raw_conversation_id = payload.get(
            "conversation_id", payload.get("conversationId")
        )
        return cls(
            kind=kind,
            id=scope_id,
            canvas_id=canvas_id,
            conversation_id=normalize_conversation_id(raw_conversation_id),
        )

    def to_dict(self) -> dict[str, str | None]:
        payload = {"kind": self.kind, "id": self.id}
        if self.kind == "project":
            payload["canvas_id"] = normalize_canvas_id(self.canvas_id)
        conversation_id = normalize_conversation_id(self.conversation_id)
        if conversation_id != DEFAULT_CHAT_CONVERSATION_ID:
            payload["conversation_id"] = conversation_id
        return payload


def project_canvas_chat_state_dir(base_dir: Path, canvas_id: object) -> Path:
    """Keep the legacy project chat path for the default canvas.

    Non-default canvases get an isolated directory without exposing arbitrary
    canvas identifiers as filesystem paths.
    """

    normalized = normalize_canvas_id(canvas_id)
    if normalized == "default":
        return base_dir
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return base_dir / "canvas-chats" / digest


class ChatStore:
    @staticmethod
    def _ensure_conversation(
        conn: sqlite3.Connection,
        conversation_id: str,
        *,
        title: str = "新对话",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO chat_conversations(id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, title, now, now),
        )

    @staticmethod
    def _touch_conversation(
        conn: sqlite3.Connection,
        conversation_id: str,
        *,
        first_user_text: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        row = conn.execute(
            "SELECT title FROM chat_conversations WHERE id=?",
            (conversation_id,),
        ).fetchone()
        title = str(row["title"] if row else "新对话")
        normalized_text = " ".join(str(first_user_text or "").split()).strip()
        if normalized_text and title == "新对话":
            title = normalized_text[:36]
        conn.execute(
            "UPDATE chat_conversations SET title=?, updated_at=? WHERE id=?",
            (title, now, conversation_id),
        )

    def create_conversation(
        self,
        username: str,
        scope: ChatScope,
        *,
        title: str = "新对话",
    ) -> dict[str, Any]:
        conversation_id = uuid.uuid4().hex
        normalized_title = " ".join(str(title or "").split()).strip()[:60] or "新对话"
        conn = self.connect(username, scope)
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO chat_conversations(id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, normalized_title, now, now),
            )
            conn.commit()
            return {
                "id": conversation_id,
                "title": normalized_title,
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
                "preview": "",
            }
        finally:
            conn.close()

    def list_conversations(
        self,
        username: str,
        scope: ChatScope,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conn = self.connect(username, scope)
        try:
            rows = conn.execute(
                """
                SELECT c.id,
                       c.title,
                       c.created_at,
                       CASE
                         WHEN MAX(m.created_at) IS NULL THEN c.updated_at
                         WHEN MAX(m.created_at) > c.updated_at THEN MAX(m.created_at)
                         ELSE c.updated_at
                       END AS updated_at,
                       COUNT(m.id) AS message_count,
                       COALESCE((
                         SELECT mm.content
                           FROM chat_messages mm
                          WHERE mm.conversation_id = c.id
                            AND mm.role IN ('user', 'assistant')
                          ORDER BY mm.id DESC
                          LIMIT 1
                       ), '') AS preview
                  FROM chat_conversations c
             LEFT JOIN chat_messages m ON m.conversation_id = c.id
              GROUP BY c.id, c.title, c.created_at, c.updated_at
              ORDER BY updated_at DESC, c.created_at DESC
                 LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            return [
                {
                    "id": str(row["id"]),
                    "title": str(row["title"]),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "message_count": int(row["message_count"] or 0),
                    "preview": " ".join(str(row["preview"] or "").split())[:120],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def delete_conversation(
        self,
        username: str,
        scope: ChatScope,
    ) -> dict[str, int | str | bool]:
        """Delete one conversation and its messages/UI receipts atomically."""

        conversation_id = normalize_conversation_id(scope.conversation_id)
        conn = self.connect(username, scope)
        try:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT 1 FROM chat_conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            message_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()[0]
            )
            ui_event_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM chat_ui_events WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()[0]
            )
            conn.execute(
                "DELETE FROM chat_ui_events WHERE conversation_id=?",
                (conversation_id,),
            )
            conn.execute(
                "DELETE FROM chat_messages WHERE conversation_id=?",
                (conversation_id,),
            )
            conn.execute(
                "DELETE FROM chat_conversations WHERE id=?",
                (conversation_id,),
            )
            conn.commit()
            return {
                "id": conversation_id,
                "deleted": exists is not None,
                "message_count": message_count,
                "ui_event_count": ui_event_count,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def db_for(self, username: str, scope: ChatScope) -> Path:
        if scope.kind == "home":
            return _state_root() / username / "_home" / "chat.db"
        state_dir = project_canvas_chat_state_dir(
            _state_root() / username / str(scope.id),
            scope.canvas_id,
        )
        return state_dir / "chat.db"

    def _connect_path(self, db_path: Path) -> sqlite3.Connection:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn)
        ensure_chat_schema(conn)
        conn.commit()
        return conn

    def connect(self, username: str, scope: ChatScope) -> sqlite3.Connection:
        return self._connect_path(self.db_for(username, scope))

    def append_message(
        self,
        username: str,
        scope: ChatScope,
        role: str,
        content: str,
        media: list[dict[str, Any]] | None = None,
        *,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        media = media or []
        metadata = sanitize_chat_metadata(metadata)
        normalized_turn_id = str(turn_id or "").strip() or None
        conversation_id = normalize_conversation_id(scope.conversation_id)
        if role == "assistant":
            content = _strip_infrastructure_error_tail(content)
            if not content.strip():
                raise ValueError(
                    "assistant content contains only infrastructure error text"
                )
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self.connect(username, scope)
        try:
            self._ensure_conversation(conn, conversation_id)
            if role == "assistant" and normalized_turn_id:
                row = conn.execute(
                    """
                    INSERT INTO chat_messages(
                        role, content, media_json, conversation_id, turn_id, metadata_json, created_at
                    )
                    VALUES ('assistant', ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id, turn_id)
                      WHERE role='assistant' AND turn_id IS NOT NULL AND turn_id <> ''
                    DO UPDATE SET
                        content=excluded.content,
                        media_json=excluded.media_json,
                        metadata_json=excluded.metadata_json
                    RETURNING id, created_at
                    """,
                    (
                        content,
                        json.dumps(media, ensure_ascii=False),
                        conversation_id,
                        normalized_turn_id,
                        json.dumps(metadata, ensure_ascii=False),
                        created_at,
                    ),
                ).fetchone()
                self._touch_conversation(conn, conversation_id)
                conn.commit()
                return {
                    "id": int(row["id"]),
                    "role": role,
                    "content": content,
                    "media": media,
                    "attachments": media,
                    "turn_id": normalized_turn_id,
                    "conversation_id": conversation_id,
                    **({"metadata": metadata} if metadata else {}),
                    "created_at": str(row["created_at"]),
                }
            cursor = conn.execute(
                """
                INSERT INTO chat_messages(role, content, media_json, conversation_id, turn_id, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    role,
                    content,
                    json.dumps(media, ensure_ascii=False),
                    conversation_id,
                    normalized_turn_id,
                    json.dumps(metadata, ensure_ascii=False),
                    created_at,
                ),
            )
            self._touch_conversation(
                conn,
                conversation_id,
                first_user_text=content if role == "user" else "",
            )
            conn.commit()
            return {
                "id": int(cursor.lastrowid),
                "role": role,
                "content": content,
                "media": media,
                "attachments": media,
                "conversation_id": conversation_id,
                **({"turn_id": normalized_turn_id} if normalized_turn_id else {}),
                **({"metadata": metadata} if metadata else {}),
                "created_at": created_at,
            }
        finally:
            conn.close()

    def append_ui_event(
        self,
        username: str,
        scope: ChatScope,
        turn_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        event_type = str(
            event.get("type") or event.get("event_type") or "ui_event"
        ).strip()
        event_id = str(event.get("receiptId") or event.get("event_id") or "").strip()
        conversation_id = normalize_conversation_id(scope.conversation_id)
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self.connect(username, scope)
        try:
            self._ensure_conversation(conn, conversation_id)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO chat_ui_events(
                  turn_id, event_type, conversation_id, receipt_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    event_type,
                    conversation_id,
                    event_id,
                    json.dumps(event, ensure_ascii=False),
                    created_at,
                ),
            )
            if event_id and cursor.rowcount == 0:
                existing = conn.execute(
                    """
                    SELECT id, payload_json, created_at
                      FROM chat_ui_events
                     WHERE conversation_id=? AND turn_id=? AND event_type=? AND receipt_id=?
                    """,
                    (conversation_id, turn_id, event_type, event_id),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("receipt idempotency conflict was not persisted")
                conn.commit()
                return {
                    "id": int(existing["id"]),
                    "turn_id": turn_id,
                    "type": event_type,
                    "payload": json.loads(existing["payload_json"] or "{}"),
                    "created_at": str(existing["created_at"]),
                    "idempotent_replay": True,
                }
            conn.commit()
            return {
                "id": int(cursor.lastrowid),
                "turn_id": turn_id,
                "type": event_type,
                "payload": event,
                "created_at": created_at,
                "idempotent_replay": False,
            }
        finally:
            conn.close()

    def list_ui_events(
        self,
        username: str,
        scope: ChatScope,
        *,
        event_type: str | None = None,
        limit: int = 96,
    ) -> list[dict[str, Any]]:
        """Return a bounded, oldest-first replay tail for the active conversation."""

        conversation_id = normalize_conversation_id(scope.conversation_id)
        normalized_type = str(event_type or "").strip()
        normalized_limit = min(max(int(limit), 1), 512)
        conn = self.connect(username, scope)
        try:
            params: list[Any] = [conversation_id]
            event_filter = ""
            if normalized_type:
                event_filter = " AND event_type=?"
                params.append(normalized_type)
            params.append(normalized_limit)
            rows = conn.execute(
                f"""
                SELECT id, turn_id, event_type, payload_json, created_at
                  FROM chat_ui_events
                 WHERE conversation_id=?{event_filter}
                 ORDER BY id DESC
                 LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in reversed(rows):
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except json.JSONDecodeError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {"value": payload}
                result.append(
                    {
                        "id": int(row["id"]),
                        "turn_id": str(row["turn_id"] or ""),
                        "type": str(row["event_type"] or "ui_event"),
                        "created_at": str(row["created_at"]),
                        **payload,
                    }
                )
            return result
        finally:
            conn.close()

    def _load_ui_events(
        self, conn: sqlite3.Connection, conversation_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        rows = conn.execute(
            """
            SELECT id, turn_id, event_type, payload_json, created_at
              FROM chat_ui_events
              WHERE conversation_id=?
              ORDER BY id ASC
            """
            ,
            (conversation_id,),
        ).fetchall()
        events_by_turn: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            turn_id = str(row["turn_id"] or "").strip()
            if not turn_id:
                continue
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {"value": payload}
            payload = {
                "id": int(row["id"]),
                "type": str(row["event_type"] or payload.get("type") or "ui_event"),
                "turn_id": turn_id,
                "created_at": str(row["created_at"]),
                **payload,
            }
            events_by_turn.setdefault(turn_id, []).append(payload)
        return events_by_turn

    @staticmethod
    def _attach_ui_events_to_messages(
        messages: list[dict[str, Any]],
        events_by_turn: dict[str, list[dict[str, Any]]],
    ) -> None:
        if not messages or not events_by_turn:
            return
        for turn_id, events in events_by_turn.items():
            if not events:
                continue
            target_index: int | None = None
            for index, message in enumerate(messages):
                if (
                    message.get("role") == "assistant"
                    and message.get("turn_id") == turn_id
                ):
                    target_index = index
                    break
            if target_index is None:
                user_index = next(
                    (
                        index
                        for index, message in enumerate(messages)
                        if message.get("role") == "user"
                        and message.get("turn_id") == turn_id
                    ),
                    None,
                )
                if user_index is not None:
                    for index in range(user_index + 1, len(messages)):
                        if messages[index].get("role") == "assistant":
                            target_index = index
                            break
            if target_index is None:
                continue
            existing = messages[target_index].get("ui_events")
            if not isinstance(existing, list):
                existing = []
            messages[target_index]["ui_events"] = [*existing, *events]

    def list_messages(
        self,
        username: str,
        scope: ChatScope,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conn = self.connect(username, scope)
        try:
            conversation_id = normalize_conversation_id(scope.conversation_id)
            self._ensure_conversation(conn, conversation_id)
            rows = conn.execute(
                """
                SELECT id, role, content, media_json, turn_id, metadata_json, created_at
                  FROM chat_messages
                 WHERE role <> 'trace' AND conversation_id=?
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
            events_by_turn = self._load_ui_events(conn, conversation_id)
        finally:
            conn.close()
        messages: list[dict[str, Any]] = []
        previous_assistants: list[str] = []
        for row in reversed(rows):
            try:
                media = json.loads(row["media_json"] or "[]")
            except json.JSONDecodeError:
                media = []
            role = str(row["role"])
            content = str(row["content"])
            if role == "assistant":
                content = _strip_infrastructure_error_tail(content)
                if not content.strip():
                    continue
                raw_content = content
                content = _strip_replayed_assistant_prefix(content, previous_assistants)
                previous_assistants.append(raw_content)
                if not content.strip():
                    continue
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata = sanitize_chat_metadata(metadata)
            if _is_context_compression_failure(content, metadata):
                continue
            messages.append(
                {
                    "id": int(row["id"]),
                    "role": role,
                    "content": content,
                    "media": media if isinstance(media, list) else [],
                    "attachments": media if isinstance(media, list) else [],
                    **({"turn_id": str(row["turn_id"])} if row["turn_id"] else {}),
                    **metadata,
                    **({"metadata": metadata} if metadata else {}),
                    "created_at": str(row["created_at"]),
                }
            )
        self._attach_ui_events_to_messages(messages, events_by_turn)
        return filter_stale_director_clarifications(messages)

    def find_assistant_message_for_workflow_run(
        self,
        username: str,
        scope: ChatScope,
        *,
        workflow_run_id: str,
        limit: int = 2000,
    ) -> dict[str, Any] | None:
        """Find the newest assistant turn bound to one WorkflowRun."""

        return self.find_assistant_message_for_workflow_run_in_db(
            self.db_for(username, scope),
            workflow_run_id=workflow_run_id,
            limit=limit,
        )

    def find_assistant_message_for_workflow_run_in_db(
        self,
        db_path: str | Path,
        *,
        workflow_run_id: str,
        limit: int = 2000,
    ) -> dict[str, Any] | None:
        """Find a bound assistant turn in the service project chat database."""

        normalized_run_id = str(workflow_run_id or "").strip()
        if not normalized_run_id:
            return None
        return self.find_assistant_messages_for_workflow_runs_in_db(
            db_path,
            workflow_run_ids={normalized_run_id},
            limit=limit,
        ).get(normalized_run_id)

    def find_assistant_messages_for_workflow_runs_in_db(
        self,
        db_path: str | Path,
        *,
        workflow_run_ids: set[str],
        limit: int = 2000,
    ) -> dict[str, dict[str, Any]]:
        """Return the newest bound assistant row for each requested Run."""

        normalized_run_ids = {
            str(run_id or "").strip()
            for run_id in workflow_run_ids
            if str(run_id or "").strip()
        }
        if not normalized_run_ids:
            return {}
        conn = self._connect_path(Path(db_path))
        try:
            rows = conn.execute(
                """
                SELECT id, content, media_json, turn_id, metadata_json, created_at
                  FROM chat_messages
                 WHERE role='assistant'
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        finally:
            conn.close()
        found: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(metadata, dict):
                continue
            delivery = metadata.get("delivery_verification")
            if not isinstance(delivery, dict):
                continue
            run_id = str(delivery.get("workflow_run_id") or "").strip()
            if run_id not in normalized_run_ids or run_id in found:
                continue
            try:
                media = json.loads(row["media_json"] or "[]")
            except json.JSONDecodeError:
                media = []
            found[run_id] = {
                "id": int(row["id"]),
                "content": str(row["content"] or ""),
                "media": media if isinstance(media, list) else [],
                "turn_id": str(row["turn_id"] or ""),
                "metadata": sanitize_chat_metadata(metadata),
                "created_at": str(row["created_at"] or ""),
            }
            if len(found) == len(normalized_run_ids):
                break
        return found

    def update_assistant_message(
        self,
        username: str,
        scope: ChatScope,
        *,
        message_id: int,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update one assistant row without changing its turn identity."""

        return self.update_assistant_message_in_db(
            self.db_for(username, scope),
            message_id=message_id,
            content=content,
            metadata=metadata,
            conversation_id=normalize_conversation_id(scope.conversation_id),
        )

    def update_assistant_message_in_db(
        self,
        db_path: str | Path,
        *,
        message_id: int,
        content: str,
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Update one service-project assistant row by its stable row id."""

        normalized_content = _strip_infrastructure_error_tail(content)
        if not normalized_content.strip():
            raise ValueError("assistant content contains only infrastructure error text")
        normalized_conversation_id = (
            normalize_conversation_id(conversation_id) if conversation_id else None
        )
        conn = self._connect_path(Path(db_path))
        try:
            where = "id=? AND role='assistant'"
            params: list[Any] = [int(message_id)]
            if normalized_conversation_id is not None:
                where += " AND conversation_id=?"
                params.append(normalized_conversation_id)
            cursor = conn.execute(
                f"""
                UPDATE chat_messages
                   SET content=?, metadata_json=?
                 WHERE {where}
                """,
                (
                    normalized_content,
                    json.dumps(
                        sanitize_chat_metadata(metadata),
                        ensure_ascii=False,
                    ),
                    *params,
                ),
            )
            if cursor.rowcount <= 0:
                conn.rollback()
                return None
            row = conn.execute(
                f"""
                SELECT id, content, media_json, turn_id, metadata_json,
                       created_at, conversation_id
                  FROM chat_messages
                 WHERE {where}
                """,
                params,
            ).fetchone()
            if row is not None:
                self._touch_conversation(conn, str(row["conversation_id"]))
            conn.commit()
            if row is None:
                return None
            try:
                media = json.loads(row["media_json"] or "[]")
            except json.JSONDecodeError:
                media = []
            try:
                stored_metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                stored_metadata = {}
            return {
                "id": int(row["id"]),
                "content": str(row["content"] or ""),
                "media": media if isinstance(media, list) else [],
                "turn_id": str(row["turn_id"] or ""),
                "metadata": (
                    sanitize_chat_metadata(stored_metadata)
                    if isinstance(stored_metadata, dict)
                    else {}
                ),
                "created_at": str(row["created_at"] or ""),
            }
        finally:
            conn.close()


chat_store = ChatStore()
