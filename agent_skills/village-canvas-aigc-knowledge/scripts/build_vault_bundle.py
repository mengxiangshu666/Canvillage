"""Build a deterministic, public AIGC methodology bundle.

The source vault contains private project notes and machine-local state. This
builder therefore uses a default-deny allowlist, copies text only, removes
machine locators from otherwise public notes, and records anonymous exclusion
evidence without publishing excluded paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote


SCHEMA_VERSION = 2
BUNDLE_SCOPE = "public_aigc_methodology"
BUNDLE_OWNER = "Village Infinite Canvas"
SOURCE_NAME = "xiaoshu-brain"
MAX_SOURCE_FILE_BYTES = 512 * 1024
ALLOWED_TEXT_EXTENSIONS = frozenset({".md", ".txt"})
RETIRED_PRODUCT_NOTE = PurePosixPath(
    "AIGC导演/工具库/" + "drama" + "claw.md"
)

# These directories contain reusable methodology rather than user profiles,
# project state, daily notes, machine operations, or agent memory.
ALLOWED_DIRECTORIES: dict[str, str] = {
    "AIGC/调色与配乐": "post-production",
    "AIGC/抖音爆款": "short-video-strategy",
    "AIGC/剪辑语言": "editing",
    "AIGC/剧作结构": "storytelling",
    "AIGC/摄影机实拍": "cinematography",
    "AIGC/声音设计": "audio",
    "AIGC/演员表演": "performance",
    "AIGC导演": "director-tools",
}

# Top-level AIGC notes are listed explicitly so adding a new vault file cannot
# silently expand the public bundle.
ALLOWED_FILES: dict[str, str] = {
    "AIGC/01_LibTV完整使用手册_AI版.md": "canvas-workflow",
    "AIGC/02_生图模型与提示词_AI版.md": "image-generation",
    "AIGC/03_视频大模型与提示词_AI版.md": "video-generation",
    "AIGC/04_剧本创作方法论_AI版.md": "storytelling",
    "AIGC/05_AI注意力机制权威指南_AI版.md": "agent-context",
    "AIGC/06_v7.0框架总结与兄弟判断框架.md": "production-framework",
    "AIGC/07_兄弟角色设定表工作流_AI版.md": "character-workflow",
    "AIGC/08_上下文工程4大支柱映射_AI版.md": "agent-context",
    "AIGC/10_电影摄影学体系_AI版.md": "cinematography",
    "AIGC/11_叙事诡计分类学_AI版.md": "storytelling",
    "AIGC/12_补充_当代数字艺术.md": "visual-style",
    "AIGC/12_补充_地域民族艺术.md": "visual-style",
    "AIGC/12_补充_历史艺术运动.md": "visual-style",
    "AIGC/12_补充_亚文化视觉.md": "visual-style",
    "AIGC/12_全画风参数体系_AI版.md": "visual-style",
    "AIGC/13_角色心理学与对话深度_AI版.md": "character-workflow",
    "AIGC/14_视听叙事学术基础_AI版.md": "cinematography",
    "AIGC/15_文化与讽刺语法_AI版.md": "storytelling",
    "AIGC/16_AIGC失败修复手册_实战版.md": "failure-repair",
    "AIGC/17_AI声音设计与台词提示词.md": "audio",
    "AIGC/18_ComfyUI工作流体系.md": "node-workflow",
    "AIGC/19_AI电影全工作流.md": "production-workflow",
    "AIGC/20_LoRA训练与模型微调.md": "model-training",
    "AIGC/21_AI 3D 资产生成.md": "3d-assets",
    "AIGC/22_AI配音调色后制.md": "post-production",
    "AIGC/23_AI创作者版权合规与商业化.md": "governance",
    "AIGC/99_权威来源与参考文献.md": "governance",
    "AIGC/AI漫剧生态全景-工具与案例.md": "production-workflow",
    "AIGC/AI漫剧制作全流程-手把手.md": "production-workflow",
    "AIGC/AIGC导演视听语言框架.md": "cinematography",
    "AIGC/AIGC短剧全链路能力学习路径.md": "production-workflow",
    "AIGC/AIGC画布深度学习-终极大总结.md": "canvas-workflow",
    "AIGC/AIGC视频导演实战手册.md": "video-generation",
    "AIGC/AIGC视频导演通用框架.md": "video-generation",
    "AIGC/AIGC提示词翻译引擎规则库.md": "prompt-engineering",
    "AIGC/AIGC铁律汇总.md": "production-standards",
    "AIGC/AIGC铁律索引_v1.0.md": "production-standards",
    "AIGC/AIGC铁律体系_v1.md": "production-standards",
    "AIGC/AIGC知识体系-总索引.md": "navigation",
    "AIGC/patch-01_seedance-2.5-update.md": "video-generation",
    "AIGC/patch-02_多角色控制与迭代方法论.md": "character-workflow",
    "AIGC/patch-03_agentic-ai-video.md": "agent-context",
    "AIGC/patch-04_视频扩展与帧插值.md": "video-generation",
    "AIGC/patch-05_场景化提示词模板库.md": "prompt-engineering",
    "AIGC/patch-06_成本镜头智能优化.md": "production-workflow",
    "AIGC/patch-07_libtv_模型参数速查表.md": "canvas-workflow",
    "AIGC/Seedance-2.0-Skill-OS.md": "video-generation",
    "AIGC/video-reverse-engineering-数字人带货.md": "short-video-strategy",
}

BLOCKED_PATH_TERMS = tuple(
    value.casefold()
    for value in (
        "我是谁",
        "个人",
        "简历",
        "履历",
        "档案",
        "每日",
        "日记",
        "周报",
        "月报",
        "memory",
        "codex-memory",
        "学习笔记",
        "实时同步",
        "grok同步",
        "等不到的老汉儿",
        "我不是药神",
        "远程",
        "运维",
        "服务器",
        "ssh",
        "wireguard",
        "vpn",
        "凭据",
        "密钥",
        "password",
        "secret",
        "token",
        "auth",
        "账号",
        "账户",
        "主机",
        "部署",
    )
)

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|DSA)? ?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk|ak|ghp|github_pat|xox[baprs]|AIza)[-_A-Za-z0-9]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]{16,}\b", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"\b(?:api[_-]?key|secret|token|password|passwd|client_secret|"
        r"access_token|private_key)\b\s*[:=]\s*[\"']?"
        r"(?!<|\{|\$|YOUR_|REDACTED|PLACEHOLDER)"
        r"[A-Za-z0-9._~+\-/=]{12,}",
        re.I,
    ),
)

REDACTION_PATTERNS = (
    (
        "project_reference",
        re.compile(r"等不到的老汉儿|我不是药神"),
        "<PROJECT_CASE>",
    ),
    (
        "local_path",
        re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/][^\s`\"'<>|\r\n]+|/(?:Users|home)/[^\s`\"'<>\r\n]+)"),
        "<LOCAL_PATH>",
    ),
    (
        "network_path",
        re.compile(r"\\\\[^\s`\"'<>|\\/]+\\[^\s`\"'<>|\r\n]+"),
        "<NETWORK_PATH>",
    ),
    (
        "host_address",
        re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
        "<HOST>",
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def headings(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^#{1,3}\s+", line)
    ][:20]


def _path_is_blocked(rel: PurePosixPath) -> bool:
    if rel == RETIRED_PRODUCT_NOTE:
        return True
    return any(
        blocked in part.casefold()
        for part in rel.parts
        for blocked in BLOCKED_PATH_TERMS
    )


def _allowlisted_category(rel: PurePosixPath) -> str | None:
    path_text = rel.as_posix()
    if path_text in ALLOWED_FILES:
        return ALLOWED_FILES[path_text]
    for root_text, category in ALLOWED_DIRECTORIES.items():
        root = PurePosixPath(root_text)
        if root in rel.parents:
            return category
    return None


def _contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _redact_public_text(text: str) -> tuple[str, dict[str, int]]:
    redactions: Counter[str] = Counter()
    sanitized = text
    for label, pattern, replacement in REDACTION_PATTERNS:
        sanitized, count = pattern.subn(replacement, sanitized)
        if count:
            redactions[label] += count
    sanitized, count = re.subn(r"[ \t]+(?=\r?$)", "", sanitized, flags=re.MULTILINE)
    if count:
        redactions["trailing_whitespace"] += count
    return sanitized, dict(sorted(redactions.items()))


def _audit_entry(rel: PurePosixPath, *, reason: str, size: int) -> dict[str, Any]:
    return {
        "path_hash": sha256(rel.as_posix().encode("utf-8")),
        "reason": reason,
        "extension": rel.suffix.lower(),
        "size": size,
    }


def _fingerprint_rows(rows: list[str]) -> str:
    return sha256("\n".join(rows).encode("utf-8"))


def _manifest_policy() -> dict[str, Any]:
    allowlist = [
        {"path": path, "kind": "directory", "category": category}
        for path, category in sorted(ALLOWED_DIRECTORIES.items())
    ]
    allowlist.extend(
        {"path": path, "kind": "file", "category": category}
        for path, category in sorted(ALLOWED_FILES.items())
    )
    return {
        "default": "deny",
        "allowlist": allowlist,
        "allowed_extensions": sorted(ALLOWED_TEXT_EXTENSIONS),
        "max_source_file_bytes": MAX_SOURCE_FILE_BYTES,
        "excluded_scopes": [
            "personal-profile",
            "project-specific",
            "daily-record",
            "agent-memory",
            "remote-operations",
            "machine-locators",
            "credentials",
            "large-or-binary",
        ],
        "excluded_paths_are_hashed": True,
    }


def _write_bundle(
    output: Path,
    *,
    manifest: dict[str, Any],
    bundled_files: list[tuple[dict[str, Any], bytes]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    source_dir = output / "source"
    if source_dir.exists():
        shutil.rmtree(source_dir)
    source_dir.mkdir(parents=True)

    for record, data in bundled_files:
        target = output / record["bundle_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Village Infinite Canvas public AIGC methodology bundle",
        "",
        f"- Schema: **{manifest['schema_version']}**",
        f"- Scope: **{manifest['scope']}**",
        f"- Included files: **{manifest['summary']['included_files']}**",
        f"- Source hash: `{manifest['source_hash'][:16]}`",
        "",
        "按任务类别选择少量文档读取；不要一次性加载整个 bundle。",
        "",
    ]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for record, _ in bundled_files:
        by_category.setdefault(record["category"], []).append(record)
    for category in sorted(by_category):
        records = sorted(by_category[category], key=lambda item: item["path"])
        lines.append(f"## {category} ({len(records)})")
        lines.append("")
        for record in records:
            title = PurePosixPath(record["path"]).stem
            encoded_link = quote(record["bundle_path"], safe="/._-")
            lines.append(
                f"- [{title}]({encoded_link}) — {record['lines']} 行，"
                f"SHA256 `{record['sha256'][:12]}`"
            )
        lines.append("")
    (output / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def build(vault: Path, output: Path) -> dict[str, Any]:
    if not vault.is_dir():
        raise SystemExit(f"vault not found: {vault}")

    bundled_files: list[tuple[dict[str, Any], bytes]] = []
    exclusions: list[dict[str, Any]] = []
    total_files = 0
    total_bytes = 0

    for path in sorted((path for path in vault.rglob("*") if path.is_file())):
        total_files += 1
        rel = PurePosixPath(path.relative_to(vault).as_posix())
        size = path.stat().st_size
        total_bytes += size
        category = _allowlisted_category(rel)

        if _path_is_blocked(rel):
            exclusions.append(_audit_entry(rel, reason="blocked_scope", size=size))
            continue
        if category is None:
            exclusions.append(_audit_entry(rel, reason="outside_allowlist", size=size))
            continue
        if rel.suffix.lower() not in ALLOWED_TEXT_EXTENSIONS:
            exclusions.append(_audit_entry(rel, reason="disallowed_extension", size=size))
            continue
        if size > MAX_SOURCE_FILE_BYTES:
            exclusions.append(_audit_entry(rel, reason="oversized", size=size))
            continue

        source_data = path.read_bytes()
        source_text = source_data.decode("utf-8", errors="replace")
        if _contains_secret(source_text):
            exclusions.append(_audit_entry(rel, reason="sensitive_content", size=size))
            continue

        public_text, redactions = _redact_public_text(source_text)
        public_data = public_text.encode("utf-8")
        bundle_path = f"source/{rel.as_posix()}"
        record = {
            "path": rel.as_posix(),
            "bundle_path": bundle_path,
            "category": category,
            "extension": rel.suffix.lower(),
            "source_size": len(source_data),
            "source_sha256": sha256(source_data),
            "size": len(public_data),
            "sha256": sha256(public_data),
            "lines": public_text.count("\n") + (1 if public_text else 0),
            "headings": headings(public_text),
            "redactions": redactions,
        }
        bundled_files.append((record, public_data))

    bundled_files.sort(key=lambda item: item[0]["path"])
    exclusions.sort(key=lambda item: item["path_hash"])
    file_records = [record for record, _ in bundled_files]

    source_hash = _fingerprint_rows(
        [
            "\0".join(
                (
                    record["path"],
                    record["category"],
                    record["source_sha256"],
                    str(record["source_size"]),
                )
            )
            for record in file_records
        ]
    )
    bundle_hash = _fingerprint_rows(
        [
            "\0".join(
                (
                    record["bundle_path"],
                    record["category"],
                    record["sha256"],
                    str(record["size"]),
                )
            )
            for record in file_records
        ]
    )
    reason_counts = Counter(item["reason"] for item in exclusions)
    reason_bytes: Counter[str] = Counter()
    for item in exclusions:
        reason_bytes[item["reason"]] += item["size"]

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope": BUNDLE_SCOPE,
        "owner": BUNDLE_OWNER,
        "source": {"name": SOURCE_NAME, "locator": "vault-relative"},
        "source_hash": source_hash,
        "bundle_hash": bundle_hash,
        "policy": _manifest_policy(),
        "summary": {
            "source_files_seen": total_files,
            "source_bytes_seen": total_bytes,
            "included_files": len(file_records),
            "included_source_bytes": sum(item["source_size"] for item in file_records),
            "published_bytes": sum(item["size"] for item in file_records),
            "redacted_values": sum(
                sum(item["redactions"].values()) for item in file_records
            ),
        },
        "exclusion_audit": {
            "default_policy": "deny",
            "excluded_files": len(exclusions),
            "excluded_bytes": sum(item["size"] for item in exclusions),
            "reasons": [
                {
                    "reason": reason,
                    "files": reason_counts[reason],
                    "bytes": reason_bytes[reason],
                }
                for reason in sorted(reason_counts)
            ],
            "entries": exclusions,
        },
        "files": file_records,
    }
    _write_bundle(output, manifest=manifest, bundled_files=bundled_files)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.vault, args.output)
    print(
        json.dumps(
            {
                "schema_version": manifest["schema_version"],
                "scope": manifest["scope"],
                "source_hash": manifest["source_hash"],
                "included_files": manifest["summary"]["included_files"],
                "excluded_files": manifest["exclusion_audit"]["excluded_files"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
