"""Open the local diagnostics page after the infinite canvas API is ready."""

from __future__ import annotations

import os
import time
import urllib.parse
import urllib.error
import urllib.request
import webbrowser


DEFAULT_API_URL = "http://127.0.0.1:8784"
READY_TIMEOUT_SECONDS = 180
POLL_SECONDS = 2


def _api_url() -> str:
    return os.environ.get("VILLAGE_CANVAS_API_URL", DEFAULT_API_URL).rstrip("/")


def _with_launch_cache_bust(url: str) -> str:
    """Add a per-launch query so old cached SPA shells are bypassed."""
    parts = urllib.parse.urlsplit(url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key != "__village_canvas_launch"
    ]
    query.append(("__village_canvas_launch", str(int(time.time() * 1000))))
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def _is_ready(base_url: str) -> bool:
    for path in ("/healthz", "/"):
        request = urllib.request.Request(
            f"{base_url}{path}",
            headers={"Accept": "application/json,text/html"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                if not 200 <= response.status < 300:
                    return False
        except (OSError, urllib.error.URLError):
            return False
    return True


def main() -> int:
    base_url = _api_url()
    # Prefer explicit env; never default to missing mod-diagnostics.html (404).
    open_url = (
        os.environ.get("VILLAGE_CANVAS_DIAGNOSTICS_URL")
        or os.environ.get("VILLAGE_CANVAS_OPEN_URL")
        or f"{base_url}/"
    )
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        if _is_ready(base_url):
            webbrowser.open(_with_launch_cache_bust(open_url), new=2)
            return 0
        time.sleep(POLL_SECONDS)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
