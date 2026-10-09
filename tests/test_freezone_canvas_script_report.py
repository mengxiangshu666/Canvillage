"""画布写入路径上的脚本合同报告。

合同检查原先只在生成路径上跑。把分镜表直接写进节点（导入、手改、外部工具生成）会整条
绕过它——实测就有一次：外部生成的 28 行表写进画布，段序与运动稿段数两条 blocking 一直
不合规，节点上却没有任何提示，直到出片之后才由人肉眼看出来。

这一层补的是**读数可见**，不是拦截：报告挂到节点上，保存照常成功。
"""

from __future__ import annotations

from novelvideo.freezone.canvas_store import annotate_script_contract_report
from novelvideo.freezone.script_contract import (
    script_rows_fingerprint,
    validate_script_rows,
)

CARD = "[沈昭昭_现代: 28岁女性，面色苍白]"


def _clean_prompt() -> str:
    return " + ".join(
        [
            "[画面构图：近景，平视机位]",
            f"[角色卡/主体描述：{CARD}]",
            "[主体/人物空间与互动关系：她独坐在办公桌前]",
            "[极具体的微表情：眼下发青，手指微颤]",
            "[明确的场景环境元素：深夜办公室、冷掉的咖啡杯]",
            "[光影几何与大气效果：冷蓝主调]",
            "[视觉风格/质感：都市悬疑写实电影感]",
            "[技术参数：85mm镜头，f/1.8，浅景深]",
        ]
    )


def _motion_prompt() -> str:
    return " + ".join(
        [
            "[明确的摄影机运镜轨迹与速度：镜头前推，极慢速]",
            "[主体极其具体的物理动作细节：她抬眼看屏]",
            "[环境物理动态：纸张被空调风吹起]",
            "[音效与氛围描述：键盘声、室内低频电流声]",
            "[对话台词与语气：无]",
            "[时长：4s]",
        ]
    )


def _row(index: int, **overrides) -> dict:
    row = {
        "shot_no": index,
        "duration": 4,
        "visual_description": "她抬眼看屏",
        "shot": "近景 / 平视",
        "shot_prompt": _clean_prompt(),
        "video_motion_prompt": _motion_prompt(),
    }
    row.update(overrides)
    return row


def _payload(rows: list[dict], *, report: dict | None = None) -> dict:
    data: dict = {"scriptResult": {"title": "测试剧本", "rows": rows}}
    if report is not None:
        data["scriptContractReport"] = report
    return {"nodes": [{"id": "jl-script-main", "type": "scriptNode", "data": data}]}


def test_a_hand_written_script_table_gets_a_report_on_write():
    """直接写进画布的表也要过一遍合同：段序错乱必须被报出来。"""

    broken = _clean_prompt().replace("[画面构图：近景，平视机位]", "近景，平视机位")
    payload = _payload([_row(index + 1, shot_prompt=broken) for index in range(10)])

    annotate_script_contract_report(payload)

    report = payload["nodes"][0]["data"]["scriptContractReport"]
    assert report["schema"] == "freezone.script-contract.v1"
    assert any(
        issue["rule_id"] == "script.shot_prompt.order.v1"
        for issue in report["issues"]
    )


def test_the_report_carries_viewability_metrics():
    """可看性指标要随报告一起落到节点上，前端才有东西可显示。"""

    rows = [
        _row(index + 1, dialogue="你终于来了。", shot="中景 / 平视") for index in range(10)
    ]
    payload = _payload(rows)

    annotate_script_contract_report(payload)

    metrics = payload["nodes"][0]["data"]["scriptContractReport"]["metrics"]
    assert metrics["shot_count"] == 10
    assert metrics["dialogue_shot_count"] == 10
    assert metrics["dialogue_shot_share"] == 1.0


def test_an_existing_report_is_preserved_when_rows_have_not_changed():
    """生成路径的报告里带着「修了什么」；行内容没变时必须原样保留。"""

    rows = [_row(index + 1) for index in range(10)]
    existing = validate_script_rows(rows).as_dict()
    payload = _payload(rows, report=existing)

    annotate_script_contract_report(payload)

    assert payload["nodes"][0]["data"]["scriptContractReport"] == existing


def test_an_existing_report_is_recomputed_when_the_rows_change():
    """报告不是永久缓存：行内容变了，旧读数必须被当前内容重新计算。"""

    rows = [_row(index + 1) for index in range(10)]
    existing = validate_script_rows(rows).as_dict()
    changed = [dict(row) for row in rows]
    changed[0]["dialogue"] = "新增的一句台词。"
    payload = _payload(changed, report=existing)

    annotate_script_contract_report(payload)

    report = payload["nodes"][0]["data"]["scriptContractReport"]
    assert report["rows_fingerprint"] == script_rows_fingerprint(changed)
    assert report["rows_fingerprint"] != existing["rows_fingerprint"]
    assert report["metrics"]["dialogue_shot_count"] == 1


def test_row_fingerprint_ignores_key_order_but_tracks_content_changes():
    rows = [_row(1), _row(2)]
    reordered = [{key: row[key] for key in reversed(list(row))} for row in rows]
    changed = [dict(rows[0], shot="全景 / 平视"), rows[1]]

    assert script_rows_fingerprint(rows) == script_rows_fingerprint(reordered)
    assert script_rows_fingerprint(rows) != script_rows_fingerprint(changed)


def test_nodes_without_a_usable_table_are_left_alone():
    """没有表、表是空的、或压根不是脚本节点，都不该被写进任何字段。"""

    payload = {
        "nodes": [
            {"id": "a", "type": "scriptNode", "data": {"scriptResult": {"rows": []}}},
            {"id": "b", "type": "scriptNode", "data": {}},
            {"id": "c", "type": "videoNode", "data": {"scriptResult": {"rows": [_row(1)]}}},
            {"id": "d", "type": "scriptNode"},
        ]
    }

    annotate_script_contract_report(payload)

    for node in payload["nodes"]:
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        assert "scriptContractReport" not in data


def test_a_payload_without_nodes_is_ignored():
    annotate_script_contract_report({})
    annotate_script_contract_report({"nodes": "not-a-list"})
