from __future__ import annotations

import io
import json
import zipfile

import pytest

from novelvideo.skills_distill.admission import scan_skill
from novelvideo.skills_distill import install as skill_install
from novelvideo.skills_distill import store


def _item(**overrides):
    body = overrides.pop(
        "body",
        """## Purpose
完成一个可追踪的画布任务。

## Inputs
用户目标和当前画布事实。

## Workflow
1. 读取当前状态
2. 执行一个正式命令

## Outputs
返回命令回执和新 revision。

## Quality
核对 command_id、revision 和结果状态。
""",
    )
    return {
        "id": "custom.demo-skill",
        "skill_key": "demo-skill",
        "name": "Demo Skill",
        "description": "一个可执行画布技能",
        "body": body,
        "contract": {
            "schema_version": "canvas_skill_contract.v1",
            "maturity": "production_ready",
            "readiness_score": 90,
        },
        "source_manifest": {
            "schema": "skill.provenance.v1",
            "source_kind": "user_authored",
            "source_ref": "demo.md",
            "license": "User-provided",
            "content_sha256": __import__("hashlib").sha256(body.strip().encode()).hexdigest(),
        },
        **overrides,
    }


def test_admission_passes_complete_user_skill():
    report = scan_skill(_item())
    assert report["status"] == "admitted"
    assert report["can_install"] is True
    assert report["summary"] == {"errors": 0, "warnings": 0}


def test_admission_blocks_duplicate_id_and_high_description_overlap():
    current = _item()
    duplicate = _item(id="custom.other", skill_key="other", body=current["body"])
    duplicate["name"] = "Other Skill"
    duplicate["source_manifest"]["content_sha256"] = __import__("hashlib").sha256(
        duplicate["body"].strip().encode()
    ).hexdigest()
    report = scan_skill(current, existing_items=[current])
    assert report["status"] == "blocked"
    assert any(
        item["code"] in {"skill_id_duplicate", "skill_id_collision"}
        for item in report["issues"]
    )

    overlap = _item(id="custom.overlap", skill_key="overlap", name="Demo Skill")
    overlap["source_manifest"]["content_sha256"] = __import__("hashlib").sha256(
        overlap["body"].strip().encode()
    ).hexdigest()
    report = scan_skill(current, existing_items=[overlap])
    assert any(item["code"] == "skill_name_duplicate" for item in report["issues"])


def test_admission_blocks_missing_external_provenance_and_unsafe_links():
    item = _item()
    item["source_manifest"] = {
        "schema": "skill.provenance.v1",
        "source_kind": "external_source",
        "source_ref": "https://example.invalid/repo",
        "content_sha256": __import__("hashlib").sha256(item["body"].strip().encode()).hexdigest(),
    }
    item["body"] += "\n[run](javascript:alert(1))"
    report = scan_skill(item)
    codes = {issue["code"] for issue in report["issues"]}
    assert "skill_license_missing" in codes
    assert "skill_source_revision_missing" in codes
    assert "skill_unsafe_link_scheme" in codes
    assert report["can_install"] is False


def test_review_required_skill_is_catalog_visible_but_not_installable():
    item = _item()
    item["source_manifest"] = {
        "schema": "skill.provenance.v1",
        "source_kind": "external_source",
        "source_ref": "https://example.invalid/repo",
        "content_sha256": __import__("hashlib").sha256(item["body"].strip().encode()).hexdigest(),
    }
    imported = scan_skill(item, phase="import")
    install = scan_skill(item, phase="install")
    assert imported["status"] == "review_required"
    assert imported["can_install"] is True
    assert install["status"] == "blocked"
    assert install["can_install"] is False


def test_markdown_import_records_admission_and_rejects_short_skill(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "CUSTOM_SKILLS_DIR", tmp_path / "custom")
    monkeypatch.setattr(store, "SKILL_ARCHIVES_DIR", tmp_path / "archives")
    markdown = """---
name: Demo Skill
description: 可执行画布技能
---

## Purpose
完成一个任务。

## Inputs
目标。

## Workflow
1. 读取状态
2. 执行命令

## Outputs
回执。

## Quality
核对结果。
"""
    imported = store.import_skill_file("demo.md", markdown.encode())
    assert imported[0]["admission"]["schema"] == "skill_admission.v1"
    assert imported[0]["admission"]["can_install"] is True
    saved = json.loads((tmp_path / "custom" / "demo-skill.json").read_text())
    assert saved["source_manifest"]["source_ref"] == "demo.md"
    assert saved["admission"]["status"] == "admitted"

    with pytest.raises(store.SkillStoreError, match="导入检查"):
        store.import_skill_file("bad.md", b"---\nname: Too short\ndescription: x\n---\n# x\n")


def test_zip_import_preserves_auxiliary_files_and_install_copies_them(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "CUSTOM_SKILLS_DIR", tmp_path / "custom")
    monkeypatch.setattr(store, "SKILL_ARCHIVES_DIR", tmp_path / "archives")
    runtime_root = tmp_path / "runtime" / ".hermes" / "skills" / "village-canvas"
    monkeypatch.setattr(skill_install, "_skills_root", lambda: runtime_root)
    body = """---
name: Packaged Skill
description: 带辅助资料的可执行技能
---

## Purpose
执行一个带参考资料的任务。

## Inputs
目标。

## Workflow
1. 读取 references/guide.md
2. 执行命令

## Outputs
回执。

## Quality
核对结果。
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pack/SKILL.md", body)
        archive.writestr("pack/references/guide.md", "guide")
    imported = store.import_skill_file("pack.zip", buffer.getvalue())
    item_id = imported[0]["id"]
    result = store.install_store_item(item_id)
    assert result["admission"]["can_install"] is True
    assert (runtime_root / "packaged-skill" / "references" / "guide.md").read_text() == "guide"


def test_install_rolls_back_skill_when_auxiliary_package_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "CUSTOM_SKILLS_DIR", tmp_path / "custom")
    monkeypatch.setattr(store, "SKILL_ARCHIVES_DIR", tmp_path / "archives")
    runtime_root = tmp_path / "runtime" / ".hermes" / "skills" / "village-canvas"
    monkeypatch.setattr(skill_install, "_skills_root", lambda: runtime_root)
    body = """---
name: Atomic Skill
description: 带辅助资料的原子安装技能
---

## Purpose
执行完整安装。

## Inputs
目标。

## Workflow
1. 读取状态
2. 执行命令

## Outputs
回执。

## Quality
核对结果。
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pack/SKILL.md", body)
        archive.writestr("pack/references/guide.md", "guide")
    imported = store.import_skill_file("pack.zip", buffer.getvalue())
    item_id = imported[0]["id"]

    def fail_package(*args, **kwargs):
        raise store.SkillStoreError("模拟辅助文件失败")

    monkeypatch.setattr(store, "_install_package_files", fail_package)
    with pytest.raises(store.SkillStoreError, match="模拟辅助文件失败"):
        store.install_store_item(item_id)
    assert not (runtime_root / "atomic-skill").exists()
    assert not list(runtime_root.parent.glob(".atomic-skill.install-*"))


def test_install_failure_preserves_previous_package(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "CUSTOM_SKILLS_DIR", tmp_path / "custom")
    monkeypatch.setattr(store, "SKILL_ARCHIVES_DIR", tmp_path / "archives")
    runtime_root = tmp_path / "runtime" / ".hermes" / "skills" / "village-canvas"
    monkeypatch.setattr(skill_install, "_skills_root", lambda: runtime_root)
    item = _item()
    store.CUSTOM_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    store._atomic_write_json(store.CUSTOM_SKILLS_DIR / "demo-skill.json", item)
    installed = store.install_store_item(item["id"])
    path = runtime_root / "demo-skill" / "SKILL.md"
    original = path.read_text(encoding="utf-8")

    monkeypatch.setattr(store, "_install_package_files", lambda *args, **kwargs: (_ for _ in ()).throw(store.SkillStoreError("boom")))
    with pytest.raises(store.SkillStoreError, match="boom"):
        store.install_store_item(item["id"])
    assert path.read_text(encoding="utf-8") == original
    assert installed["installed_path"] == str(path)
