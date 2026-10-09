"""Detached Cunzhang Infinite Canvas API runner.

Survives parent console exit and writes bounded logs below NOVELVIDEO_DATA_ROOT.
"""
from __future__ import annotations

import os
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_LOG_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 3
ROTATED_LOG_NAMES = (
    "backend.log",
    "backend-daemon.out.log",
    "backend-daemon.err.log",
)


def _data_dir() -> Path:
    return Path(
        os.environ.get("NOVELVIDEO_DATA_ROOT")
        or str(Path(__file__).resolve().parent / "项目资产")
    )


def _apply_data_root_defaults(data: Path) -> None:
    """Align direct runner defaults with the canonical portable data tree."""
    os.environ.setdefault("NOVELVIDEO_DATA_ROOT", str(data))
    os.environ.setdefault("NOVELVIDEO_STATE_DIR", str(data / "state"))
    os.environ.setdefault("NOVELVIDEO_OUTPUT_DIR", str(data / "output"))
    os.environ.setdefault("NOVELVIDEO_RUNTIME_DIR", str(data / "runtime"))


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _rotate_logs(log_dir: Path) -> list[Path]:
    """Bound every portable backend log before this process opens its handles."""
    from village_canvas_rotate_log import rotate_log

    max_bytes = _positive_env_int("VILLAGE_CANVAS_LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES)
    backups = _positive_env_int("VILLAGE_CANVAS_LOG_BACKUPS", DEFAULT_LOG_BACKUPS)
    rotated: list[Path] = []
    for name in ROTATED_LOG_NAMES:
        path = log_dir / name
        try:
            if rotate_log(path, max_bytes, backups):
                rotated.append(path)
        except OSError as exc:
            print(f"[Village Infinite Canvas] log rotation skipped for {path}: {exc}", file=sys.__stderr__)
    return rotated


def _safe_host(value: object) -> str:
    """Return a log-safe host without query strings, paths or credentials."""
    raw = str(value or "").strip()
    if not raw:
        return "<empty>"
    try:
        parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return "<invalid>"
    return parsed.hostname or "<invalid>"


def _runtime_route_summary(gateway: object) -> dict[str, object]:
    """Build a non-secret startup summary for model and gateway routing."""
    try:
        from novelvideo.generators.direct_models import resolve_direct_model

        agent = resolve_direct_model("agent")
        vision = resolve_direct_model("vision")
    except Exception as exc:  # pragma: no cover - startup diagnostics are best effort
        return {"registry": "unavailable", "registry_error": type(exc).__name__}

    def model_summary(model: object | None) -> dict[str, object]:
        if model is None:
            return {"source": "unconfigured", "key_state": "missing"}
        api_key = str(getattr(model, "api_key", "") or "").strip()
        return {
            "source": "direct-registry",
            "model": str(getattr(model, "upstream_model", "") or "").strip(),
            "host": _safe_host(getattr(model, "base_url", "")),
            "key_state": "present" if api_key else "missing",
        }

    gateway_base_url = str(getattr(gateway, "base_url", "") or "").strip()
    gateway_key = str(getattr(gateway, "api_key", "") or "").strip()
    return {
        "edition": os.environ.get("ST_EDITION", "").strip().lower() or "<unset>",
        "agent": model_summary(agent),
        "vision": model_summary(vision),
        "gateway": {
            "source": str(getattr(gateway, "source", "") or "unknown"),
            "host": _safe_host(gateway_base_url),
            "key_state": "present" if gateway_key else "missing",
        },
    }


def main() -> int:
    data = _data_dir()
    # Keep direct invocation on the same persistent tree as the official
    # launcher. Without these defaults, config.py falls back to the current
    # working directory, whose `state` junction points at the development
    # workspace and makes saved projects/models appear to disappear.
    _apply_data_root_defaults(data)
    log_dir = data / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    rotated_logs = _rotate_logs(log_dir)
    out_path = log_dir / "backend-daemon.out.log"
    err_path = log_dir / "backend-daemon.err.log"
    pid_path = log_dir / "api.pid"

    # Append so restarts don't wipe history; line-buffer for live tails.
    sys.stdout = open(out_path, "a", encoding="utf-8", buffering=1, errors="replace")
    sys.stderr = open(err_path, "a", encoding="utf-8", buffering=1, errors="replace")

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"\n===== village_canvas_run_api start {stamp} pid={os.getpid()} =====", flush=True)
    print(f"===== village_canvas_run_api start {stamp} pid={os.getpid()} =====", file=sys.stderr, flush=True)
    if rotated_logs:
        names = ", ".join(path.name for path in rotated_logs)
        print(f"[Village Infinite Canvas] rotated logs before startup: {names}", flush=True)
    try:
        pid_path.write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass

    # Ensure package import path (Start.bat also sets PYTHONPATH).
    root = Path(__file__).resolve().parent
    env_path = root / "runtime" / "env"
    for p in (
        env_path,
        env_path / "win32",
        env_path / "win32" / "lib",
        env_path / "Pythonwin",
    ):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    source_path = root / "src"
    if source_path.is_dir():
        source = str(source_path)
        if source in sys.path:
            sys.path.remove(source)
        # The portable package keeps the editable source tree beside this
        # launcher. Prefer it over the frozen runtime/env copy so desktop fixes
        # take effect immediately after restart.
        sys.path.insert(0, source)

    os.environ.setdefault("NOVELVIDEO_API_HOST", "127.0.0.1")
    os.environ.setdefault("NOVELVIDEO_API_PORT", "8784")
    if not str(os.environ.get("ST_EDITION") or "").strip():
        os.environ["ST_EDITION"] = "ce"
    # The shipped 8784 runtime only publishes models whose upstream catalog
    # identity has been confirmed. Library/test callers remain migration-safe.
    os.environ.setdefault("VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS", "1")
    os.environ.setdefault("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1")
    # The portable runtime does not use Pydantic telemetry plugins. Disabling
    # discovery avoids importing the optional logfire plugin and its absent
    # google.protobuf dependency on every cold start.
    os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")

    # Collapse legacy official/custom records into the launcher-owned HK
    # gateway before any API module resolves a model client. The helper never
    # logs credentials and keeps old records untouched for rollback only.
    from novelvideo.model_gateway_settings import ensure_unified_gateway_migration

    gateway = ensure_unified_gateway_migration()
    print(
        "[Village Infinite Canvas] unified gateway active "
        f"source={gateway.source} host={_safe_host(gateway.base_url)}",
        flush=True,
    )
    print(
        "[Village Infinite Canvas] runtime routes "
        + json.dumps(_runtime_route_summary(gateway), ensure_ascii=False, sort_keys=True),
        flush=True,
    )

    # The portable canary is a persistent local service. Browser presence is
    # still reported for diagnostics, but closing the last canvas tab must not
    # take down queued work, task polling, or the model gateway connection.
    os.environ.setdefault("VILLAGE_CANVAS_BROWSER_AUTO_SHUTDOWN", "false")
    from novelvideo.api.app import app as asgi_app
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(asgi_app, host=os.environ.get("NOVELVIDEO_API_HOST", "127.0.0.1"), port=int(os.environ.get("NOVELVIDEO_API_PORT", "8784")), log_level="info")
    )

    def request_local_shutdown() -> None:
        server.should_exit = True

    asgi_app.state.local_shutdown_callback = request_local_shutdown
    try:
        code = server.run()
        return int(code or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception:
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
