"""Standalone ASGI app for the 2.0 REST API."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from novelvideo.api import OPENAPI_TAGS, api_router, register_verification_routes
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.account import account_avatar_file_response
from novelvideo.api.routes.files import preview_project_media_file
from novelvideo.product_identity import PRODUCT_NAME
from novelvideo.shared.billing_errors import (
    BILLING_RULE_NOT_CONFIGURED_MESSAGE,
    INSUFFICIENT_CREDITS_MESSAGE,
    BillingRuleNotConfiguredError,
    InsufficientCreditsError,
    billing_rule_not_configured_payload,
    insufficient_credits_payload,
)
from novelvideo.shared.api_coverage import mount_api_coverage_middleware
from novelvideo.task_backend.limits import (
    GlobalLaneQueueLimitExceeded,
    ProjectTaskLimitExceeded,
    ProjectUserTaskLimitExceeded,
)

logger = logging.getLogger("novelvideo.api.app")

# Per durability plan §N: reject oversized request bodies before they
# reach a handler. 5 MB covers the largest legitimate freezone canvas
# (50k nodes × ~80 bytes JSON each) with comfortable headroom; anything
# bigger is almost certainly a runaway client / DoS attempt.
MAX_REQUEST_BODY_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_REQUEST_BODY_BYTES = 200 * 1024 * 1024
_RESOURCE_REQUEST_COUNTS: Counter[str] = Counter()
_RESOURCE_REQUEST_TOTAL = 0
_RESOURCE_REQUEST_LOCK = threading.Lock()


def _resolve_frontend_dist() -> Path | None:
    """Resolve the SPA build for portable and direct Python launches.

    The desktop launcher normally supplies ``VILLAGE_CANVAS_FRONTEND_DIST``.
    Direct launches (for example from an IDE or a maintenance shell) do not
    inherit that variable, but they still run beside the project's frontend
    build.  Falling back to that deterministic sibling keeps the API and SPA
    from appearing healthy as two unrelated services.
    """
    configured = str(os.environ.get("VILLAGE_CANVAS_FRONTEND_DIST") or "").strip()
    candidates: list[Path] = []
    if configured and "%" not in configured:
        candidates.append(Path(configured))
    project_root = Path(__file__).resolve().parents[3]
    candidates.append(project_root / "frontend" / "dist")
    for candidate in candidates:
        try:
            if candidate.is_dir() and (candidate / "index.html").is_file():
                return candidate
        except OSError:
            continue
    return None


def _request_body_limit(request: Request) -> int:
    content_type = request.headers.get("content-type", "").lower()
    if (
        request.url.path.startswith("/api/v1/projects/")
        and request.url.path.endswith("/upload")
        and "multipart/form-data" in content_type
    ):
        return MAX_UPLOAD_REQUEST_BODY_BYTES
    return MAX_REQUEST_BODY_BYTES


def _is_freezone_audio_voice_upload(request: Request) -> bool:
    return (
        request.method.upper() == "POST"
        and request.url.path.startswith("/api/v1/projects/")
        and request.url.path.endswith("/freezone/audio/voices")
    )


def _resource_request_key(path: str) -> str | None:
    if path.startswith("/static/"):
        return path

    if not path.startswith("/api/v1/projects/"):
        return None

    parts = path.split("/")
    if len(parts) >= 6 and parts[4] in {"media", "files"}:
        return path
    return None


def _record_resource_request(resource_key: str) -> tuple[int, int]:
    global _RESOURCE_REQUEST_TOTAL
    with _RESOURCE_REQUEST_LOCK:
        _RESOURCE_REQUEST_TOTAL += 1
        _RESOURCE_REQUEST_COUNTS[resource_key] += 1
        return _RESOURCE_REQUEST_TOTAL, _RESOURCE_REQUEST_COUNTS[resource_key]


def create_app() -> FastAPI:
    register_verification_routes()

    application = FastAPI(title=f"{PRODUCT_NAME} API", openapi_tags=OPENAPI_TAGS)
    mount_api_coverage_middleware(application)

    @application.exception_handler(ProjectTaskLimitExceeded)
    async def _project_task_limit_exceeded(
        request: Request,
        exc: ProjectTaskLimitExceeded,
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": f"当前项目 {exc.queue_kind} 队列任务已满，请等待已有任务完成后再提交",
                "data": {
                    "project_id": exc.project_id,
                    "queue_kind": exc.queue_kind,
                    "limit": exc.limit,
                    "active": exc.active,
                    "limit_scope": "project",
                },
            },
        )

    @application.exception_handler(ProjectUserTaskLimitExceeded)
    async def _project_user_task_limit_exceeded(
        request: Request,
        exc: ProjectUserTaskLimitExceeded,
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": (
                    f"你在当前项目 {exc.queue_kind} 队列任务已满，"
                    "请等待自己的任务完成后再提交"
                ),
                "data": {
                    "project_id": exc.project_id,
                    "requester_user_id": exc.requester_user_id,
                    "queue_kind": exc.queue_kind,
                    "limit": exc.limit,
                    "active": exc.active,
                    "limit_scope": "user",
                },
            },
        )

    @application.exception_handler(GlobalLaneQueueLimitExceeded)
    async def _global_lane_queue_limit_exceeded(
        request: Request,
        exc: GlobalLaneQueueLimitExceeded,
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": f"当前节点 {exc.queue_kind} 队列已满，请稍后再提交",
                "data": {
                    "project_id": exc.project_id,
                    "queue_kind": exc.queue_kind,
                    "limit": exc.limit,
                    "queued": exc.queued,
                    "limit_scope": "global_lane_queue",
                },
            },
        )

    @application.exception_handler(InsufficientCreditsError)
    async def _insufficient_credits(
        request: Request,
        exc: InsufficientCreditsError,
    ) -> JSONResponse:
        _ = request
        payload = insufficient_credits_payload(exc)
        return JSONResponse(
            status_code=402,
            content={
                "ok": False,
                "error": INSUFFICIENT_CREDITS_MESSAGE,
                "data": payload,
            },
        )

    @application.exception_handler(BillingRuleNotConfiguredError)
    async def _billing_rule_not_configured(
        request: Request,
        exc: BillingRuleNotConfiguredError,
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": BILLING_RULE_NOT_CONFIGURED_MESSAGE,
                "data": billing_rule_not_configured_payload(exc),
            },
        )

    @application.middleware("http")
    async def _limit_body_size(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl:
            try:
                limit = _request_body_limit(request)
                if int(cl) > limit:
                    if _is_freezone_audio_voice_upload(request):
                        return JSONResponse(
                            status_code=200,
                            content={
                                "ok": False,
                                "error": "参考音频超过 5MB 上限，请压缩或裁剪后重新上传",
                                "data": {
                                    "code": "freezone_audio_voice_too_large",
                                    "field": "file",
                                    "limit": limit,
                                    "got": int(cl),
                                },
                            },
                        )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": {
                                "code": "canvas_payload_too_large",
                                "field": "body",
                                "limit": limit,
                                "got": int(cl),
                            }
                        },
                    )
            except ValueError:
                pass
        return await call_next(request)

    @application.middleware("http")
    async def _log_resource_requests(request: Request, call_next):
        resource_key = _resource_request_key(request.url.path)
        if resource_key is None:
            return await call_next(request)

        started_at = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started_at) * 1000
        total_count, same_resource_count = _record_resource_request(resource_key)

        query = request.url.query
        range_header = request.headers.get("range", "")
        content_length = response.headers.get("content-length", "")
        content_type = response.headers.get("content-type", "")
        logger.info(
            "resource request total=%s same_resource=%s method=%s status=%s "
            "duration_ms=%.1f path=%s query=%s range=%s bytes=%s content_type=%s",
            total_count,
            same_resource_count,
            request.method,
            response.status_code,
            duration_ms,
            resource_key,
            query or "-",
            range_header or "-",
            content_length or "-",
            content_type or "-",
        )
        return response

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.on_event("startup")
    async def startup() -> None:
        try:
            from novelvideo.ports.registry import ensure_bootstrap, get_port

            ensure_bootstrap()
            await get_port("lifecycle").on_startup(register_as_worker=True)

            from novelvideo.sqlite_pragmas import litestream_enabled

            if litestream_enabled():
                import asyncio

                from novelvideo.backup.wal_migrator import migrate_state_tree
                from novelvideo.config import STATE_DIR

                try:
                    await asyncio.to_thread(migrate_state_tree, Path(STATE_DIR))
                except Exception:
                    logger.exception("WAL migration sweep failed (non-fatal)")
            from novelvideo.api.browser_presence import install_browser_presence_watchdog

            install_browser_presence_watchdog(application)
            try:
                from novelvideo.chat.service import schedule_growth_distillation_drain

                schedule_growth_distillation_drain("local", limit=2)
            except Exception:
                logger.exception("growth distillation startup drain scheduling failed")
            projects = []
            try:
                from novelvideo.ports import get_project_registry
                from novelvideo.workflow_runtime.executor import (
                    resume_workflow_runs_for_projects,
                )

                projects = await get_project_registry().list_accessible_projects(
                    [("user", "local")]
                )
                resumed = await resume_workflow_runs_for_projects(projects)
                if resumed:
                    logger.info("resumed durable canvas workflow runs count=%s", resumed)
            except Exception:
                logger.exception("durable canvas workflow resume scan failed (non-fatal)")
            try:
                if projects:
                    from novelvideo.chat.workflow_turn_receipts import (
                        recover_terminal_workflow_turn_receipts_for_projects,
                    )

                    receipt_recovery = (
                        await recover_terminal_workflow_turn_receipts_for_projects(
                            projects
                        )
                    )
                    if receipt_recovery["messages_updated"]:
                        logger.info(
                            "recovered stale workflow terminal turn receipts count=%s",
                            receipt_recovery["messages_updated"],
                        )
            except Exception:
                logger.exception(
                    "workflow terminal turn receipt recovery failed (non-fatal)"
                )
        except Exception:
            logger.exception("API startup failed while connecting to control-plane")
            raise

    @application.on_event("shutdown")
    async def shutdown() -> None:
        from novelvideo.api.browser_presence import stop_browser_presence_watchdog
        from novelvideo.ports.registry import PortNotRegistered, get_port

        await stop_browser_presence_watchdog(application)

        try:
            from novelvideo.chat.service import shutdown_growth_distillation

            await shutdown_growth_distillation(timeout_seconds=2.0)
        except Exception:
            logger.exception("growth distillation shutdown drain failed")

        try:
            lifecycle = get_port("lifecycle")
        except PortNotRegistered:
            return
        await lifecycle.on_shutdown()

    application.include_router(api_router)

    @application.get(
        "/static/projects/{project}/{file_path:path}", include_in_schema=False
    )
    async def static_project_media(
        project: str,
        file_path: str,
        request: Request,
        st_thumb: str | None = None,
        user: dict = Depends(get_api_user),
    ):
        return await preview_project_media_file(
            project, file_path, user, st_thumb=st_thumb, request=request
        )

    # Account avatars are served per session, so this route must be registered
    # before the legacy /static catch-all below; Starlette matches in
    # registration order and the catch-all would otherwise swallow every
    # /static/avatars/<user>/<file> request into its 410 answer.
    @application.get(
        "/static/avatars/{username}/{filename}", include_in_schema=False
    )
    async def static_account_avatar(
        username: str,
        filename: str,
        user: dict = Depends(get_api_user),
    ):
        return await account_avatar_file_response(username, filename, user)

    @application.get("/static/{legacy_path:path}", include_in_schema=False)
    async def legacy_static_media(legacy_path: str):
        _ = legacy_path
        return PlainTextResponse(
            "legacy static path; use /static/projects/<project_id>/...\n",
            status_code=410,
        )

    # 原生/便携部署(无 nginx)时由后端直接伺服 SPA:设 VILLAGE_CANVAS_FRONTEND_DIST
    # 指向前端构建产物目录才启用;Docker/EE 路径不设该变量,行为不变。
    #
    # 这里不能直接 application.mount("/", ...): Mount 会吞掉 create_app()
    # 返回后追加的动态路由,并把未知 API 的 POST 转成 StaticFiles 的 405。
    # 让应用路由先完成一次真实匹配,仅对最终的非 API 404 做 SPA 回落,可同时
    # 保留静态文件的 range/cache 语义和前端深链接刷新。
    frontend_dist = _resolve_frontend_dist()
    if frontend_dist is not None:
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as _StarletteHTTPException

        class _SpaStaticFiles(StaticFiles):
            """SPA fallback: unknown extensionless paths serve index.html.

            StaticFiles 未命中时 raise HTTPException(404)(仅 dist 含 404.html
            时才返回 404 响应),回落必须捕获异常;带扩展名的缺失资产照常 404。
            """

            @staticmethod
            def _is_spa_route(path: str) -> bool:
                # A missing API endpoint must remain a real 404. Serving the
                # SPA shell here turns a contract error into a misleading
                # client-side JSON parse failure.
                # StaticFiles hands us OS-native paths on Windows. Normalize
                # separators before checking the URL namespace so /api routes
                # cannot be mistaken for client-side deep links.
                normalized = str(path or "").replace("\\", "/").lstrip("/")
                if normalized == "api" or normalized.startswith("api/"):
                    return False
                name = Path(path).name
                return path in {"", "."} or "." not in name

            @staticmethod
            def _disable_shell_cache(response):
                response.headers["Cache-Control"] = (
                    "no-store, no-cache, must-revalidate, max-age=0"
                )
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
                return response

            async def get_response(self, path: str, scope):  # type: ignore[override]
                try:
                    response = await super().get_response(path, scope)
                except _StarletteHTTPException as exc:
                    if exc.status_code == 404 and self._is_spa_route(path):
                        return self._disable_shell_cache(
                            await super().get_response("index.html", scope)
                        )
                    raise
                if Path(path).name in {"index.html", "version.json"} or self._is_spa_route(path):
                    return self._disable_shell_cache(response)
                return response

        spa_static = _SpaStaticFiles(directory=frontend_dist, html=True)

        @application.middleware("http")
        async def _spa_fallback(request: Request, call_next):
            response = await call_next(request)
            path = request.url.path
            # API and backend-owned static namespaces must retain their real
            # HTTP status and JSON/error body.  Only browser GET/HEAD requests
            # are candidates for a client-side route or frontend asset.
            if (
                response.status_code != 404
                or request.method.upper() not in {"GET", "HEAD"}
                or path == "/healthz"
                or path.startswith("/api/")
                or path == "/api"
                or path.startswith("/static/")
            ):
                return response

            relative_path = path.lstrip("/")
            try:
                return await spa_static.get_response(relative_path, request.scope)
            except _StarletteHTTPException:
                return response

    return application


app = create_app()
