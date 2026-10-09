from __future__ import annotations

import csv
import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from scripts.prepare_public_source import export_source


def test_export_keeps_product_tests_and_not_private_history_or_data(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    fixtures = {
        ".gitattributes": "docs/ai/** export-ignore\n.env export-ignore\n*.db export-ignore\n",
        ".gitleaks.toml": '[extend]\nuseDefault = true\n[allowlist]\nstopwords = ["fake-marker"]\ncommits = [\n"fake-commit",\n]\n',
        "src/product.py": "VALUE = 1\n",
        "tests/test_product.py": "assert 1 == 1\n",
        "docs/ai/private.md": "Private work record\n",
        ".env": "PRIVATE_VALUE=fake-value\n",
        "private.db": "Not a real database\n",
        "license-inventory.csv": "path,license_expression\nsrc/product.py,Elastic-2.0\ndocs/ai/private.md,Elastic-2.0\n",
        "sbom.spdx.json": json.dumps({
            "files": [
                {"SPDXID": "SPDXRef-product", "fileName": "./src/product.py"},
                {"SPDXID": "SPDXRef-private", "fileName": "docs/ai/private.md"},
            ],
            "relationships": [
                {"spdxElementId": "SPDXRef-package", "relatedSpdxElement": "SPDXRef-product"},
                {"spdxElementId": "SPDXRef-package", "relatedSpdxElement": "SPDXRef-private"},
            ],
        }),
    }
    for name, data in fixtures.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
    subprocess.run(["git", "add", "--all"], cwd=tmp_path, check=True)
    private_bytes = (tmp_path / ".env").read_bytes()
    destination = tmp_path / "workspace" / "artifacts" / "public-source"
    hashes = export_source(tmp_path, destination)

    assert (destination / "src/product.py").read_bytes() == (tmp_path / "src/product.py").read_bytes()
    assert "tests/test_product.py" in hashes
    assert not any(name in hashes for name in (".env", "private.db", "docs/ai/private.md"))
    assert not (destination / ".git").exists()
    assert (tmp_path / ".env").read_bytes() == private_bytes
    config = tomllib.loads((destination / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert config["allowlist"] == {"stopwords": ["fake-marker"]}
    with (destination / "license-inventory.csv").open(encoding="utf-8", newline="") as handle:
        assert [row["path"] for row in csv.DictReader(handle)] == ["src/product.py"]
    sbom = json.loads((destination / "sbom.spdx.json").read_text(encoding="utf-8"))
    assert len(sbom["files"]) == len(sbom["relationships"]) == 1
    with pytest.raises(FileExistsError):
        export_source(tmp_path, destination)
    with pytest.raises(ValueError):
        export_source(tmp_path, tmp_path / "outside-artifacts")


def test_repository_export_attributes_keep_product_and_license_files() -> None:
    from scripts.prepare_public_source import ROOT, public_files

    paths = set(public_files(ROOT))
    assert {"src/novelvideo/cli.py", "frontend/package.json", "LICENSES/Elastic-2.0.txt", "NOTICE", "tests/test_shared_context.py"} <= paths
    assert not any(name.startswith(("docs/ai/", "docs/research/", "docs/status/")) for name in paths)
    assert {name for name in paths if name.startswith(".github/")} == {".github/workflows/quality-source.yml"}
    assert "docs/PUBLIC_SOURCE_RELEASE.md" in paths
    assert "AGENTS.md" not in paths
    assert not any(name.startswith(("frontend/public/expression-head/libtv-mood-proxy/", "assets/partners/")) for name in paths)
    assert "src/novelvideo/assets/login_bgm.mp3" not in paths
    assert "frontend/public/login-cinematic/fonts/DingTalk_JinBuTi_Title.woff2" not in paths
    assert {
        "frontend/public/expression-head/ict-facekit-head.glb",
        "frontend/public/video/camera-presets/fixed.mp4",
        "frontend/public/images/camera/arri-alexa-35.png",
        "src/novelvideo/assets/login_bg_v1.mp4",
    } <= paths
    css = (ROOT / "frontend/src/components/login/cinematic/hero-layout.module.css").read_text(encoding="utf-8")
    assert "DingTalk" not in css


def test_cli_archive_keeps_git_executable_bits(tmp_path: Path, monkeypatch) -> None:
    from scripts import prepare_public_source

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "run.sh"], cwd=tmp_path, check=True)
    subprocess.run(["git", "update-index", "--chmod=+x", "run.sh"], cwd=tmp_path, check=True)
    destination = tmp_path / "workspace" / "artifacts" / "public-source"
    monkeypatch.setattr(prepare_public_source, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["prepare_public_source.py", "--output", str(destination)])
    prepare_public_source.main()
    with zipfile.ZipFile(str(destination) + ".zip") as archive:
        assert archive.namelist() == ["run.sh"]
        assert archive.getinfo("run.sh").external_attr >> 16 == 0o100755
        assert archive.read("run.sh") == (tmp_path / "run.sh").read_bytes()
