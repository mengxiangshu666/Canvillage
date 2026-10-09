============================================================
Canvillage · 村长无限画布 Windows 入口说明
============================================================

英文全称：Infinite Canvas for Village Chiefs

这是当前源码目录的 Windows 启动说明。发行包中的独立说明由打包脚本生成，
以发行包内的 README-Portable.txt 为准。

启动
----
权威入口是同目录的“启动村长无限画布.vbs”或“启动村长无限画布.bat”。
启动器会拉起内置运行时和 API，并在服务就绪后打开：

    http://127.0.0.1:8784

若服务已经运行，再次启动只会打开网页，不会重复拉起后端。

停止
----
运行同目录的 `_stop.ps1`。它只清理本项目运行时及其子进程，不会结束其他
Python、Node 或 FFmpeg 进程。

数据与产物
----------
用户项目、画布、媒体、模型配置、任务状态和 Agent 记忆位于：

    项目资产\

工程报告、构建证据和回滚快照位于：

    workspace\

根目录的 `state`、`output`、`artifacts`、`_task_backups`、`_deploy_backups`
是指向上述 workspace/项目资产目录的兼容链接，不要把它们当作第二份数据删除。

开发与发布
----------
源码开发需要 `runtime\`、Python 依赖和前端依赖。不要把 `.venv`、
`frontend\node_modules`、`workspace\` 或 `项目资产\` 直接复制到发行包。
使用 `scripts\package_village_canvas.ps1` 生成白名单发行包，再按包内
`BUILD_INFO.json`、`MANIFEST.json` 和 `SHA256SUMS` 核对版本与文件完整性。

故障排查
--------
1. 先访问 `http://127.0.0.1:8784/healthz`，确认服务返回 `status: ok`。
2. 若浏览器仍显示旧界面，重新运行启动器；启动器会自动追加缓存破除参数。
3. 查看 `项目资产\logs\` 中的后端日志。不要删除正在运行的 SQLite
   `*.db-wal` 或 `*.db-shm` 文件。
4. 修改启动配置后先停止服务，再重新启动；保留 `workspace\backups\` 中的
   回滚快照。

源码安装与数据目录说明见 `README.md`。源码包不包含便携 runtime，
不能当成已经安装好依赖的独立运行包。
