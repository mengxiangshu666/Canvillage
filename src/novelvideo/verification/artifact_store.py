"""Content-addressable artifact store for director-OS data.

All large payloads (prompts, model responses, gate verdicts, grid images,
individual sketches referenced by traces) land under a single
`artifacts/<sha256_prefix>/<sha256>.<ext>` tree so identical bytes
are naturally deduplicated. Call sites only ever see the resolved
path plus the hash; they never pick filenames by hand.

Only the `artifact_store` module should own the layout — callers must
not invent their own subdirectories under `global_shared_artifacts_dir`.
"""

from __future__ import annotations

import gzip
import hashlib
import os
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactRef:
    path: Path
    sha256: str
    size_bytes: int

    @property
    def artifact_id(self) -> str:
        """Stable join key shared by Agent, WorkflowRun and verifier."""

        return f"artifact:{self.sha256[:32]}"

    @property
    def artifact_uri(self) -> str:
        """Project-owned URI; never a provider URL or signed download link."""

        return f"artifact://{self.sha256}"

    def to_agent_artifact(self, **kwargs: object) -> dict[str, object]:
        """Return the versioned Agent artifact contract for this stored file."""

        from .agent_artifacts import agent_artifact_from_store

        return agent_artifact_from_store(self, **kwargs)


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _target_path(root: Path, sha: str, ext: str) -> Path:
    ext_clean = ext.lstrip(".")
    prefix = sha[:2]
    return root / prefix / f"{sha}.{ext_clean}"


def write_bytes(root: Path, data: bytes, *, ext: str) -> ArtifactRef:
    """Write raw bytes content-addressably. Idempotent on identical bytes."""
    sha = compute_sha256(data)
    target = _target_path(root, sha, ext)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return ArtifactRef(path=target, sha256=sha, size_bytes=len(data))


def write_text(root: Path, text: str, *, ext: str = "txt") -> ArtifactRef:
    return write_bytes(root, text.encode("utf-8"), ext=ext)


def write_json_gz(root: Path, payload: str | bytes) -> ArtifactRef:
    """Gzip-compress a JSON string then store. Use for verdicts / responses."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    compressed = gzip.compress(payload)
    return write_bytes(root, compressed, ext="json.gz")


def copy_file_in(root: Path, src: Path, *, ext: str | None = None) -> ArtifactRef:
    """Read a file from disk, hash its bytes, land it into the store.

    The copy is streamed so generated videos do not have to fit in memory.
    An existing artifact is reused, and a same-volume source is hard-linked
    before falling back to a streamed copy. Both paths are content addressed
    and retain the source artifact if the original output is later cleaned.
    """

    source = Path(src)
    resolved_ext = ext if ext is not None else source.suffix.lstrip(".") or "bin"
    digest = hashlib.sha256()
    size_bytes = 0
    with source.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    sha = digest.hexdigest()
    target = _target_path(root, sha, resolved_ext)
    if target.exists():
        return ArtifactRef(path=target, sha256=sha, size_bytes=size_bytes)

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        copied_digest = hashlib.sha256()
        copied_size = 0
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    copied_digest.update(chunk)
                    copied_size += len(chunk)
            copied_sha = copied_digest.hexdigest()
            # The source might change while a task is finalizing. Store the
            # exact bytes that were copied instead of attaching a stale hash.
            copied_target = _target_path(root, copied_sha, resolved_ext)
            copied_target.parent.mkdir(parents=True, exist_ok=True)
            if copied_target.exists():
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, copied_target)
            return ArtifactRef(path=copied_target, sha256=copied_sha, size_bytes=copied_size)
        finally:
            temporary.unlink(missing_ok=True)
    return ArtifactRef(path=target, sha256=sha, size_bytes=size_bytes)


def resolve_path(root: Path, sha: str, ext: str) -> Path:
    """Pure path arithmetic — does not check existence."""
    return _target_path(root, sha, ext)


def read_bytes(path: Path) -> bytes:
    return Path(path).read_bytes()


def read_json_gz_text(path: Path) -> str:
    data = Path(path).read_bytes()
    return gzip.decompress(data).decode("utf-8")
