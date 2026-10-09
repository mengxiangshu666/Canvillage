"""Dedicated log file for the agent runtime.

The Agent's own records (``capability broker stage=...``, ``tool executor
stage=...``, turn lifecycle) used to be mixed into the daemon's raw stderr.
Mirror the ``novelvideo.api`` pattern instead: one rotating file where the
Agent's own diagnostics land.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_AGENT_LOGGER_NAME = "novelvideo.chat"


def configure_agent_log() -> None:
    """Attach one rotating file handler to the agent's logger. Idempotent.

    Never raises: logging setup must not be able to break agent startup, and
    tests import this package without a writable runtime directory.
    """

    try:
        from novelvideo.config import RUNTIME_DIR

        log_path = str(Path(RUNTIME_DIR) / "agent.log")
        logger = logging.getLogger(_AGENT_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        existing = next(
            (
                handler
                for handler in logger.handlers
                if isinstance(handler, RotatingFileHandler)
                and getattr(handler, "baseFilename", "") == log_path
            ),
            None,
        )
        if existing is not None:
            return
        Path(RUNTIME_DIR).mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except Exception:
        return
