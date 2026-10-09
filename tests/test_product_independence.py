from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import sys


PRODUCT_IDENTITY_SCRIPT = Path("scripts/check_product_identity.py").resolve()
PRODUCT_IDENTITY_SPEC = spec_from_file_location("product_identity", PRODUCT_IDENTITY_SCRIPT)
if PRODUCT_IDENTITY_SPEC is None or PRODUCT_IDENTITY_SPEC.loader is None:
    raise RuntimeError(f"Unable to load {PRODUCT_IDENTITY_SCRIPT}")
product_identity = module_from_spec(PRODUCT_IDENTITY_SPEC)
PRODUCT_IDENTITY_SPEC.loader.exec_module(product_identity)


RETIRED_VENDOR = "dramaclaw"  # identity-allow: negative-sample constant for the gate itself
UPSTREAM_PRODUCT = "supertale"  # identity-allow: negative-sample constant for the gate itself
FORBIDDEN_VENDOR_HOSTS = (
    f"github.com/{RETIRED_VENDOR}",
    f"api.github.com/repos/{RETIRED_VENDOR}",
    f"{RETIRED_VENDOR}-dl.cdnfg.com",
    f"nfg-web-assets.cdnfg.com/{RETIRED_VENDOR}",
    f"{RETIRED_VENDOR}.ai",
    "relayclaw.cdnfg.com",  # identity-allow: negative-sample host for the gate itself
)


def test_runtime_sources_do_not_reference_retired_vendor_hosts() -> None:
    roots = (
        Path("src/novelvideo"),
        Path("frontend/src"),
        Path("frontend/public/locales"),
        Path("frontend/docker"),
        Path("agent_skills"),
        Path("src/novelvideo/agent_tools"),
        Path("scripts"),
        Path(".github/workflows"),
    )
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".json", ".template"}:
                continue
            body = path.read_text(encoding="utf-8", errors="ignore").lower()
            for host in FORBIDDEN_VENDOR_HOSTS:
                if host in body:
                    offenders.append(f"{path}: {host}")

    index_body = Path("frontend/index.html").read_text(encoding="utf-8").lower()
    for host in FORBIDDEN_VENDOR_HOSTS:
        if host in index_body:
            offenders.append(f"frontend/index.html: {host}")

    assert offenders == []


def test_product_identity_gate_passes_for_repository_files() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/check_product_identity.py"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr


def test_product_identity_gate_limits_upstream_compatibility_scope() -> None:
    assert product_identity.is_compatibility_path(
        "src/novelvideo/chat/identity_compat.py"
    )
    assert product_identity.is_compatibility_path("scripts/migrate_identity_data.py")
    assert product_identity.is_compatibility_path("tests/test_identity_compat.py")
    assert not product_identity.is_compatibility_path(
        f"agent_skills/{RETIRED_VENDOR}/SKILL.md"
    )
    assert not product_identity.is_compatibility_path(
        "frontend/src/features/village-workflow/superchat-panel.tsx"
    )
    assert not product_identity.is_compatibility_path(
        "frontend/src/features/freezone/retired-product.ts"
    )


def test_product_identity_gate_rejects_machine_specific_runtime_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime_file = tmp_path / "src" / "novelvideo" / "portable_probe.py"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text(
        'MODEL_HOME = "C:/Users/example/Desktop/another-project"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(product_identity, "ROOT", tmp_path)
    monkeypatch.setattr(product_identity, "SELF", tmp_path / "identity_gate.py")
    monkeypatch.setattr(product_identity, "repository_files", lambda: [runtime_file])
    monkeypatch.setattr(product_identity, "staged_repository_files", lambda: [])

    assert product_identity.main() == 1


def test_identity_gate_includes_staged_paths_even_when_worktree_file_is_deleted(
    monkeypatch,
) -> None:
    retired_path = Path("staged-") / f"{RETIRED_VENDOR}.md"
    monkeypatch.setattr(product_identity, "repository_files", lambda: [])
    monkeypatch.setattr(
        product_identity,
        "staged_repository_files",
        lambda: [product_identity.ROOT / retired_path],
    )

    assert product_identity.main() == 1


def _isolate_gate(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(product_identity, "ROOT", root)
    monkeypatch.setattr(product_identity, "SELF", root / "identity_gate.py")
    monkeypatch.setattr(product_identity, "repository_files", lambda: [])
    monkeypatch.setattr(product_identity, "staged_repository_files", lambda: [])


def test_identity_gate_reads_deployed_backend_mirror(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """`runtime/env/novelvideo` mirrors `src/novelvideo` but is gitignored.

    A stale copy left behind by an interrupted deploy is invisible to
    `git ls-files`, so the gate has to read it off disk.
    """

    deployed = tmp_path / "runtime" / "env" / "novelvideo" / "freezone"
    deployed.mkdir(parents=True)
    (deployed / "slots.py").write_text(
        f'"""Canonical {RETIRED_VENDOR} asset slots."""\n',
        encoding="utf-8",
    )
    _isolate_gate(monkeypatch, tmp_path)

    assert product_identity.main() == 1


def test_identity_gate_reads_deployed_agent_skills_mirror(
    monkeypatch, tmp_path: Path
) -> None:
    deployed = tmp_path / "runtime" / "agent_skills" / "village-canvas"
    deployed.mkdir(parents=True)
    (deployed / "SKILL.md").write_text(
        f"# 旧称 {RETIRED_VENDOR} 不得出现在自然语言回复里\n",
        encoding="utf-8",
    )
    _isolate_gate(monkeypatch, tmp_path)

    assert product_identity.main() == 1


def test_identity_gate_ignores_vendored_runtime_payload(monkeypatch, tmp_path: Path) -> None:
    """Vendored site-packages keep their own upstream provenance.

    Rewriting PyTorch's LICENSE or pip's build URL is not our call, so the walk
    must stop at the owned subtrees instead of policing third-party payload.
    """

    vendored = tmp_path / "runtime" / "env" / "site-packages" / "upstream_thing"
    vendored.mkdir(parents=True)
    (vendored / "notes.txt").write_text(f"built on {RETIRED_VENDOR}\n", encoding="utf-8")
    own = tmp_path / "runtime" / "env" / "novelvideo"
    own.mkdir(parents=True)
    (own / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
    _isolate_gate(monkeypatch, tmp_path)

    assert product_identity.main() == 0


def test_identity_gate_reads_deployed_distribution_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    metadata = (
        tmp_path
        / "runtime"
        / "env"
        / "supertale_ce-9.9.9.dist-info"  # identity-allow: regression fixture path
        / "METADATA"
    )
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "Name: supertale-ce\n"  # identity-allow: licence attribution name
        "Summary: packaged product\n"
        "Project-URL: legacy, https://relayclaw.cdnfg.com\n",  # identity-allow: negative sample
        encoding="utf-8",
    )
    _isolate_gate(monkeypatch, tmp_path)

    assert product_identity.main() == 1


def test_identity_gate_allows_distribution_attribution_name(
    monkeypatch,
    tmp_path: Path,
) -> None:
    metadata = (
        tmp_path
        / "runtime"
        / "env"
        / "supertale_ce-9.9.9.dist-info"  # identity-allow: regression fixture path
        / "METADATA"
    )
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "Name: supertale-ce\n"  # identity-allow: licence attribution name
        "Summary: Village Infinite Canvas\n",
        encoding="utf-8",
    )
    _isolate_gate(monkeypatch, tmp_path)

    assert product_identity.main() == 0


def test_identity_gate_stays_offline_when_runtime_is_absent(monkeypatch, tmp_path: Path) -> None:
    """CI checks out no `runtime/` at all; the deployment walk must be a no-op."""

    _isolate_gate(monkeypatch, tmp_path)

    assert product_identity.deployment_files() == []
    assert product_identity.main() == 0


def test_deployed_mirrors_inherit_their_source_compatibility_scope() -> None:
    assert product_identity.mirrored_source_path(
        "runtime/env/novelvideo/chat/identity_compat.py"
    ) == "src/novelvideo/chat/identity_compat.py"
    assert product_identity.mirrored_source_path(
        "runtime/agent_skills/village-canvas/SKILL.md"
    ) == "agent_skills/village-canvas/SKILL.md"
    assert not product_identity.is_compatibility_path(
        product_identity.mirrored_source_path(
            f"runtime/env/novelvideo/freezone/{RETIRED_VENDOR}_compat.py"
        )
    )


def test_deployment_scan_trees_are_disjoint_from_vendored_payload() -> None:
    for tree in product_identity.DEPLOYMENT_TREES:
        for marker in product_identity.DEPLOYED_VENDOR_MARKERS:
            assert not tree.startswith(marker), (tree, marker)
            assert not marker.startswith(tree), (tree, marker)


def test_product_identity_gate_rejects_the_upstream_distribution_name(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The retired upstream name is as forbidden as the Chinese legacy brands."""

    planted = tmp_path / "frontend" / "src" / "leftover-label.ts"
    planted.parent.mkdir(parents=True)
    planted.write_text(
        f'export const LABEL = "{UPSTREAM_PRODUCT}";\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(product_identity, "ROOT", tmp_path)
    monkeypatch.setattr(product_identity, "SELF", tmp_path / "identity_gate.py")
    monkeypatch.setattr(product_identity, "repository_files", lambda: [planted])
    monkeypatch.setattr(product_identity, "staged_repository_files", lambda: [])

    assert product_identity.main() == 1


def test_product_identity_gate_accepts_a_line_level_allow_marker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    marked = tmp_path / "frontend" / "src" / "compat.ts"
    marked.parent.mkdir(parents=True)
    marked.write_text(
        f'export const LEGACY = "{UPSTREAM_PRODUCT}-app";  // identity-allow: pre-rename key\n',
        encoding="utf-8",
    )
    unmarked = tmp_path / "frontend" / "src" / "other.ts"
    unmarked.write_text("export const VALUE = 1;\n", encoding="utf-8")
    monkeypatch.setattr(product_identity, "ROOT", tmp_path)
    monkeypatch.setattr(product_identity, "SELF", tmp_path / "identity_gate.py")
    monkeypatch.setattr(
        product_identity, "repository_files", lambda: [marked, unmarked]
    )
    monkeypatch.setattr(product_identity, "staged_repository_files", lambda: [])

    assert product_identity.main() == 0


GENERATION_ONE_SAMPLE = "BuilderGPT"  # identity-allow: negative-sample constant for the gate itself
GENERATION_THREE_SAMPLES = (
    "XiaHua",  # identity-allow: negative-sample constant for the gate itself
    "XiaDao",  # identity-allow: negative-sample constant for the gate itself
    "XiaJi",  # identity-allow: negative-sample constant for the gate itself
    "XiaJing",  # identity-allow: negative-sample constant for the gate itself
    "XiaLiao",  # identity-allow: negative-sample constant for the gate itself
    "XiaTang",  # identity-allow: negative-sample constant for the gate itself
    "XiaGe",  # identity-allow: negative-sample constant for the gate itself
    "Xia Director",  # identity-allow: negative-sample constant for the gate itself
    "Xia Style",  # identity-allow: negative-sample constant for the gate itself
)


def _gate_over(monkeypatch, tmp_path: Path, name: str, body: str) -> int:
    planted = tmp_path / "frontend" / "src" / name
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(body, encoding="utf-8")
    monkeypatch.setattr(product_identity, "ROOT", tmp_path)
    monkeypatch.setattr(product_identity, "SELF", tmp_path / "identity_gate.py")
    monkeypatch.setattr(product_identity, "repository_files", lambda: [planted])
    monkeypatch.setattr(product_identity, "staged_repository_files", lambda: [])
    return product_identity.main()


def test_product_identity_gate_rejects_the_first_generation_scene_editor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    assert _gate_over(
        monkeypatch,
        tmp_path,
        "stage.ts",
        f'export const LABEL = "{GENERATION_ONE_SAMPLE} director stage";\n',
    ) == 1


def test_product_identity_gate_rejects_every_romanised_generation_three_name(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """One sampled file per retired romanisation, so no branch is dead weight."""

    for index, sample in enumerate(GENERATION_THREE_SAMPLES):
        assert _gate_over(
            monkeypatch,
            tmp_path,
            f"gen3_{index}.ts",
            f'export const LABEL = "{sample}";\n',
        ) == 1, sample


def test_product_identity_gate_leaves_current_identifier_families_alone(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The `xia`-prefix is deliberately not a blanket match.

    `xiaoshu` (小树 / the assistant), the Azure TTS voice ids and `xianxia` (仙侠,
    a genre word) all start with the same four letters as the retired generation-3
    romanisations. A coarse `xia` match would red the whole repository, so this
    test pins the alternation shut.
    """

    body = "\n".join(
        (
            'export const ASSISTANT = "xiaoshu";',
            'export const VOICE = "zh-CN-XiaoxiaoNeural";',
            'export const STYLE = "3D Xianxia Guoman";',
            'export const ROUTE = "xaio";',
            "",
        )
    )
    assert _gate_over(monkeypatch, tmp_path, "kept.ts", body) == 0


def test_attribution_and_audit_scope_stays_narrow() -> None:
    """File-scoped exemptions exist because those artifacts have no line syntax.

    NOTICE / sbom / uv.lock are JSON, CSV, or plain text, so they cannot carry a
    `# identity-allow` comment. The exemption has to stay on exactly those files
    or it becomes a hole the rest of the tree can hide behind.
    """

    for exempt in (
        "NOTICE",
        "frontend/NOTICE",
        "frontend/THIRD-PARTY-LICENSES.txt",
        "uv.lock",
        "sbom.spdx.json",
        "license-inventory.csv",
        "scripts/lint_ce_imports.py",
        "MODIFICATIONS.md",
    ):
        assert product_identity.is_compatibility_path(exempt), exempt

    for ordinary in (
        "src/novelvideo/storage/__init__.py",
        "src/novelvideo/freezone/slots.py",
        "frontend/src/features/freezone/jobs.ts",
        "tests/test_something_new.py",
    ):
        assert not product_identity.is_compatibility_path(ordinary), ordinary
