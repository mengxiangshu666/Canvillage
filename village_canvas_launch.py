# -*- coding: utf-8 -*-
"""村长无限画布启动器（绕开 cmd 对 UTF-8 bat 的解析乱码）。

用 Python 按 UTF-8 读取 Start.bat 的全部 `set "KEY=value"` 环境变量
（按行序展开 %VAR% 引用），设置到进程环境后启动 village_canvas_run_api.py。

用途：
  1) 修复 cmd/GBK 代码页下 Start.bat 乱码导致无法启动的问题；
  2) 作为「网页生命周期」启动入口（后续可叠加空闲退出逻辑）。

用法:
  python village_canvas_launch.py            # 启动 8784（若已在运行则仅打开网页）
  python village_canvas_launch.py --web      # 仅打开网页（不重复启动）
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
START_BAT = ROOT / "启动村长无限画布.bat"
PYTHON = ROOT / "runtime" / "python" / "python.exe"
PYTHONW = ROOT / "runtime" / "python" / "pythonw.exe"
RUN_API = ROOT / "village_canvas_run_api.py"
OPEN_WHEN_READY = ROOT / "village_canvas_open_when_ready.py"
API_URL = "http://127.0.0.1:8784"
PID_FILE = ROOT / "runtime" / "api.pid"

_SET_RE = re.compile(r'^\s*set\s+"([A-Za-z_][A-Za-z0-9_]*)=(.*?)"\s*$')
_SETP_RE = re.compile(r'^\s*set\s+/p\s+([A-Za-z_][A-Za-z0-9_]*)=\s*<?"?([^<"]+)"?\s*$')
_IF_NOT_DEFINED_SET_RE = re.compile(
    r'^\s*if\s+not\s+defined\s+([A-Za-z_][A-Za-z0-9_]*)\s+'
    r'set\s+"([A-Za-z_][A-Za-z0-9_]*)=(.*?)"\s*$',
    re.IGNORECASE,
)
_IF_EQUALS_SET_RE = re.compile(
    r'^\s*if\s+/I\s+"%([A-Za-z_][A-Za-z0-9_]*)%"=="([^"]*)"\s+'
    r'set\s+"([A-Za-z_][A-Za-z0-9_]*)=(.*?)"\s*$',
    re.IGNORECASE,
)
_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def _read_startbat_env() -> dict[str, str]:
    """Extract every `set "K=V"` and `set /p K=<file` from Start.bat.

    %VAR% references are expanded in line order; `%~dp0` is the bat directory
    (== ROOT).  `set /p` reads the value from a file (e.g. media-relay-token).
    The two conditional forms used by the canonical bat are evaluated so VBS,
    bat and deployment startup produce the same environment.
    """
    if not START_BAT.exists():
        raise FileNotFoundError(f"Start.bat not found: {START_BAT}")
    text = START_BAT.read_text(encoding="utf-8", errors="replace")
    env: dict[str, str] = dict(os.environ)

    def _expand(value: str) -> str:
        expanded = value.replace("%~dp0", str(ROOT) + os.sep)
        return _VAR_RE.sub(lambda mm: env.get(mm.group(1), mm.group(0)), expanded)

    for raw in text.splitlines():
        # The canonical bat keeps an old fallback block after ``endlocal``.
        # Cmd never executes that block, so the Python launcher must stop at
        # the same boundary instead of importing dead legacy settings.
        if raw.strip().casefold() == "endlocal":
            break
        conditional_default = _IF_NOT_DEFINED_SET_RE.match(raw)
        if conditional_default:
            condition_key, key, value = conditional_default.groups()
            if not env.get(condition_key, ""):
                env[key] = _expand(value)
            continue
        conditional_equal = _IF_EQUALS_SET_RE.match(raw)
        if conditional_equal:
            condition_key, expected, key, value = conditional_equal.groups()
            if env.get(condition_key, "").casefold() == expected.casefold():
                env[key] = _expand(value)
            continue
        m = _SET_RE.match(raw)
        if m:
            key, value = m.group(1), m.group(2)
            env[key] = _expand(value)
            continue
        mp = _SETP_RE.match(raw)
        if mp:
            key, path_expr = mp.group(1), mp.group(2)
            path_text = _expand(path_expr).strip().strip('"')
            try:
                path = Path(path_text)
                if path.is_file():
                    env[key] = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                pass
    return env


def _is_running() -> bool:
    """Return true only when the API and the SPA shell are both reachable."""
    for path in ("/healthz", "/"):
        request = urllib.request.Request(
            f"{API_URL}{path}",
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


def _open_web() -> None:
    try:
        webbrowser.open(_with_launch_cache_bust(f"{API_URL}/"))
    except Exception:
        pass


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


def main() -> int:
    web_only = "--web" in sys.argv
    if _is_running():
        if not web_only:
            print("[村长无限画布] 8784 已在运行，仅打开网页。")
        _open_web()
        return 0

    env = _read_startbat_env()
    # 兜底：确保关键变量即使 bat 解析异常也存在
    env.setdefault("PYTHONUTF8", "1")
    # Empty inherited values are not usable; treat them like missing values so
    # the CE portable runtime cannot fail its bootstrap guard after a restart.
    if not str(env.get("ST_EDITION") or "").strip():
        env["ST_EDITION"] = "ce"
    env.setdefault("DATA_DIR", str(ROOT / "项目资产"))
    env.setdefault("VILLAGE_CANVAS_WORKSPACE_ROOT", str(ROOT / "workspace"))
    workspace_root = Path(env["VILLAGE_CANVAS_WORKSPACE_ROOT"])
    env.setdefault("VILLAGE_CANVAS_ARTIFACTS_DIR", str(workspace_root / "artifacts"))
    env.setdefault("VILLAGE_CANVAS_BACKUPS_DIR", str(workspace_root / "backups"))
    env.setdefault("VILLAGE_CANVAS_CACHE_DIR", str(workspace_root / "cache"))
    env.setdefault("NOVELVIDEO_DATA_ROOT", env["DATA_DIR"])
    # %~dp0 (Start.bat 所在目录) 等价于 ROOT；Python 提取时需展开
    for _key, _val in list(env.items()):
        if isinstance(_val, str) and "%~dp0" in _val:
            env[_key] = _val.replace("%~dp0", str(ROOT) + os.sep)
    env.setdefault("ROOT", str(ROOT))
    if env.get("ROOT") in (None, "", "%~dp0"):
        env["ROOT"] = str(ROOT)
    # 前端产物目录：路径无效时强制指向 ROOT/frontend/dist
    frontend_dist = env.get("VILLAGE_CANVAS_FRONTEND_DIST", "")
    if not frontend_dist or "%" in frontend_dist or not Path(frontend_dist).is_dir():
        env["VILLAGE_CANVAS_FRONTEND_DIST"] = str(ROOT / "frontend" / "dist")
    agent_skills = env.get("VILLAGE_CANVAS_AGENT_SKILLS_DIR", "")
    if not agent_skills or "%" in agent_skills or not Path(agent_skills).is_dir():
        env["VILLAGE_CANVAS_AGENT_SKILLS_DIR"] = str(ROOT / "agent_skills")

    # The parser starts from os.environ and overlays the canonical contract.
    merged = env

    print("[村长无限画布] 启动 8784 ...")
    # 与 Start.bat L192/L193 相同的两个后台进程（无黑窗）
    creationflags = 0x08000000  # CREATE_NO_WINDOW
    if PYTHONW.exists():
        subprocess.Popen(
            [str(PYTHONW), str(OPEN_WHEN_READY)],
            cwd=str(ROOT),
            env=merged,
            creationflags=creationflags,
        )
    subprocess.Popen(
        [str(PYTHON), str(RUN_API)],
        cwd=str(ROOT),
        env=merged,
        creationflags=creationflags,
    )

    # 等待就绪
    for _ in range(30):
        if _is_running():
            print("[村长无限画布] 8784 已就绪。")
            _open_web()
            return 0
        time.sleep(2)
    print("[村长无限画布] 启动超时（30s），8784 未就绪；请查看 项目资产/logs/backend-daemon.err.log。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
