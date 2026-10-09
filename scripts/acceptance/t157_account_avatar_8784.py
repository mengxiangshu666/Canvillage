"""T-157 真机实证：账号头像后端（`/api/v1/account/avatar` + `/static/avatars`）。

验的是**已部署的 8784**：

1. 先备份现场（如果账号本来就有头像，连字节一起取回，收尾原样还原）；
2. `POST` 一张真 PNG → 200 且拿到 `/static/avatars/...?v=` URL；
3. 直接用该 URL 取图 → 200 / `image/png` / 字节与上传内容一致；
4. `GET /api/v1/account/avatar` 返回同一个 URL；
5. 非图片字节 → 400；超大文件 → 400；
6. 别人的头像路径 → 404（不确认存在性）；
7. `DELETE` → 清空，旧 URL 变 404，`GET` 回到 `null`；
8. 还原现场并回读确认。

跑法：

    .venv\\Scripts\\python.exe scripts\\acceptance\\t157_account_avatar_8784.py

零付费：不上传任何用户素材、不建项目、不调 provider、不碰其他账号。
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image


BASE_URL = os.environ.get("T157_BASE_URL") or "http://127.0.0.1:8784"
ENDPOINT = f"{BASE_URL}/api/v1/account/avatar"
REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "workspace" / "artifacts" / "t157"
SUMMARY = ARTIFACT_DIR / "account-avatar-8784.json"
# Just over the 4 MB avatar cap but under the 5 MB request-body limit the API
# middleware applies, so this proves the route's own 400 wins that race.
OVERSIZE_BYTES = 4 * 1024 * 1024 + 64 * 1024


def _png(size: tuple[int, int] = (96, 96)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (28, 96, 168)).save(buffer, format="PNG")
    return buffer.getvalue()


def _multipart(payload: bytes, filename: str, content_type: str) -> tuple[bytes, str]:
    boundary = "----t157avatar"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + payload + tail, f"multipart/form-data; boundary={boundary}"


def _call(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes, str]:
    headers = {"Accept": "*/*"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read(), response.headers.get(
                "Content-Type", ""
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def _json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _upload(payload: bytes, filename: str, content_type: str) -> tuple[int, Any]:
    body, multipart_type = _multipart(payload, filename, content_type)
    status, raw, _ = _call(
        "POST", ENDPOINT, data=body, content_type=multipart_type
    )
    return status, _json(raw)


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"base_url": BASE_URL, "checks": []}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    uploaded_bytes = _png()

    # --- 现场备份 -------------------------------------------------------- #
    status, raw, _ = _call("GET", ENDPOINT)
    before = _json(raw) or {}
    before_url = ((before.get("data") or {}).get("avatar_url")) if status == 200 else None
    before_blob: bytes | None = None
    before_type = ""
    if before_url:
        blob_status, blob, blob_type = _call("GET", f"{BASE_URL}{before_url}")
        if blob_status == 200:
            before_blob, before_type = blob, blob_type or "image/png"
    check(
        "read_endpoint_answers",
        status == 200 and isinstance(before.get("data"), dict),
        {"status": status, "body": before},
    )
    report["pre_existing_avatar"] = bool(before_url)

    # --- 上传 ------------------------------------------------------------ #
    status, body = _upload(uploaded_bytes, "smoke.png", "image/png")
    url = ((body or {}).get("data") or {}).get("avatar_url") if status == 200 else None
    check(
        "upload_returns_a_versioned_static_url",
        status == 200 and isinstance(url, str) and url.startswith("/static/avatars/")
        and "?v=" in url,
        {"status": status, "url": url, "body": body},
    )
    if not url:
        _write(report)
        return 1
    _, account_segment, filename = url.split("?")[0].rsplit("/", 2)

    # --- 伺服 ------------------------------------------------------------ #
    served_status, served, served_type = _call("GET", f"{BASE_URL}{url}")
    check(
        "uploaded_bytes_are_served_verbatim",
        served_status == 200 and served == uploaded_bytes,
        {"status": served_status, "content_type": served_type, "bytes": len(served)},
    )
    check(
        "served_as_a_png",
        served_type.split(";")[0].strip() == "image/png",
        served_type,
    )

    status, reread_raw, _ = _call("GET", ENDPOINT)
    check(
        "read_endpoint_reports_the_stored_avatar",
        status == 200
        and ((_json(reread_raw) or {}).get("data") or {}).get("avatar_url") == url,
        {"status": status, "body": _json(reread_raw)},
    )

    # --- 拒绝 ------------------------------------------------------------ #
    status, rejected = _upload(b"this is not an image", "fake.png", "image/png")
    check(
        "non_image_payload_is_rejected",
        status == 400,
        {"status": status, "body": rejected},
    )

    status, oversized = _upload(b"\x89PNG\r\n\x1a\n" + b"0" * OVERSIZE_BYTES, "big.png", "image/png")
    check(
        "oversized_upload_is_rejected",
        status == 400,
        {"status": status, "body": oversized},
    )

    status, _raw, _ctype = _call(
        "GET", f"{BASE_URL}/static/avatars/definitely-not-this-account/avatar.png"
    )
    check("other_accounts_avatar_is_not_served", status == 404, {"status": status})

    # 失败的上传不能把已经存好的头像弄丢。
    status, _raw, _ctype = _call("GET", f"{BASE_URL}{url}")
    check(
        "rejected_uploads_leave_the_stored_avatar_intact",
        status == 200,
        {"status": status},
    )

    # --- 清除 ------------------------------------------------------------ #
    status, cleared_raw, _ = _call("DELETE", ENDPOINT)
    cleared_body = _json(cleared_raw) or {}
    check(
        "delete_clears_the_avatar",
        status == 200
        and (cleared_body.get("data") or {}).get("avatar_url") is None,
        {"status": status, "body": cleared_body},
    )

    status, _raw, _ctype = _call(
        "GET", f"{BASE_URL}/static/avatars/{account_segment}/{filename.split('?')[0]}"
    )
    check("cleared_avatar_stops_being_served", status == 404, {"status": status})

    status, after_raw, _ = _call("GET", ENDPOINT)
    check(
        "read_endpoint_reports_no_avatar_after_delete",
        status == 200
        and ((_json(after_raw) or {}).get("data") or {}).get("avatar_url") is None,
        {"status": status, "body": _json(after_raw)},
    )

    # --- 还原现场 -------------------------------------------------------- #
    if before_blob is not None:
        status, _body = _upload(before_blob, "restore.png", before_type)
        check("pre_existing_avatar_restored", status == 200, {"status": status})
        status, restored_raw, _ = _call("GET", ENDPOINT)
        check(
            "restored_avatar_matches_the_original",
            status == 200
            and ((_json(restored_raw) or {}).get("data") or {}).get("avatar_url")
            == before_url,
            {"status": status, "expected": before_url, "body": _json(restored_raw)},
        )

    _write(report)
    return 0 if report["passed"] == report["total"] else 1


def _write(report: dict[str, Any]) -> None:
    report["passed"] = sum(1 for item in report["checks"] if item["ok"])
    report["total"] = len(report["checks"])
    SUMMARY.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in report["checks"]:
        print(f"{'OK ' if item['ok'] else 'FAIL'} {item['name']}: {item['detail']}")
    print(f"{report['passed']}/{report['total']} checks ok -> {SUMMARY}")


if __name__ == "__main__":
    sys.exit(main())
