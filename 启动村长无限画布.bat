@echo off
if not "%VILLAGE_CANVAS_LAUNCHER_ACTIVE%"=="1" (
  set "VILLAGE_CANVAS_LAUNCHER_ACTIVE=1"
  "%~dp0runtime\python\python.exe" "%~dp0village_canvas_launch.py" %*
  exit /b %errorlevel%
)
setlocal
set "ROOT=%~dp0"
set "DATA_DIR=%ROOT%项目资产"
rem Stable single-root layout: mutable engineering output lives under workspace.
set "VILLAGE_CANVAS_WORKSPACE_ROOT=%ROOT%workspace"
set "VILLAGE_CANVAS_ARTIFACTS_DIR=%VILLAGE_CANVAS_WORKSPACE_ROOT%\artifacts"
set "VILLAGE_CANVAS_BACKUPS_DIR=%VILLAGE_CANVAS_WORKSPACE_ROOT%\backups"
set "VILLAGE_CANVAS_CACHE_DIR=%VILLAGE_CANVAS_WORKSPACE_ROOT%\cache"
set "PYTHONUTF8=1"
set "ST_EDITION=ce"
set "VILLAGE_CANVAS_CANVAS_ONLY=1"
set "VILLAGE_CANVAS_PRODUCT_NAME=村长无限画布"
set "VILLAGE_CANVAS_OPEN_URL=http://127.0.0.1:8784/"
rem Release notices are local-only unless this product's own feed is configured.
set "VILLAGE_CANVAS_RELEASE_FEED_URL="
set "VILLAGE_CANVAS_RELEASE_FEED_TOKEN="
set "ST_CONTROL_PLANE_DSN="
set "ST_REDIS_URL="
set "ST_CELERY_BROKER_URL="
set "ST_CELERY_RESULT_BACKEND="
rem Persist/recover only Freezone/mainline task envelopes; other CE tasks stay inline.
set "ST_FREEZONE_DURABLE_QUEUE=1"
set "NEWAPI_PROVISIONER_ENABLED=true"
rem --- New API: verified WireGuard long-lived route + public fallback ---
rem The WireGuard route is the lower-latency persistent model path. Keep the
rem public endpoint available only for an explicit VILLAGE_CANVAS_HK_API_FORCE=public check.
set "HK_API_PRIMARY=http://156.245.244.194:3000/v1"
set "HK_API_FALLBACK=http://10.66.66.1:3000/v1"
set "HK_API_BASE=%HK_API_FALLBACK%"
rem VILLAGE_CANVAS_HK_API_FORCE=public bypasses WireGuard; =wg pins the long-lived path.
if /I "%VILLAGE_CANVAS_HK_API_FORCE%"=="public" set "HK_API_BASE=%HK_API_PRIMARY%"
if /I "%VILLAGE_CANVAS_HK_API_FORCE%"=="wg" set "HK_API_BASE=%HK_API_FALLBACK%"
rem One operator-facing model gateway. All legacy NewAPI variables below are
rem derived compatibility aliases and must never point to a second endpoint.
set "VILLAGE_CANVAS_GATEWAY_BASE_URL=%HK_API_BASE%"
set "VILLAGE_CANVAS_GATEWAY_OFFICIAL_FIRST=false"
rem Catalog discovery remains on the active route. The WireGuard endpoint is
rem diagnostic-only and must never become a silent model-list fallback.
set "VILLAGE_CANVAS_MODEL_CATALOG_FALLBACK_URL=%HK_API_BASE%"
set "NEWAPI_BASE_URL=%VILLAGE_CANVAS_GATEWAY_BASE_URL%"
rem Avoid system proxy hijacking HK new-api (Clash etc.). Village Canvas protocol = chat + images + embed.
if not defined NEWAPI_TEXT_TRUST_ENV set "NEWAPI_TEXT_TRUST_ENV=false"
set "NEWAPI_TEXT_TIMEOUT_SECONDS=300"
rem Bytecode cache is regenerable state, not user data, and it mirrors absolute
rem paths; keep it inside the checkout so a moved project never strands copies.
set "PYTHONPYCACHEPREFIX=%ROOT%.cache\pycache"
if not exist "%ROOT%.cache" mkdir "%ROOT%.cache"
if not exist "%DATA_DIR%\state" mkdir "%DATA_DIR%\state"
if not exist "%DATA_DIR%\output" mkdir "%DATA_DIR%\output"
if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
rem Keep the API alive after the browser/automation tab closes; queued tasks,
rem task polling and the unified gateway are independent of canvas presence.
set "VILLAGE_CANVAS_BROWSER_AUTO_SHUTDOWN=false"
rem Private upload over WireGuard; public random URLs let multimodal upstreams fetch images.
set "VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL=http://10.66.66.1:8782/upload"
set "VILLAGE_CANVAS_MEDIA_RELAY_TOKEN="
if exist "%DATA_DIR%\state\media-relay-token.txt" (
  set /p VILLAGE_CANVAS_MEDIA_RELAY_TOKEN=<"%DATA_DIR%\state\media-relay-token.txt"
)
set "NOVELVIDEO_DATA_ROOT=%DATA_DIR%"
set "NOVELVIDEO_STATE_DIR=%DATA_DIR%\state"
set "NOVELVIDEO_OUTPUT_DIR=%DATA_DIR%\output"
set "NOVELVIDEO_RUNTIME_DIR=%DATA_DIR%\runtime"
set "PYTHONPATH=%ROOT%src;%ROOT%runtime\env;%ROOT%runtime\env\win32;%ROOT%runtime\env\win32\lib;%ROOT%runtime\env\Pythonwin"
set "PATH=%ROOT%runtime\ffmpeg;%ROOT%runtime\node;%PATH%"
set "ST_SPLAT_TRANSFORM_BIN=%ROOT%runtime\node\splat-transform.cmd"
set "VILLAGE_CANVAS_FRONTEND_DIST=%ROOT%frontend\dist"
set "VILLAGE_CANVAS_AGENT_SKILLS_DIR=%ROOT%agent_skills"
set "NOVELVIDEO_API_HOST=127.0.0.1"
set "NOVELVIDEO_API_PORT=8784"
set "VILLAGE_CANVAS_API_URL=http://127.0.0.1:8784"
rem Open the stable app root; never pin startup to a project/canvas that can be deleted.
if not defined VILLAGE_CANVAS_DIAGNOSTICS_URL set "VILLAGE_CANVAS_DIAGNOSTICS_URL=http://127.0.0.1:8784/"
set "VILLAGE_CANVAS_LOG_FILE=%DATA_DIR%\logs\backend.log"
set "VILLAGE_CANVAS_LOG_MAX_BYTES=33554432"
set "VILLAGE_CANVAS_LOG_BACKUPS=3"
set "VILLAGE_CANVAS_CHAT_BACKEND=village"
rem Canvas Agent web research only reads the protected key file inside this project.
set "TAVILY_KEYS_FILE=%DATA_DIR%\state\tavily-keys.txt"
rem Unified exit: New API only (Agent + media). No Cockpit or provider-direct calls.
rem Routing is role-based inside New API: canvas/vision, batch text, director, image, video, embedding.
set "VILLAGE_CANVAS_GATEWAY_API_KEY="
if exist "%DATA_DIR%\state\gateway-api-key.txt" (
  set /p VILLAGE_CANVAS_GATEWAY_API_KEY=<"%DATA_DIR%\state\gateway-api-key.txt"
)
rem This is the sole operator key. All client-specific variables are aliases.
set "NEWAPI_API_KEY=%VILLAGE_CANVAS_GATEWAY_API_KEY%"
set "MODEL_API_KEY=%VILLAGE_CANVAS_GATEWAY_API_KEY%"
set "MODEL_BASE_URL=%VILLAGE_CANVAS_GATEWAY_BASE_URL%"
rem 统一中转站是唯一运行路由：忽略外部会话残留的旧 Provider / 模型变量。
set "MODEL_PROVIDER=newapi"
set "MODEL_NAME="
rem Canvas Agent model and endpoint come only from the local direct Agent registry.
rem Clear legacy environment injections so a stale shell cannot override it.
rem Structured text helpers must resolve their direct model rows at runtime.
rem Leave these variables empty so no retired model alias can be injected.
set "LITERAL_BEAT_META_MODEL="
set "SCREENPLAY_NORMALIZER_MODEL="
if not defined LITERAL_BEAT_META_THINKING_LEVEL set "LITERAL_BEAT_META_THINKING_LEVEL=none"
if not defined SCREENPLAY_NORMALIZER_THINKING_LEVEL set "SCREENPLAY_NORMALIZER_THINKING_LEVEL=none"
rem All text and vision helpers resolve through the local direct registries.
set "GLOBAL_VIDEO_OPTIMIZER_MODEL="
set "GLOBAL_VIDEO_IDENTITY_DETECTOR_MODEL="
set "STYLE_ANALYZER_MODEL="
set "FREEZONE_PROMPT_OPTIMIZER_MODEL="
rem Agent transport and token budgets are resolved from the selected model-center row.
set "VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS=1"
set "VILLAGE_CANVAS_DIRECT_MODELS_ONLY=1"
set "VILLAGE_CANVAS_DIRECTOR_WORKFLOW_WRITE_STEP_LIMIT=8"
rem DeepSeek-class batch models burn reasoning tokens; keep a floor so content is not empty.
if not defined VILLAGE_CANVAS_TEXT_MAX_TOKENS_FLOOR set "VILLAGE_CANVAS_TEXT_MAX_TOKENS_FLOOR=64"
set "FREEZONE_VISION_MODEL="
set "VILLAGE_CANVAS_MODEL_FALLBACK="
set "VILLAGE_CANVAS_TEXT_MODEL_FALLBACK="
set "NEWAPI_IMAGE_MODEL="
set "VILLAGE_CANVAS_IMAGE_MODEL_FALLBACK="
set "VILLAGE_CANVAS_IMAGE_EDIT_MODEL_FALLBACK="
rem Stable single-channel image route: Yunfei only. Legacy NB-2 selections are
rem resolved to the verified G2 route; enable NB-2 only after a fresh probe.
set "NEWAPI_NANOBANANA2_ENABLED=false"
set "NEWAPI_IMAGE_FAILOVER_CHANNEL_ID="
if not defined NEWAPI_IMAGE_HTTP_TIMEOUT_SECONDS set "NEWAPI_IMAGE_HTTP_TIMEOUT_SECONDS=420"
set "NEWAPI_FAST_REFERENCE_MODE=false"
rem Unified concurrency limit for every task lane.
set "ST_PROJECT_USER_MAX_ACTIVE_DEFAULT_TASKS=500"
set "ST_PROJECT_USER_MAX_ACTIVE_AUDIO_TASKS=500"
set "ST_PROJECT_USER_MAX_ACTIVE_VIDEO_TASKS=500"
set "ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS=500"
set "ST_PROJECT_USER_MAX_ACTIVE_FFMPEG_TASKS=500"
set "ST_PROJECT_MAX_ACTIVE_DEFAULT_TASKS=500"
set "ST_PROJECT_MAX_ACTIVE_AUDIO_TASKS=500"
set "ST_PROJECT_MAX_ACTIVE_VIDEO_TASKS=500"
set "ST_PROJECT_MAX_ACTIVE_WORLD_TASKS=500"
set "ST_PROJECT_MAX_ACTIVE_FFMPEG_TASKS=500"
set "ST_GLOBAL_MAX_ACTIVE_DEFAULT_TASKS=500"
set "ST_GLOBAL_MAX_ACTIVE_AUDIO_TASKS=500"
set "ST_GLOBAL_MAX_ACTIVE_VIDEO_TASKS=500"
set "ST_GLOBAL_MAX_ACTIVE_WORLD_TASKS=500"
set "ST_GLOBAL_MAX_ACTIVE_FFMPEG_TASKS=500"
rem CE inline executor uses the ST_CE_* namespace.
set "ST_CE_GLOBAL_MAX_ACTIVE_DEFAULT_TASKS=500"
set "ST_CE_GLOBAL_MAX_ACTIVE_AUDIO_TASKS=500"
set "ST_CE_GLOBAL_MAX_ACTIVE_VIDEO_TASKS=500"
set "ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS=500"
set "ST_CE_GLOBAL_MAX_ACTIVE_FFMPEG_TASKS=500"
set "ST_CE_GLOBAL_MAX_QUEUED_DEFAULT_TASKS=10000"
set "ST_CE_GLOBAL_MAX_QUEUED_VIDEO_TASKS=50"
set "ST_CE_GLOBAL_MAX_QUEUED_WORLD_TASKS=10000"
set "ST_CE_GLOBAL_MAX_QUEUED_FFMPEG_TASKS=10000"

rem Text/image/vision keep the active gateway. Video is configured only through
rem the local direct-video registry, so no inherited gateway variable can alter
rem its model, billing route or result-download path.
set "VILLAGE_CANVAS_VIDEO_USE_DEDICATED_GATEWAY=false"
set "NEWAPI_VIDEO_BASE_URL="
set "NEWAPI_VIDEO_API_KEY="
set "NEWAPI_VIDEO_CREATE_PATH="
set "NEWAPI_VIDEO_MODELS="
set "DEFAULT_VIDEO_MODEL="
set "NEWAPI_VIDEO_MODEL="
set "NEWAPI_VIDEO_RESOLUTION="
set "NEWAPI_VIDEO_AUDIO_MODELS="
set "NEWAPI_VIDEO_DURATION_BOUNDS="
set "NEWAPI_VIDEO_GENERATE_AUDIO="
set "NEWAPI_VIDEO_AUTO_FACE="
set "VILLAGE_CANVAS_ALLOW_EXPLICIT_VIDEO_MODEL=true"
set "VIDEO_BACKEND="
set "VILLAGE_CANVAS_VIDEO_MODEL_FALLBACK="
rem Direct models never cross-failover: the selected endpoint stays pinned.

netstat -ano | findstr ":8784" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  start "" "%VILLAGE_CANVAS_DIAGNOSTICS_URL%"
  endlocal
  exit /b 0
)

rem zombie-guard: 8784 not listening; clear package python leftovers before cold start (CREATE_NO_WINDOW, no flash)
if exist "%ROOT%_stop.ps1" (
  "%ROOT%runtime\python\python.exe" -c "import subprocess; subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',r'%~dp0_stop.ps1'], creationflags=0x08000000)"
)

rem �����º��ǰ���������־��������ʷ������������תʧ�ܲ���Ϻ��������
if exist "%ROOT%village_canvas_rotate_log.py" (
  "%ROOT%runtime\python\python.exe" "%ROOT%village_canvas_rotate_log.py" --log "%VILLAGE_CANVAS_LOG_FILE%" --max-bytes "%VILLAGE_CANVAS_LOG_MAX_BYTES%" --backups "%VILLAGE_CANVAS_LOG_BACKUPS%"
  if errorlevel 1 echo [Village Infinite Canvas] log rotation skipped; backend startup will continue.
)

rem model_catalog module is optional; never block cold start on missing package
"%ROOT%runtime\python\python.exe" -m novelvideo.model_catalog --refresh --timeout 4 >nul 2>&1
if errorlevel 1 echo [Village Infinite Canvas] model catalog preflight unavailable; requested model names will be preserved.

rem Detached daemon via village_canvas_run_api.py (survives bat/console exit; append logs)
start "" /b "%ROOT%runtime\python\pythonw.exe" "%ROOT%village_canvas_open_when_ready.py"
start "Village-Infinite-Canvas-8784" /MIN "%ROOT%runtime\python\python.exe" "%ROOT%village_canvas_run_api.py"
echo [Village Infinite Canvas] API launched detached on 127.0.0.1:8784
endlocal
