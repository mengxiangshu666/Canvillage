"""视频产物与上游任务的对应关系。

上游任务已经跑完、钱也花了，本地成片却可能还是上一次的老文件：同一个 Beat
重新生成时输出路径永远不变，「文件存在」并不等于「这次的新成片已经落盘」。
所以判断依据必须是显式标记，而不是 ``Path(output_path).exists()``。
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "result_provider_task_marker_path",
    "read_result_provider_task",
    "write_result_provider_task",
    "clear_result_provider_task",
    "result_belongs_to_provider_task",
]

_MARKER_SUFFIX = ".provider-task.json"


def result_provider_task_marker_path(output_path: str | Path) -> Path:
    """成片旁边的来源标记文件路径。"""

    path = Path(output_path)
    return path.with_name(f"{path.name}{_MARKER_SUFFIX}")


def read_result_provider_task(output_path: str | Path) -> str:
    """读回「这个成片是哪个上游任务写下来的」，读不到就返回空串。"""

    marker = result_provider_task_marker_path(output_path)
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("provider_task_id") or "").strip()


def write_result_provider_task(
    output_path: str | Path,
    provider_task_id: str,
    *,
    protocol: str = "",
    downloaded_at: str = "",
) -> None:
    """下载成功后立刻记账；写不进去不影响成片本身。"""

    task_id = str(provider_task_id or "").strip()
    if not task_id:
        return
    marker = result_provider_task_marker_path(output_path)
    payload = {
        "schema": "video_result_provenance.v1",
        "provider_task_id": task_id,
        "protocol": str(protocol or ""),
        "downloaded_at": str(downloaded_at or ""),
    }
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_name(f"{marker.name}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(marker)
    except OSError:
        return


def clear_result_provider_task(output_path: str | Path) -> None:
    """成片被其它流程改写后，旧标记必须作废。"""

    marker = result_provider_task_marker_path(output_path)
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        return


def result_belongs_to_provider_task(
    output_path: str | Path,
    provider_task_id: str,
) -> bool:
    """本地这个非空成片，是不是指定上游任务写下来的。"""

    task_id = str(provider_task_id or "").strip()
    if not task_id:
        return False
    path = Path(output_path)
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    return read_result_provider_task(path) == task_id
