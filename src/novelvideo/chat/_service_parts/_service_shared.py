"""AI chat service with project-scoped history and user-level agent sessions."""

from __future__ import annotations

import asyncio
import copy
import ctypes
import errno
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sqlite3
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from novelvideo.chat.backend_sdk import (
    ClaudeSdkClient,
    interrupt_live_claude_client,
)
from novelvideo.chat.context_checkpoint import (
    build_checkpoint,
    checkpoint_prompt,
    load_checkpoint,
    save_checkpoint,
    workflow_run_recovery_checkpoint,
)
from novelvideo.chat.context_budget import budget_from_model_config
from novelvideo.chat.engine import AgentEngine, normalize_agent_engine
from novelvideo.chat.agent_models import (
    DirectVillageAgentModelConfig,
    list_village_agent_models,
    resolve_village_direct_agent_model,
)
from novelvideo.chat.memory_index import (
    apply_execution_feedback,
    build_knowledge_packet,
    capture_growth_distillation_event,
    claim_growth_distillation_events,
    materialize_growth_distillation_result,
    finalize_growth_distillation_event,
    is_serial_continuity_task,
    knowledge_task_stage,
    memory_influence_receipt_payload,
    parse_growth_distillation_event,
    record_memory_influence_receipts,
    record_execution_episode,
    requeue_growth_distillation_event,
    save_growth_distillation_candidate,
    GROWTH_DISTILLATION_RETRY_DELAYS,
    remember_successful_turn,
)
from novelvideo.chat.memory_feedback import (
    classify_execution_feedback,
    has_actionable_teaching_signal,
)
from novelvideo.chat.expert_arbitration import build_expert_plan
from novelvideo.chat.agent_runtime import (
    AgentHandoffLedger,
    find_specialist_result,
)
from novelvideo.chat.shared_context import (
    build_canvas_observation,
    build_shared_agent_context,
    project_reference_manifest,
)
from novelvideo.chat.taste_graph import build_taste_graph
from novelvideo.freezone.reference_manifest import build_canvas_reference_manifest
from novelvideo.chat.tool_allowlist import compile_tool_allowlist
from novelvideo.chat.identity_compat import (
    CANONICAL_CHAT_RECOVERY_SCHEMA,
    CANONICAL_RECOVERY_CLOSE_MARKER,
    CANONICAL_RECOVERY_OPEN_MARKER,
    LEGACY_PRODUCT_ID,
    normalize_payload_schema,
    normalize_prompt_text,
    normalize_tool_name,
    read_compat_env,
)
from novelvideo.chat.store import (
    DEFAULT_CHAT_CONVERSATION_ID,
    ChatScope,
    chat_store,
    ensure_chat_schema,
    normalize_conversation_id,
    sanitize_chat_metadata,
    filter_stale_director_clarifications,
)
from novelvideo.chat.tool_events import (
    tool_event_call_id,
    tool_event_kind,
    tool_event_terminal,
    tool_payload_failed,
)
from novelvideo.chat.workflow_stage_receipts import (
    project_workflow_release_readiness,
)
from novelvideo.ports import get_auth_session_port
from novelvideo.sqlite_pragmas import configure_sqlite_connection
from novelvideo.utils.error_redaction import redact_secrets
from novelvideo.utils.static_urls import project_static_url
from novelvideo.utils.turn_scope import turn_command_scope

logger = logging.getLogger("novelvideo.chat.service")
_GROWTH_TASKS: set[asyncio.Task[object]] = set()
_GROWTH_EVENTS_IN_FLIGHT: set[tuple[str, int]] = set()




__all__ = [name for name in globals() if not name.startswith("__")]
