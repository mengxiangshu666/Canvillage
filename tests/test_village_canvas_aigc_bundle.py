from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import unquote


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "agent_skills" / "village-canvas-aigc-knowledge"
BUILDER_PATH = SKILL_ROOT / "scripts" / "build_vault_bundle.py"
BUNDLE_ROOT = SKILL_ROOT / "references" / "vault-aigc"


def _load_builder():
    spec = importlib.util.spec_from_file_location("village_canvas_aigc_bundle", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_builder_is_allowlisted_redacted_auditable_and_reproducible(tmp_path: Path) -> None:
    builder = _load_builder()
    vault = tmp_path / "vault"
    output = tmp_path / "bundle"

    allowed_core = vault / "AIGC" / "02_生图模型与提示词_AI版.md"
    allowed_core.parent.mkdir(parents=True)
    allowed_core.write_text(
        "# 生图方法\n模型输入位于 C:\\Users\\someone\\private\\input.png。\n",
        encoding="utf-8",
    )

    allowed_topic = vault / "AIGC" / "调色与配乐" / "色彩方法#公开版.md"
    allowed_topic.parent.mkdir(parents=True)
    allowed_topic.write_text("# 色彩方法\n只保留可复用的方法论。\n", encoding="utf-8")

    allowed_tool = vault / "AIGC导演" / "工具库" / "director.md"
    allowed_tool.parent.mkdir(parents=True)
    allowed_tool.write_text("# 导演工具\n镜头合同与验收点。\n", encoding="utf-8")

    # Build the fake credential at runtime so repository secret scanners do not
    # mistake this regression fixture for a committed plaintext key.
    fake_secret = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"
    secret_candidate = vault / "AIGC" / "调色与配乐" / "provider-example.md"
    secret_candidate.write_text(
        f"API_KEY={fake_secret}\n", encoding="utf-8"
    )
    binary_candidate = vault / "AIGC导演" / "工具库" / "archive.zip"
    binary_candidate.write_bytes(b"PK\x03\x04not-public-methodology")

    excluded = {
        vault / "10-我是谁" / "个人档案.md": "个人资料",
        vault / "40-每日记录" / "2026-07-27.md": "每日记录",
        vault / "90-Codex-Memory" / "memory.md": "agent memory",
        vault / "AIGC" / "学习笔记" / "daily.md": "学习记录",
        vault / "AIGC" / "我不是药神" / "project.md": "项目资料",
        vault / "运维" / "remote.md": "ssh HOST",
        vault / "misc" / "unscoped.md": "不在公开 allowlist 中",
    }
    for path, content in excluded.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    manifest_first = builder.build(vault, output)
    manifest_bytes_first = (output / "manifest.json").read_bytes()
    index_bytes_first = (output / "INDEX.md").read_bytes()
    source_snapshot_first = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in sorted((output / "source").rglob("*"))
        if path.is_file()
    }

    manifest_second = builder.build(vault, output)

    assert manifest_first == manifest_second
    assert (output / "manifest.json").read_bytes() == manifest_bytes_first
    assert (output / "INDEX.md").read_bytes() == index_bytes_first
    assert {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in sorted((output / "source").rglob("*"))
        if path.is_file()
    } == source_snapshot_first

    assert manifest_first["schema_version"] == 2
    assert manifest_first["scope"] == "public_aigc_methodology"
    assert manifest_first["owner"] == "Village Infinite Canvas"
    assert manifest_first["source"]["name"] == "xiaoshu-brain"
    assert "source_hash" in manifest_first
    assert "bundle_hash" in manifest_first
    assert str(vault) not in json.dumps(manifest_first, ensure_ascii=False)

    included_paths = {item["path"] for item in manifest_first["files"]}
    assert included_paths == {
        "AIGC/02_生图模型与提示词_AI版.md",
        "AIGC/调色与配乐/色彩方法#公开版.md",
        "AIGC导演/工具库/director.md",
    }

    core_record = next(
        item
        for item in manifest_first["files"]
        if item["path"] == "AIGC/02_生图模型与提示词_AI版.md"
    )
    bundled_core = (output / core_record["bundle_path"]).read_bytes()
    assert b"C:\\Users\\someone" not in bundled_core
    assert "<LOCAL_PATH>" in bundled_core.decode("utf-8")
    assert core_record["source_sha256"] == _sha256(allowed_core.read_bytes())
    assert core_record["sha256"] == _sha256(bundled_core)
    assert core_record["redactions"]["local_path"] == 1

    audit = manifest_first["exclusion_audit"]
    assert audit["default_policy"] == "deny"
    assert audit["excluded_files"] == len(audit["entries"])
    reasons = {item["reason"] for item in audit["reasons"]}
    assert "outside_allowlist" in reasons
    assert "disallowed_extension" in reasons
    assert "sensitive_content" in reasons
    assert all(set(item) == {"path_hash", "reason", "extension", "size"} for item in audit["entries"])

    serialized_manifest = json.dumps(manifest_first, ensure_ascii=False)
    for private_label in ("个人档案", "每日记录", "Codex-Memory", "我不是药神"):
        assert private_label not in serialized_manifest
    assert fake_secret not in serialized_manifest
    assert not (output / "source" / secret_candidate.relative_to(vault)).exists()
    assert not (output / "source" / binary_candidate.relative_to(vault)).exists()

    index = (output / "INDEX.md").read_text(encoding="utf-8")
    assert "%23" in index
    assert "C:\\Users\\someone" not in index


def test_public_text_redacts_windows_paths_with_either_separator() -> None:
    builder = _load_builder()
    text, counts = builder._redact_public_text(
        r"Download to E:/private/project/clip.mp4 or C:\Users\example\clip.mp4"
    )
    assert text == "Download to <LOCAL_PATH> or <LOCAL_PATH>"
    assert counts == {"local_path": 2}


def test_checked_in_bundle_matches_manifest_and_has_resolvable_index_links() -> None:
    builder = _load_builder()
    manifest = json.loads((BUNDLE_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 2
    assert manifest["scope"] == "public_aigc_methodology"
    assert manifest["owner"] == "Village Infinite Canvas"
    assert manifest["summary"]["included_files"] == len(manifest["files"])
    assert manifest["exclusion_audit"]["excluded_files"] == len(
        manifest["exclusion_audit"]["entries"]
    )

    expected_source_hash_rows: list[str] = []
    expected_bundle_hash_rows: list[str] = []
    published_hashes: set[str] = set()
    for item in manifest["files"]:
        bundled_path = BUNDLE_ROOT / item["bundle_path"]
        data = bundled_path.read_bytes()
        text = data.decode("utf-8")
        assert _sha256(data) == item["sha256"]
        assert bundled_path.stat().st_size == item["size"]
        assert item["extension"] in builder.ALLOWED_TEXT_EXTENSIONS
        assert not builder._contains_secret(text)
        assert all(not pattern.search(text) for _, pattern, _ in builder.REDACTION_PATTERNS)
        assert item["sha256"] not in published_hashes
        published_hashes.add(item["sha256"])
        assert Path(item["path"]).parts[0] in {"AIGC", "AIGC导演"}
        assert not any(
            blocked.casefold() in part.casefold()
            for part in Path(item["path"]).parts
            for blocked in (
                "我是谁",
                "每日记录",
                "Codex-Memory",
                "学习笔记",
                "我不是药神",
                "等不到的老汉儿",
                "运维",
                "服务器",
                "凭据",
            )
        )
        expected_source_hash_rows.append(
            "\0".join(
                (
                    item["path"],
                    item["category"],
                    item["source_sha256"],
                    str(item["source_size"]),
                )
            )
        )
        expected_bundle_hash_rows.append(
            "\0".join(
                (
                    item["bundle_path"],
                    item["category"],
                    item["sha256"],
                    str(item["size"]),
                )
            )
        )

    assert manifest["source_hash"] == _sha256(
        "\n".join(expected_source_hash_rows).encode("utf-8")
    )
    assert manifest["bundle_hash"] == _sha256(
        "\n".join(expected_bundle_hash_rows).encode("utf-8")
    )

    serialized_manifest = json.dumps(manifest, ensure_ascii=False)
    assert "D:\\超级小树" not in serialized_manifest
    assert "C:\\Users" not in serialized_manifest
    assert all(
        private_label not in serialized_manifest
        for private_label in (
            "10-我是谁",
            "40-每日记录",
            "90-Codex-Memory",
            "我不是药神",
            "等不到的老汉儿",
        )
    )

    index = (BUNDLE_ROOT / "INDEX.md").read_text(encoding="utf-8")
    links = [
        line.split("](", 1)[1].split(")", 1)[0]
        for line in index.splitlines()
        if line.startswith("- [") and "](" in line
    ]
    assert len(links) == len(manifest["files"])
    assert all((BUNDLE_ROOT / unquote(link)).is_file() for link in links)
