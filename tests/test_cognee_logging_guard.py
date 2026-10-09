"""Regression coverage for Cognee's process-wide logging side effects."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def _run_python_probe(tmp_path: Path, script: str, *, log_file: str | None) -> None:
    """Run an isolated interpreter because imports mutate global logging state."""
    logs_dir = tmp_path / "cognee-logs"
    env = os.environ.copy()
    env["COGNEE_LOGS_DIR"] = str(logs_dir)
    env["ST_EDITION"] = "ce"
    env.pop("LOG_FILE_NAME", None)
    if log_file is None:
        env.pop("COGNEE_LOG_FILE", None)
    else:
        env["COGNEE_LOG_FILE"] = log_file

    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_cognee_import_preserves_host_logging_and_disables_private_log(tmp_path):
    _run_python_probe(
        tmp_path,
        """
        import io
        import logging
        import os
        import sys
        from contextlib import redirect_stderr

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        marker_filter = logging.Filter("application")
        root = logging.getLogger()
        root.handlers[:] = [handler]
        root.filters[:] = [marker_filter]
        root.setLevel(logging.WARNING)
        original_excepthook = sys.excepthook

        import novelvideo.cognee.config  # noqa: F401
        from cognee.shared.logging_utils import PlainFileHandler, setup_logging

        late_setup_stderr = io.StringIO()
        with redirect_stderr(late_setup_stderr):
            setup_logging()

        assert root.handlers == [handler]
        assert root.filters == [marker_filter]
        assert root.level == logging.WARNING
        assert sys.excepthook is original_excepthook
        assert "COGNEE_LOG_FILE" not in os.environ
        assert late_setup_stderr.getvalue() == ""
        assert getattr(setup_logging, "_novelvideo_logging_guard", False)
        assert not any(isinstance(item, PlainFileHandler) for item in root.handlers)
        """,
        log_file=None,
    )

    assert not list((tmp_path / "cognee-logs").glob("*.log"))


def test_cognee_preimport_detaches_private_handler_and_guards_late_setup(tmp_path):
    _run_python_probe(
        tmp_path,
        """
        import io
        import logging

        # Simulate an integration importing Cognee before Village Infinite Canvas gets an
        # opportunity to protect the host process logging configuration.
        import cognee  # noqa: F401
        from cognee.shared.logging_utils import PlainFileHandler

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.WARNING)

        import novelvideo.cognee.config  # noqa: F401
        from cognee.shared.logging_utils import setup_logging

        assert "Cognee was imported before Village Infinite Canvas installed its logging guard" in (
            stream.getvalue()
        )
        assert not any(isinstance(item, PlainFileHandler) for item in root.handlers)
        assert getattr(setup_logging, "_novelvideo_logging_guard", False)

        setup_logging()
        logging.getLogger("application.probe").warning("post-guard-file-probe")
        for item in root.handlers:
            item.flush()
        """,
        log_file="true",
    )

    private_logs = list((tmp_path / "cognee-logs").glob("*.log"))
    assert all(
        "post-guard-file-probe" not in path.read_text(encoding="utf-8")
        for path in private_logs
    )
