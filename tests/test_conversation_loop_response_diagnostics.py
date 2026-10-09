"""Hermes 0.21 removed the normalized-response shape diagnostic.

0.18 logged a bounded ``LLM response normalized shape`` record through
``agent.conversation_loop._response_structure_diagnostic`` and this suite pinned
its no-content guarantee. Upstream dropped the helper entirely, so keep a guard
that notices if a future upgrade reintroduces a shape logger.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


RUNTIME_HERMES = Path(__file__).resolve().parents[1] / "runtime" / "hermes"
if str(RUNTIME_HERMES) not in sys.path:
    sys.path.insert(0, str(RUNTIME_HERMES))

conversation_loop = pytest.importorskip("agent.conversation_loop")


def test_normalized_response_shape_diagnostic_is_absent() -> None:
    assert not hasattr(conversation_loop, "_response_structure_diagnostic")
