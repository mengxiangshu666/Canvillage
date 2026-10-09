from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_SCRIPTS = ROOT / "scripts" / "architecture"
if str(ARCHITECTURE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ARCHITECTURE_SCRIPTS))

from check_import_boundaries import (  # noqa: E402
    ImportEdge,
    build_report,
    collect_findings,
    load_config,
    package_domains,
)


CONFIG_PATH = ARCHITECTURE_SCRIPTS / "domain_boundaries.json"


def test_domain_boundary_config_is_complete_for_current_top_level_modules() -> None:
    config = load_config(CONFIG_PATH)
    domains = package_domains(config)

    assert domains["api"] == "api"
    assert domains["freezone"] == "canvas"
    assert domains["generators"] == "model"
    assert domains["workflow_runtime"] == "workflow"
    assert domains["chat"] == "agent"


def test_report_mode_scans_clean_current_source_without_mutating_it() -> None:
    report = build_report(
        repo_root=ROOT,
        source_root=ROOT / "src" / "novelvideo",
        config_path=CONFIG_PATH,
    )

    assert report["mode"] == "report-only"
    assert report["files_scanned"] >= 400
    assert report["import_edges"] >= 1000
    assert report["finding_count"] == 0
    assert report["unmapped_packages"] == []
    assert report["severity_counts"] == {}


def test_known_reverse_api_edge_is_classified_by_rule() -> None:
    config = load_config(CONFIG_PATH)
    edge = ImportEdge(
        source_file="src/novelvideo/freezone/slots.py",
        source_package="freezone",
        target_package="api",
        target_module="novelvideo.api.deps",
        line=18,
    )

    findings, unmapped = collect_findings([edge], config)

    assert unmapped == []
    assert len(findings) == 1
    assert findings[0].rule_id == "domain-to-api"
    assert findings[0].severity == "high"


def test_workflow_media_dispatch_uses_neutral_project_resource_facade() -> None:
    report = build_report(
        repo_root=ROOT,
        source_root=ROOT / "src" / "novelvideo",
        config_path=CONFIG_PATH,
    )

    reverse_edges = {
        (finding["source_file"], finding["target_module"])
        for finding in report["findings"]
        if finding["rule_id"] == "domain-to-api"
    }
    assert (
        "src/novelvideo/workflow_runtime/media_dispatch.py",
        "novelvideo.api.deps",
    ) not in reverse_edges


def test_canvas_and_agent_facades_do_not_import_api_schemas_or_deps() -> None:
    report = build_report(
        repo_root=ROOT,
        source_root=ROOT / "src" / "novelvideo",
        config_path=CONFIG_PATH,
    )
    reverse_edges = {
        (finding["source_file"], finding["target_module"])
        for finding in report["findings"]
        if finding["rule_id"] == "domain-to-api"
    }
    assert not any(
        source in {"src/novelvideo/freezone/route_helpers.py", "src/novelvideo/freezone/text_node.py"}
        and target in {"novelvideo.api.schemas", "novelvideo.api.deps"}
        for source, target in reverse_edges
    )


def test_project_resource_facade_is_publicly_discoverable() -> None:
    from novelvideo import services

    assert callable(services.make_sqlite_store_for_context)
    assert callable(services.make_cognee_store_for_context)
    assert callable(services.make_static_url_for_context)


def test_cross_domain_exemption_list_is_frozen() -> None:
    """T-216：跨域豁免名单不得被悄悄放宽。

    ``exclude_target_modules`` 能让一条高危/中危越界规则对指定模块直接放行。
    往名单里加一条就能把越界洗成绿，且此前没有任何测试看住它——所以这里把当前
    唯一的豁免集钉死；要改动必须同步改本断言，改动因此无法隐身。
    """

    config = load_config(CONFIG_PATH)
    exempted = {
        rule["id"]: sorted(rule.get("exclude_target_modules", []))
        for rule in config["rules"]
        if rule.get("exclude_target_modules")
    }
    assert exempted == {
        "workflow-to-production-implementation": [
            "novelvideo.production.asset_passport",
            "novelvideo.production.cost_receipt",
            "novelvideo.production.metadata",
            "novelvideo.production.shot_contract",
        ]
    }


def test_string_literal_dynamic_imports_are_visible_to_the_scanner() -> None:
    """T-216 回归：动态导入的字符串形式必须被边界扫描看见。

    2026-10-01 之前扫描器只看 ``ast.Import``/``ast.ImportFrom``，于是
    ``import_module("novelvideo.api.deps")`` 这类写法完全绕过 domain-to-api 规则，
    门禁报 0 命中、测试还把这个 0 当成正确状态（同源断言）。这里用一段最小源码
    直接钉住扫描行为，避免盲区长回来。
    """

    import ast

    from check_import_boundaries import _absolute_imports

    sample = ast.parse(
        "\n".join(
            (
                "from importlib import import_module",
                "import importlib",
                "import novelvideo.freezone.slots",
                "from novelvideo.api.deps import make_store",
                "from novelvideo import production",
                'a = import_module("novelvideo.api.deps")',
                'b = importlib.import_module("novelvideo.api.routes.pipeline")',
                'c = __import__("novelvideo.chat.service")',
                'name = "novelvideo.api.deps"',
                "d = import_module(name)",  # 变量形式：有意不解析
            )
        )
    )
    found = {module for module, _line in _absolute_imports(sample)}
    assert "novelvideo.api.deps" in found
    assert "novelvideo.api.routes.pipeline" in found
    assert "novelvideo.chat.service" in found
    assert "novelvideo.freezone.slots" in found
    assert "novelvideo.production" in found  # from novelvideo import production
    # 变量形式无法静态求值 —— 必须保持「扫不到」，否则会误报。
    assert "name" not in found
    assert "novelvideo" not in found


def test_scan_fails_closed_on_unparsable_source(tmp_path: Path) -> None:
    """语法坏掉的源文件不得被静默跳过（T-216：本门自身原为 fail-open）。"""

    from check_import_boundaries import scan_imports

    source_root = tmp_path / "src" / "novelvideo"
    (source_root / "ok_pkg").mkdir(parents=True)
    (source_root / "ok_pkg" / "__init__.py").write_text("", encoding="utf-8")
    (source_root / "bad.py").write_text("def broken(:\n", encoding="utf-8")

    try:
        scan_imports(source_root, tmp_path)
    except ValueError as exc:
        assert "could not parse" in str(exc)
    else:  # pragma: no cover - 失败路径必须触发
        raise AssertionError("scanner silently skipped an unparsable file")
