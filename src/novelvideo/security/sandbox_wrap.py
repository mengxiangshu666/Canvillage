"""Cross-platform sandbox wrapper for Hermes worker subprocesses.

Design (per plan):
- READ:  whitelist (system libs + self business dirs + shared repo resources).
         Host secrets (~/.ssh etc.) and other users explicitly denied.
- WRITE: only HERMES_HOME (state/{user}/.hermes/). Business writes go via API.

Linux:  codex-linux-sandbox binary (bwrap + seccomp; workspace-write mode).
macOS:  /usr/bin/sandbox-exec + dynamically composed sbpl profile
        (base policy copied from openai/codex, MIT licensed,
         in deploy/sandbox/seatbelt_base_policy.sbpl).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_log = logging.getLogger(__name__)

# Village Infinite Canvas repo root: src/novelvideo/security/sandbox_wrap.py → parents[3]
VILLAGE_CANVAS_ROOT = Path(__file__).resolve().parents[3]
SANDBOX_PROFILES_DIR = VILLAGE_CANVAS_ROOT / "deploy" / "sandbox"
SEATBELT_BASE_POLICY = SANDBOX_PROFILES_DIR / "seatbelt_base_policy.sbpl"
SEATBELT_NETWORK_POLICY = SANDBOX_PROFILES_DIR / "seatbelt_network_policy.sbpl"


def _data_dir(kind: str) -> Path:
    env = os.environ.get(f"NOVELVIDEO_{kind.upper()}_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return VILLAGE_CANVAS_ROOT / kind


@dataclass
class SandboxSpec:
    """Per-user sandbox configuration.

    Only `user` is required. Other fields auto-derive defaults sensible for
    the Village Infinite Canvas layout (state/{user}/, output/{user}/, runtime/{user}/).
    """

    user: str
    hermes_home: Path | None = None  # default state/{user}/.hermes
    extra_read_paths: list[Path] = field(default_factory=list)

    def resolved_hermes_home(self) -> Path:
        return self.hermes_home or (_data_dir("state") / self.user / ".hermes")

    def self_business_paths(self) -> list[Path]:
        """The user's own state/output/runtime trees (read+write for hermes_home,
        read-only for the rest — sandbox enforces write side)."""
        return [
            _data_dir("state") / self.user,
            _data_dir("output") / self.user,
            _data_dir("runtime") / self.user,
        ]

    def shared_read_paths(self) -> list[Path]:
        """Project-wide read-only resources."""
        paths: list[Path] = [
            _data_dir("state") / "_shared",
            VILLAGE_CANVAS_ROOT / "src",
            VILLAGE_CANVAS_ROOT / "integrations",
            VILLAGE_CANVAS_ROOT / "agent_skills",  # repo-pinned Agent skills
            VILLAGE_CANVAS_ROOT / ".venv",  # Village Infinite Canvas main venv for skill scripts
        ]
        return paths

    def other_user_paths(self) -> list[Path]:
        """All other users' state/output/runtime trees — must be denied."""
        result: list[Path] = []
        for top in ("state", "output", "runtime"):
            top_dir = _data_dir(top)
            if not top_dir.is_dir():
                continue
            for child in top_dir.iterdir():
                if not child.is_dir():
                    continue
                if child.name in (self.user, "_shared"):
                    continue
                result.append(child)
        return result


def _host_secret_paths() -> list[Path]:
    """Host credential/secret locations that every OS backend must deny.

    Shared by the macOS Seatbelt profile and the Linux managed permission
    profile so the two branches cannot drift apart.
    """
    return [
        Path.home() / ".ssh",
        Path.home() / ".gnupg",
        Path.home() / ".aws",
        Path.home() / ".kube",
        Path.home() / ".docker",
        Path("/etc/shadow"),
        Path("/etc/sudoers"),
        Path("/etc/sudoers.d"),
    ]


def wrap_command(cmd: list[str], spec: SandboxSpec) -> list[str]:
    """Return `cmd` wrapped with OS sandbox.

    Linux:  prefixes with codex-linux-sandbox CLI (bwrap-based).
    macOS:  prefixes with /usr/bin/sandbox-exec -p '<profile>' -- ...
    Other (e.g. Windows): no sandbox backend → fallback path below.

    Fallback (sandbox binary missing or no backend for this OS):
    - VILLAGE_CANVAS_ENV=production → raise (must sandbox in prod).
    - Otherwise → warn and return raw cmd (dev convenience).
    """
    system = platform.system()
    if system == "Linux":
        return _wrap_linux(cmd, spec)
    if system == "Darwin":
        return _wrap_macos(cmd, spec)
    return _fallback_or_raise(cmd, f"no sandbox backend on {system}")


def _wrap_linux(cmd: list[str], spec: SandboxSpec) -> list[str]:
    binary = shutil.which("codex-linux-sandbox") or "/usr/local/bin/codex-linux-sandbox"
    if not Path(binary).exists():
        return _fallback_or_raise(cmd, "codex-linux-sandbox not found on PATH")

    hermes_home = spec.resolved_hermes_home()
    entries: list[dict] = [
        {
            "path": {"type": "special", "value": {"kind": "root"}},
            "access": "read",
        },
    ]
    # Deny host secrets and other users' trees explicitly. The broad root read
    # above would otherwise expose them, contradicting the module contract.
    # The bwrap/Landlock helper rejects deny paths it cannot resolve, so only
    # list paths that actually exist (a missing path has nothing to deny).
    denied_paths = [
        path
        for path in (*_host_secret_paths(), *spec.other_user_paths())
        if path.exists()
    ]
    for denied in denied_paths:
        entries.append(
            {
                "path": {"type": "path", "path": str(denied)},
                "access": "deny",
            }
        )
    entries.append(
        {
            "path": {"type": "path", "path": str(hermes_home)},
            "access": "write",
        }
    )
    permission_profile = {
        "type": "managed",
        "file_system": {
            "type": "restricted",
            "entries": entries,
        },
        "network": "restricted",
    }
    args = [
        binary,
        "--sandbox-policy-cwd",
        str(hermes_home),
        "--command-cwd",
        str(hermes_home),
        "--permission-profile",
        json.dumps(permission_profile, separators=(",", ":")),
    ]
    args.append("--")
    return args + cmd


def _wrap_macos(cmd: list[str], spec: SandboxSpec) -> list[str]:
    profile = build_macos_profile(spec)
    return ["/usr/bin/sandbox-exec", "-p", profile, "--", *cmd]


def _aliases(p: Path) -> list[Path]:
    """Both literal path and /private-resolved form (macOS firmlinks).

    `/tmp` and `/etc` and `/var` are symlinks to `/private/tmp` etc., and
    Seatbelt rules need the *real* paths to match syscalls reliably.
    """
    s = str(p)
    out = [p]
    if s.startswith("/tmp/") or s == "/tmp":
        out.append(Path("/private" + s))
    elif s.startswith("/etc/") or s == "/etc":
        out.append(Path("/private" + s))
    elif s.startswith("/var/") or s == "/var":
        out.append(Path("/private" + s))
    return out


def _expand_aliases(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        for alt in _aliases(p):
            s = str(alt)
            if s not in seen:
                out.append(alt)
                seen.add(s)
    return out


def build_macos_profile(spec: SandboxSpec) -> str:
    """Compose full Seatbelt profile (base + per-user dynamic section).

    Order matters: Seatbelt uses *last-match-wins*. We arrange so that
    base (deny default) → broad allows → specific denies → final HERMES_HOME write allow.
    """
    if not SEATBELT_BASE_POLICY.is_file():
        raise FileNotFoundError(
            f"Seatbelt base policy missing: {SEATBELT_BASE_POLICY} "
            f"(run `cp ~/Documents/GitHub/codex/codex-rs/sandboxing/src/"
            f"seatbelt_base_policy.sbpl deploy/sandbox/`)"
        )
    base = SEATBELT_BASE_POLICY.read_text(encoding="utf-8")
    # codex's base profile is intentionally split — it requires the network
    # policy to be appended to function. Without it, sandbox-exec aborts
    # silently when the child process tries platform services (mach-lookup,
    # sysctl, etc.). See codex-rs/sandboxing/src/seatbelt.rs:292.
    if SEATBELT_NETWORK_POLICY.is_file():
        base = base + "\n" + SEATBELT_NETWORK_POLICY.read_text(encoding="utf-8")

    home = spec.resolved_hermes_home()

    parts: list[str] = ["\n;; ===== Village Infinite Canvas per-user dynamic policy =====\n"]

    # Hermes workers need outbound network access for the configured LLM
    # provider and Village API calls. File writes and agent-side tools remain
    # constrained by the rules below and by the per-user toolset whitelist.
    parts.append("\n;; NETWORK: allow outbound LLM/API calls\n")
    parts.append("(allow network-outbound)\n")

    # --- READ allow: broad ((subpath "/") — same as codex workspace-write mode) ---
    # Rationale: macOS dyld needs many paths to launch even `cat`; strict
    # subpath whitelist is unmaintainable. Defense relies on explicit DENY
    # of host secrets + other-user dirs below, which override this broad allow.
    parts.append(";; READ: broad allow; specific denies below override\n")
    parts.append("(allow file-read* (subpath \"/\"))\n")

    # --- READ deny: host secrets (specific denies override broad allow) ---
    parts.append("\n;; READ deny: host secrets — overrides broad allow\n")
    parts.append(_deny_read_block(_expand_aliases(_host_secret_paths())))

    # --- READ deny: other users ---
    other = list(spec.other_user_paths())
    if other:
        parts.append("\n;; READ deny: other users' state/output/runtime\n")
        parts.append(_deny_read_block(other))

    # --- WRITE deny: explicit host /tmp + other users (must come BEFORE allow HERMES_HOME) ---
    #     (base profile already `deny default`, but be explicit about /tmp and other users)
    if other:
        parts.append("\n;; WRITE deny: other users' state/output/runtime\n")
        parts.append(_deny_write_block(other))
    parts.append("\n;; WRITE deny: host /tmp (must use $TMPDIR=$HERMES_HOME/tmp)\n")
    parts.append(_deny_write_block(_expand_aliases([Path("/tmp")])))

    # --- WRITE allow: HERMES_HOME (LAST so last-match-wins keeps it allowed even if
    #     HERMES_HOME happens to live under /tmp during dev) ---
    parts.append("\n;; WRITE: only HERMES_HOME (last so this wins over /tmp deny)\n")
    parts.append(_allow_write_block(_expand_aliases([home])))

    return base + "".join(parts)


def _allow_read_block(paths: Iterable[Path]) -> str:
    lines = ["(allow file-read*"]
    for p in paths:
        lines.append(f'  (subpath "{p}")')
    lines.append(")\n")
    return "\n".join(lines)


def _deny_read_block(paths: Iterable[Path]) -> str:
    lines = ["(deny file-read*"]
    for p in paths:
        lines.append(f'  (subpath "{p}")')
    lines.append(")\n")
    return "\n".join(lines)


def _allow_write_block(paths: Iterable[Path]) -> str:
    lines = ["(allow file-write*"]
    for p in paths:
        lines.append(f'  (subpath "{p}")')
    lines.append(")\n")
    return "\n".join(lines)


def _deny_write_block(paths: Iterable[Path]) -> str:
    lines = ["(deny file-write*"]
    for p in paths:
        lines.append(f'  (subpath "{p}")')
    lines.append(")\n")
    return "\n".join(lines)


def _fallback_or_raise(cmd: list[str], reason: str) -> list[str]:
    if os.environ.get("VILLAGE_CANVAS_ENV", "").lower() == "production":
        raise RuntimeError(f"sandbox required in production but {reason}")
    msg = f"sandbox unavailable ({reason}); running unsandboxed — dev only"
    _log.warning(msg)
    warnings.warn(msg, RuntimeWarning, stacklevel=3)
    return cmd
