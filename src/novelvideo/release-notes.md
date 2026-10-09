---
version: 1.1.44
attention: medium
---
# v1.1.44

## User-facing Highlights (zh)

- **左侧有了常驻导航**：首页、全部项目、画布、工作流、任务一次排开，不用再靠顶栏猜路径；画布页仍然是全屏工作区，不挂侧栏。
- **首页去掉重复入口**：首屏只留一句输入框，「所有项目」全页只出现一次，起步路线那块的第二个同名入口换成了路线数量。
- **暗色面板分了三层**：背景、面板、浮层各有自己的层级，边框改成发丝线，卡片和按钮不再是「一坨黑」。
- **圆角和阴影收紧**：全局圆角从 16px 收到 12px 刻度，卡片不超过 10px，输入框和对话框跟着一起变紧凑。
- **强调色统一**：青色和蓝色两套强调色合并为一个蓝，选中态、主按钮和焦点环同源。

## User-facing Highlights (en)

- **A persistent left navigation**: home, all projects, canvas, workflow and tasks in one rail, so you no longer guess routes from the top bar. The canvas stays a full-screen workspace with no rail.
- **The home page stops repeating entries**: the first screen keeps a single composer, "All projects" appears once, and the second same-named entry under the starter showcase is now a route count.
- **Three dark elevations**: background, panel and popover each got their own level, borders became hairlines, and cards no longer read as one dark slab.
- **Tighter radii and shadows**: the global radius scale moved from 16px to 12px, cards stay at or under 10px, and inputs and dialogs tightened with it.
- **One accent**: the cyan and blue accents merged into a single blue used by selection, primary buttons and focus rings.

## Bug Fixes

- The home page rendered "All projects" twice on one screen; the showcase now shows a route count instead.

---
version: 1.1.43
attention: medium
---
# v1.1.43

## User-facing Highlights (zh)

- **有了一个新首页**：打开就是一句问候、一个「说一句就能开始」的输入框。你写完那句话，系统直接建好画布，并把原话带进画布里的对话输入框，不用再复制粘贴。首页改成单层总览，最近项目只铺一次，「所有项目」单独打开，不再上下重复展示。
- **起步路线一键落地**：首页列出一条条可直接开始的画布骨架（原创短片、分镜到视频、单参考图出片、多段合成等），点一下新画布就把可编辑的节点图铺好，模型和素材之后再选。
- **最近画布一眼可见**：首页按最近改动排开你手上的画布，点封面直接回到上次的工作现场。
- **视频提示词不再胡说**：文生 / 图生 / 关键帧 / 全能参考四类提示词改用同一张槽位表装配，节点只连一张图就不会再声称「综合文本、图像、视频和音频统一建模」；没有可靠来源的光学、物理、灯光三段保留占位但不再编造内容。
- **视频渠道合同变成数据包**：八个上游家族的字段定义从代码搬进独立数据文件，加渠道、改参数只动数据，合同解析错误在导入期就被拦住，不会拖到出片时才炸。
- **台词不再被漏抽**：作者写的中文台词不套引号、或引号被输入法拆碎，现在也能认出来并归到对白槽位，不再留在画面描述里被念成一串。
- **逐帧拉片看得懂了**：拉片面板按 libtv 的口径重排，接缝、主体、动作、镜头各自有位，不再是一堆没有说明的数字。
- **节点输入面板不随画布缩放变小**：画布缩得再小，点开节点弹出的输入框都保持固定屏幕尺寸，不会缩成看不清的一小条。
- **视频节点对齐 libtv / 剪映**：音视频分离改成带 ▾ 的三项下拉（人声提取、背景音提取本机未装模型时置灰并说明原因），参考素材悬停预览从 140px 放到 240px 并补上序号与名称。

## User-facing Highlights (en)

- **A new home page**: it opens on a greeting and a "say one line and start" composer. Submitting it creates the canvas and carries your sentence into the canvas chat box, so nothing has to be copied by hand. The home page is now a single-level overview: recent projects appear once, while "All projects" opens as a separate view instead of repeating the same grid below.
- **Starter routes land in one click**: the home page lists canvas skeletons you can begin from (original short film, storyboard to video, single-reference clip, multi-clip composition), and one click lays down an editable node graph. Models and media come later.
- **Recent canvases at a glance**: your latest canvases line up on the home page; clicking a cover returns you to where you left off.
- **Video prompts stop inventing**: text-to-video, image-to-video, keyframe and omni-reference prompts now assemble from one shared slot table, so a node wired to a single image no longer claims to combine text, image, video and audio references. The optics, physics and lighting slots stay reserved but no longer fabricate content without a reliable source.
- **Video channel contracts are data now**: field definitions for eight upstream families moved out of code into standalone data packages, so adding a channel or changing a field touches data only, and contract parse errors surface at import rather than at render time.
- **Dialogue is no longer dropped**: Chinese lines written without quotation marks, or with quotation marks shattered by the IME, are now recognised and routed to the dialogue slot instead of being read aloud as part of the scene description.
- **Shot breakdown is readable**: the breakdown panel is reorganised on libtv's terms, with seam, subject, action and camera each getting a labelled place instead of a wall of unlabelled numbers.
- **Node input panels keep their size**: zooming the canvas out no longer shrinks a node's popup input panel into an unreadable strip.
- **The video node lines up with libtv / Jianying**: audio-video separation became a three-item dropdown with a caret (voice extraction and background extraction are greyed out with a written reason when the local model is absent), and the reference hover preview grew from 140px to 240px with an index and name strip.

## Bug Fixes

- The video node wrote "combines text, image, video and audio references" unconditionally, even when a single image node was connected — false information that the native-audio model could read aloud. Prompt assembly now states only the references the node actually has.
- Dialogue extraction required either quotation marks or a colon; lines written as plain prose fell through and were duplicated into the scene description. Both shapes are now recognised, including the shattered-quote cases.
- Shot-breakdown fields were rendered without labels, so the panel could not be understood without reading the source.
- Node input panels were laid out in canvas coordinates and scaled with zoom, so at low zoom the controls shrank below usable size.

---
version: 1.1.42
attention: medium
---
# v1.1.42

## User-facing Highlights (zh)

- **脚本节点不再把创作指令当台词念**：脚本节点向下游只传它产出的分镜表，不再把「帮我生成不会被拦截的脚本」这类自己的生成指令当成上游内容，视频模型不会再逐字朗读这句要求。
- **脚本表可编辑、可追溯**：表格里改一行提示词现在会进操作日志，和自动保存口径一致；生成失败走人话错误卡并带出阶段、请求号和可重试判断，分镜与翻译的静默失败也一并出声。
- **分镜图带齐角色参考**：分镜图节点一次带出该行的全部角色参考图，不再只取第一张；脚本提示词把角色卡逐字一致、风格与参数段全片唯一升成硬要求。
- **时长不再被静默改动**：视频时长档位取渠道真值，提交前对账、出片后复核，30 秒不会再被悄悄折成 15 秒。
- **编码器按本机能力探测**：不再写死本机没有的 libx264，按当前 ffmpeg 实际可用的编码器与滤镜选择。
- **空画布首个入口真的能点**：空画布给可点击的起步路线，直接落地可编辑节点图，不再是只有快捷键提示的一句话。

## User-facing Highlights (en)

- **Script nodes stop speaking their own instructions**: a script node passes only the storyboard table it produced downstream, never its own steering prompt, so video models no longer read that instruction aloud.
- **The script table is editable and auditable**: editing a prompt cell now reaches the canvas mutation log, matching autosave; generation failures render the human-readable error card with stage, request id and retryability, and storyboard/translation failures no longer fail silently.
- **Storyboard images carry every character reference**: a storyboard node now emits all of that row's character references instead of only the first, and the script prompt hard-requires verbatim character cards and a single film-wide style block.
- **Requested duration is honoured**: video duration tiers come from the channel's real options, reconciled before submit and re-checked after the render, so 30 seconds is no longer silently folded to 15.
- **Encoders are probed, not assumed**: the pipeline detects the ffmpeg build's actual encoders and filters instead of hardcoding libx264.
- **An empty canvas has a clickable entry**: the starter routes insert editable nodes directly rather than showing a keyboard-shortcut hint that does nothing.

## Bug Fixes

- Script prompt assembly no longer forwards `data.prompt` as upstream content, which was the root cause of H3 narrating the prompt; every row keeps a single style and parameter block.
- `scriptResult` left the ephemeral-field list in the canvas mutation kernel, so a table edit produces a real `update_node` patch instead of zero patches.
- Script node model caches are invalidated under their real names, and rejected character references are reported instead of being dropped silently.
- Video catalog cold start no longer writes degenerate parameters (a hardcoded 1:1 / 480P) into nodes, and unmeasured nodes no longer fall back to a zero size.
- The composition gate accepts a single video track, matching the backend contract.
- Directory-gateway contract tests normalise line endings before cross-line matching, fixing two cases that always failed on a fresh CRLF checkout.
- The Agent skin layer is down from 594 to 8 `!important` declarations with no behaviour change.

---
version: 1.1.41
attention: low
---
# v1.1.41

## User-facing Highlights (zh)

- **导演澄清更聚焦**：默认流程不再追问受众、发布平台或内部样片等固定问题，只保留会改变当前创作执行的关键信息。
- **视频请求字段更清晰**：视觉风格、画幅、角色参考和声音等真实创作参数继续按缺口追问，减少无效预设干扰。
- **回归覆盖更完整**：新增回归确认主体确认后会直接进入视觉风格判断，避免再次弹出无效的受众或平台问题。

## User-facing Highlights (en)

- **More focused director clarification**: The default flow no longer asks fixed audience, publishing-platform, or internal-sample questions unless the task contract requires them.
- **Clearer video request fields**: Visual style, framing, character references, and sound remain part of clarification when they affect the actual creative execution.
- **Broader regression coverage**: Regression checks now confirm that a new video request moves to visual-style decisions after the subject is known instead of showing irrelevant presets.

## Bug Fixes

- Removed the default audience, publishing-platform, and internal-sample questions from director clarification while preserving contract-specific audience constraints.
- Kept the existing subject, scene, visual-style, aspect-ratio, reference, and sound gates intact for requests that genuinely need them.

---
version: 1.1.40
attention: low
---
# v1.1.40

## User-facing Highlights (zh)

- **工作流彻底不再抢导演职责**：生产控制和通用 WorkflowRun 都只负责接收任务、绑定画布事实并执行，不会再次弹出“这支片具体要表现什么主体或事件？”。
- **画布 Agent 保留真正的追问能力**：缺少主体、场景或关键创作信息时，仍由画布 Agent 在执行前逐问并形成任务契约。

## Bug Fixes

- 移除 `ProductionControlService` 和 `WorkflowRuntimeService` 两条启动入口的重复导演澄清拦截。
- 保留画布快照、revision、目标绑定、付费确认和其他执行门禁，避免为了消除误弹窗而削弱工作流一致性。
- 增加生产与通用 WorkflowRun 回归，确认无澄清答案也能创建持久运行，同时 Agent/画布侧澄清门继续有效。

---
version: 1.1.39
attention: low
---
# v1.1.39

## User-facing Highlights (zh)

- **工作流只负责执行**：生产总控不再重复弹出画布 Agent 的导演澄清问题；已形成的项目/故事任务可以直接进入持久运行。
- **职责边界清晰**：导演追问继续由画布 Agent 在执行前完成，工作流启动器不再把正常的澄清状态包装成“启动失败”。

## Bug Fixes

- 移除生产控制启动入口对 `director_clarification_required` 的重复拦截，保留付费确认、续跑和其他执行门禁。
- 增加回归测试，确认空目标的 `novel_adapt` 启动会创建运行，Agent 侧澄清门和画布命令门仍保持原行为。

---
version: 1.1.38
attention: medium
---
# v1.1.38

## User-facing Highlights (zh)

- **最终成片真正交付**：`final_film` 工作流现在会复用已完成的视频资产，幂等地启动合成、回读任务状态，并在文件、尺寸、时长和哈希都验证通过后才返回最终成片回执。
- **生产总控看得见真实进度**：总控页展示统一生产合同的七阶段状态、交付级别、质量门、成本回执和最终文件，不再只显示旧的四阶段外壳。
- **失败可恢复且不重复扣费**：合成失败会进入可重试状态，重试复用确定性的任务范围；重复刷新或推进不会并行创建第二个合成任务。

## User-facing Highlights (en)

- **Final-film delivery is real**: `final_film` workflows reuse verified video assets, enqueue composition idempotently, reconcile task state, and expose a final receipt only after file, metadata, and hash checks pass.
- **Production control shows the real contract**: The command center now projects seven contract stages, delivery level, quality gates, cost receipts, and the final file instead of only the legacy four-stage shell.
- **Recoverable failures without duplicate work**: Compose failures are retryable within a deterministic task scope; refreshes and repeated advances never enqueue a second compose task in parallel.

## Bug Fixes

- 修复工作流媒体产物位于 Freezone 输出目录时无法进入既有 FFmpeg 合成器的问题，同时拒绝远程 URL 和越出项目目录的路径。
- 补齐 `WorkflowExecutor.advance()` 从入队、运行、失败、重试到最终 `final_compose_artifact` 的持久状态机回归。
- 生产合同的动态运行态只作为只读投影返回，避免把任务文件和质量证据写回冻结合同 revision。

---
version: 1.1.36
attention: medium
---
# v1.1.36

## User-facing Highlights (zh)

- **执行身份不丢失**：每次 Agent turn/action 绑定同一个执行上下文，规划、能力门、画布写入、工作流续跑和回执都能对上同一身份。
- **重试不重复执行**：同一执行身份的传输重试直接复用权威回执，过期画布 revision、跨项目/画布或身份不一致的请求会在写入前明确阻断。
- **上下文更干净**：Hermes 不再重复注入专家计划里的执行上下文，减少长会话噪声并保持机器可解析字段完整。

## User-facing Highlights (en)

- **Stable execution identity**: Each Agent turn/action carries one execution context across planning, capability gates, canvas writes, workflow recovery, and receipts.
- **Retry without duplicate writes**: Transport retries reuse the authoritative receipt; stale revisions and scope or identity mismatches are blocked before mutation.
- **Cleaner context**: Hermes keeps one machine-readable execution context instead of duplicating the expert-plan copy.

## Bug Fixes

- 修复动态 Agent dispatch 因二次规划、命令 ID 漂移或 WorkflowRun 载体变化导致的重复写入和假幂等冲突。
- 新增执行身份、checkpoint、Gateway、WorkflowRun 续跑和 HTTP 合同回归，确保错误不会进入成长记忆。

---
version: 1.1.35
attention: medium
---
# v1.1.35

## User-facing Highlights (zh)

- **舰队分工可见**：SuperChat 直接展示本轮动态选中的专家、规划到执行的路径、真实 handler 和回执交接策略，延后角色不会伪装成已执行。
- **原创入口贯通**：制作总控新增“个人原创 / 小说改编”双模式，原创创意或剧本会按后端既有 `ep000` Gate A/B 流程进入生产控制。

## Bug Fixes

- 修复前端已有 Agent Fleet 合同只在后端黑板中可见、用户无法核对专家交接的问题。
- 修复生产总控未透传 `entry_mode`、原创创意和原创剧本的问题。

# v1.1.34

## User-facing Highlights (zh)

- **模型能力不再被阉割**：直连模型和 AutoDL/ComfyUI 工作流的真实输入规则、音频语义、参考声线和 provider 映射贯通到模型中心、节点和生产任务。
- **音频引用按合同执行**：驱动音频、声线样本、配乐和音频提示严格区分，只有上游明确声明支持时才会提交对应引用。
- **刷新恢复更稳定**：WebSocket fanout 在事件发出时锁定接收者，旧帧不会串入刷新后新连接。

## Bug Fixes

- 修复全量测试中认证模块重载导致的聊天恢复测试使用过期路由引用。
- 修复 AutoDL 音频任务的模型合同、任务查询和 MP3 产物链路。
- 补齐当前 Git 文件的许可证清单与 SBOM 证据。

---
version: 1.1.33
attention: medium
---
# v1.1.33

## User-facing Highlights (zh)

- **Agent 舰队交接闭环**：能力 Broker 在不同 Hermes/MCP 返回格式下统一解析真实结果，并为每次专业 Agent 调用生成有界的 `agent_specialist_result.v1`。
- **任务完成以产物为准**：`task.get` 的 queued/running/failed 状态和无稳定产物的 completed 状态会被准确保留；只有带稳定 Artifact ID 与 SHA-256 的任务才允许交给下游 Agent。
- **媒体产物可复用**：项目内真实任务输出进入内容寻址 Artifact Store，采用流式哈希、硬链接优先和原子落盘，避免大视频重复读入内存与半成品引用。

## Bug Fixes

- 修复 Hermes 将工具结果序列化为 JSON 字符串时绕过 Agent 状态/产物门控的问题。
- 修复 provider URL 或临时成功文案被误当作跨 Agent 产物交接的问题。
- 增加 task.get 字符串边界、任务状态和 Artifact 交接回归覆盖。

---
version: 1.1.32
attention: medium
---
# v1.1.32

## User-facing Highlights (zh)

- **多项目/多会话真正并行**：Hermes worker 按用户、项目、画布、会话、模型和模型网关指纹隔离，不再因切换项目而关闭或覆盖旧会话。
- **精确恢复与清理**：会话回切、上下文刷新、研究权限、失败会话 discard、空闲回收和 LRU 驱逐都只作用于目标作用域，其他项目继续保持在线。
- **作用域预热**：同一作用域的重复预热自动去重，不同项目和会话可以并行预热，首条消息不再被另一标签页的冷启动拖住。

## Bug Fixes

- 修复 HermesPool 仍以 `_slots[username]` 作为权威路由导致的双会话覆盖和冻结风险。
- 增加并行、回切、精确删除、全用户关闭、作用域 LRU 和跨作用域预热回归测试。

---
version: 1.1.31
attention: medium
---
# v1.1.31

## User-facing Highlights (zh)

- **动态 Agent 舰队**：总导演按当前意图召集最小专业队伍，叙事、资产、镜头、提示词、执行、品控和成长记忆角色按需参与，不再让一个 Agent 面对全部内部入口。
- **计划与执行分离**：舰队只输出事实、计划、提示词和校验结果，真实画布写入与媒体任务仍统一回到 `village_canvas_dispatch_action`、CanvasCommandGateway 和 WorkflowRun。
- **舰队状态可回放**：Agent 计划、Hermes 黑板和 runtime allowlist 记录稳定的 fleet revision、角色依赖、调度分组和副作用策略。
- **断线继续同一支队伍**：恢复点保存 plan/context/fleet/allowlist revision 与选中角色，重建 Hermes 工作线程时先识别过期编排，避免重复组队或沿用旧能力清单。

## Bug Fixes

- 修复 Agent 专业角色只有抽象 shadow 规划、缺少按意图动态组队合同的问题。
- 增加动态选队、失败任务续跑、成长记忆任务和 Hermes 黑板接线回归。
- 增加编排身份的有界 checkpoint 序列化与恢复提示注入。

---
version: 1.1.29
attention: medium
---
# v1.1.29

## User-facing Highlights (zh)

- **恢复执行前门禁**：断点恢复会在 Hermes 工具执行前读取结构化恢复合同，已有上游任务只允许查询/对账，已有 WorkflowRun 只允许绑定原运行继续。
- **杜绝恢复重复提交**：恢复回合拒绝新的媒体提交、替代工作流和新画布写入，保留原任务身份并把阻断状态写入工作流进度。

## Bug Fixes

- 修复恢复合同只作为提示词存在、模型忽略后仍可重复提交任务的问题。
- 增加 provider task、WorkflowRun 原 ID、错误 run 和新画布写入的 Hermes 执行前回归覆盖。

---
version: 1.1.28
attention: medium
---
# v1.1.28

## User-facing Highlights (zh)

- **断点恢复真正闭环**：浏览器刷新、Agent 线程重建或 API 重启后，恢复卡从持久状态重新出现，并继续原会话的 checkpoint。
- **工作流前沿可回放**：恢复点保存 WorkflowRun、当前 frontier、步骤状态、画布 revision 和上游任务身份，避免从头重跑。
- **已提交媒体只对账不重复提交**：视频任务保留 provider task ID，恢复时进入查询/下载路径，不再次创建上游任务。

## User-facing Highlights (en)

- **Durable turn recovery**: browser refreshes, Agent worker rebuilds, and API restarts rediscover pending recovery cards from persistent state.
- **Replayable workflow frontier**: checkpoints retain the WorkflowRun, current frontier, bounded step states, canvas revision, and accepted upstream task identities.
- **No duplicate media submission**: accepted video provider task IDs are retained so recovery polls/downloads the existing task instead of creating another one.

## Bug Fixes

- 修复恢复记录只存在进程内存、刷新后消失的问题。
- 修复 ACP 结果缺少标题字段时恢复点保存触发 `IndexError` 的问题。
- 修复恢复新物理 turn 未读取原失败 turn checkpoint 的问题。

---
version: 1.1.27
attention: medium
---
# v1.1.27

## Bug Fixes

- 修复 Hermes ACP 长 JSON 消息触发 `Separator is found, but chunk is longer than limit` 导致 Agent 回合中断的问题。
- ACP 子进程 stdout/stderr 使用有界的 4 MiB 单行缓冲（可通过 `HERMES_ACP_LINE_LIMIT_BYTES` 调整，最高 32 MiB）；超限会关闭坏会话并保留恢复点。
- 超限错误现在返回可行动的恢复提示，不再把 Python 底层异常原文直接显示给用户。

---
version: 1.1.26
attention: medium
---
# v1.1.26

## User-facing Highlights (zh)

- **节点能力跨层对账**：前端节点目录、Agent 能力卡和后端任务 runner 现在由同一条 parity gate 持续校验，减少“看起来能用、实际走错执行器”的断裂。
- **视频操作路径真实一致**：剪辑和两种去字幕明确进入视频操作面板；下载和全屏明确走浏览器桥接；拉片、高清和音视频分离继续走可查询异步任务。

## Bug Fixes

- 修复去字幕节点被误标为直接异步任务、绕过用户确认面板的问题。
- 修复剪辑节点的能力合同与 Agent 的 `video_set_operation` 路径不一致的问题。
- 新增 `validate_node_capability_parity.py`，提交前检查能力映射、任务类型、runner 和任务标签。

---
version: 1.1.25
attention: medium
---
# v1.1.25

## User-facing Highlights (zh)

- **视频节点能力贯通 Agent**：拉片解析、视频高清和音视频分离现在从持久化节点读取真实素材并返回可查询任务回执。
- **浏览器动作可调用**：Agent 可真实下载视频或打开全屏查看器，均通过现有 FE bridge 并返回完成回执。
- **能力与事实绑定**：空视频节点、错误节点类型或跨画布节点会在任务创建前被拦截，避免模型凭空猜 URL。

## Bug Fixes

- 补齐视频节点真实操作与 Agent 能力索引之间的跨层差集。
- 扩展 FE 工具名称合同，保持服务端、浏览器端和能力卡一致。

---
version: 1.1.24
attention: medium
---
# v1.1.24

## User-facing Highlights (zh)

- **节点能力与 Agent 执行合同统一**：节点目录现在同时暴露真实输入、输出、副作用、子节点和执行入口，Agent 看到的能力与画布可执行路径一致。
- **视频节点能力可被真实调用**：首帧、尾帧、当前帧、剪辑和去字幕通过浏览器桥接返回真实回执，引用视频也能沿同一链路截帧。
- **未接通能力不再伪装可用**：深度动作捕捉明确标记为未接线，避免 Agent 把目录登记误报成已经完成的生产能力。
- **跨层执行证据可回放**：模型、Agent、工作流、任务、产物和成长记忆的合同与测试集中进入同一版本基线。

## User-facing Highlights (en)

- **Unified node capability and Agent execution contracts**: node capabilities now expose real inputs, outputs, side effects, child-node behavior, and execution routes.
- **Video-node actions return real receipts**: first/last/current-frame capture, clipping, and subtitle removal use the browser bridge and work with referenced upstream videos.
- **Unwired capabilities stay honest**: depth motion capture is explicitly marked as not wired instead of being presented as a finished production feature.
- **Replayable cross-layer evidence**: model, Agent, workflow, task, artifact, and growth-memory contracts ship from one versioned baseline.

## Bug Fixes

- 修复能力目录与实际节点操作之间只有文字描述、缺少执行映射和回执的问题。
- 修复运行版长期停留在 dirty 构建、无法与当前源码版本对账的问题。

---
version: 1.1.23
attention: medium
---
# v1.1.23

## User-facing Highlights (zh)

- **模型能力按真实可运行状态统一**：模型中心、节点选择器和 Agent 目录现在共享同一套 readiness 合同，未完成探测或未准备好的模型不会被误显示为可用。
- **视频尺寸合同不再复活旧预设**：上游明确返回空尺寸能力时保持空值，避免模型层把历史尺寸重新注入请求。
- **成长记忆与生产资产可追溯**：成长蒸馏 smoke 覆盖跨项目召回、证据晋升和负向撤销；参考资产获得稳定 AssetPassport、哈希、尺寸、来源和身份锁摘要。
- **恢复与交付门禁收口**：视频结果恢复、持久队列和最终交付质检回归通过，尺寸比例、代表帧、视觉连续性和正式合成门按合同阻断。

## User-facing Highlights (en)

- **One runtime-readiness contract for models**: the model center, node selectors, and Agent catalog now share the same readiness rules, so unprobed or unavailable models are not presented as executable.
- **Video size capabilities stay truthful**: an explicit empty upstream size list remains empty instead of reviving legacy preset sizes.
- **Traceable growth memory and production assets**: the distillation smoke covers cross-project recall, evidence promotion, and negative revocation; reference assets carry stable passports, hashes, dimensions, provenance, and identity-lock summaries.
- **Recovery and delivery gates are closed**: video recovery, durable queue, and final-delivery regressions pass, while dimensions, representative frames, visual continuity, and final composition gates block on missing evidence.

## Bug Fixes

- 补齐新增生产元数据文件的许可证清单登记，保持全量合规门禁可复现。

---
version: 1.1.22
attention: medium
---
# v1.1.22

## User-facing Highlights (zh)

- **实战生成默认开启**：Freezone 新会话默认进入实战生成模式，结构、参数和依赖校验通过后可直接提交真实图片、视频和音频任务。
- **费用语义判定修正**：只有明确的“不要花钱/只检查/不生成”等请求才进入费用保护；“允许付费生成/可以实践”不会再被误判为拒绝。
- **草稿仍可显式保留**：需要只搭结构时可切回草稿模式，本轮不会启动媒体任务。

## User-facing Highlights (en)

- **Real execution is the default**: new Freezone sessions use real execution by default and can submit image, video, and audio tasks after structure, parameters, and dependencies pass validation.
- **Correct cost-intent semantics**: only explicit no-spend or analysis-only requests enable spend protection; explicit permission to pay or practice is no longer blocked.
- **Draft remains an explicit opt-out**: switch to draft mode when you only want structure and no media task starts.

## Bug Fixes

- 修复“付费”单独出现就触发费用保护的问题。
- 统一运行模式默认值、导演台授权状态和 V2 Agent 请求合同。

---
version: 1.1.21
attention: medium
---
# v1.1.21

## User-facing Highlights (zh)

- **工作流执行语义统一**：每个步骤现在都携带副作用、重试安全、执行模式、结果查找、恢复方式和失败阶段合同，运行创建时冻结到持久化状态。
- **付费媒体恢复更可靠**：媒体步骤明确要求幂等身份并通过 provider receipt 对账，重试入口会拒绝不安全的重复执行。
- **参考项目能力炼化为本地合同**：吸收 TapCanvas 的执行语义，而不引入第二套运行时或污染现有工作流。

## User-facing Highlights (en)

- **Unified workflow execution semantics**: every step now declares side effects, retry safety, execution mode, result lookup, recovery mode, and failure stage, frozen into the durable run state at creation.
- **More reliable paid-media recovery**: media steps require an idempotent identity and provider-receipt reconciliation; the retry gate rejects unsafe duplicate execution.
- **Distilled reference-project capability**: TapCanvas execution semantics are absorbed as a local contract without introducing a second runtime.

## Bug Fixes

- 为旧运行提供保守语义回退，升级后仍可读取和恢复历史工作流。
- 工作流定义校验现在会在运行前拦截不一致的付费生成和重试声明。

---
version: 1.1.20
attention: medium
---
# v1.1.20

## User-facing Highlights (zh)

- **GPT Image 2 提示词更懂视觉任务**：接入 541 个真实案例和 22 套结构模板，按商品、海报、角色、场景等意图召回有限参考，帮助优化器组织主体、构图、材质、版式和限制条件。
- **外部案例按模型能力精准隔离**：只有明确的 GPT Image 2 / image-2 图片模型会获得该案例上下文，其他图片模型和全部视频模型保持原有提示词合同。
- **来源和许可证可追溯**：上游仓库、commit、MIT 许可证和本地快照均登记，案例只作为参考材料，不成为运行时依赖。

## User-facing Highlights (en)

- **Better GPT Image 2 prompt craft**: 541 real examples and 22 structured templates are retrieved by visual intent to improve subject, composition, material, typography, and constraint coverage.
- **Model-scoped external references**: The corpus is injected only for explicit GPT Image 2 / image-2 image targets; other image models and all video models keep their existing prompt contracts.
- **Traceable provenance**: The upstream repository, commit, MIT license, and local snapshot are recorded; examples remain bounded references rather than runtime dependencies.

## Bug Fixes

- 收紧 GPT Image 2 识别条件，避免把泛 `openai` 或其他供应商名称误当成目标模型。
- 修正合规生成器的第三方许可证分组，确保 awesome-gpt-image-2 使用独立版权归属。

---
version: 1.1.19
attention: low
---
# v1.1.19

## User-facing Highlights (zh)

- **缩放回收不再误判暂停**：视频的播放/暂停意图由用户操作记录，画布回收播放器时只保存播放头，重挂载后按原意图恢复。

## User-facing Highlights (en)

- **LOD remounts no longer look like a user pause**: Playback intent comes from explicit player actions while remount cleanup only snapshots the playhead for restoration.

## Bug Fixes

- 修复低细节可见性回收 `<video>` 触发暂停事件后清掉活动标记，导致恢复分支无法执行的问题。
- 保持结束事件自动清理活动状态，避免已播完视频永久占用实时播放器。

---
version: 1.1.18
attention: low
---
# v1.1.18

## User-facing Highlights (zh)

- **视频缩放后继续播放**：即使画布低细节切换回收并重挂载视频节点，也会恢复上一次播放位置并继续播放，不再跳回 0 秒。

## User-facing Highlights (en)

- **Video playback resumes after LOD remounts**: The last playhead and active playback intent are restored when a canvas visibility pass remounts a video node.

## Bug Fixes

- 修复 React Flow 低细节可见性回收视频元素时活动状态和播放头丢失的问题。
- 活动媒体注册表现在保存当前播放时间；重新挂载后在元数据就绪时恢复位置并续播。

---
version: 1.1.17
attention: low
---
# v1.1.17

## User-facing Highlights (zh)

- **播放中的视频缩放不丢进度**：低缩放切换现在读取同步的活动媒体状态，播放中的视频始终保留原生播放器，不会被首帧缩略图替换。

## User-facing Highlights (en)

- **Active video playback survives LOD changes**: Low-detail rendering now reads the synchronous media activity registry, keeping the native player mounted while a video is playing.

## Bug Fixes

- 修复视频播放事件尚未完成 React 状态提交时缩放，导致 `<video>` 被替换为首帧图片并回到 0 秒的问题。
- 增加视频 LOD 播放合同测试，覆盖同步活动状态、播放器源稳定性和原生播放头归属。

---
version: 1.1.16
attention: low
---
# v1.1.16

## User-facing Highlights (zh)

- **视频播放与缩放彻底解耦**：视频节点自己管理低缩放首帧和实时播放器，缩放画布时不会再重置正在播放的视频。

## User-facing Highlights (en)

- **Playback stays stable while zooming**: Video nodes own their low-detail still and live-player transition, so zooming no longer resets active playback.

## Bug Fixes

- 修复外层低细节节点外壳与视频播放状态竞态导致的播放中断和进度丢失。

---
version: 1.1.15
attention: low
---
# v1.1.15

## User-facing Highlights (zh)

- **大画布低缩放更流畅**：图片在低缩放阶段使用项目级缩略图缓存，视频使用后台生成的轻量首帧，减少大量节点同时渲染时的卡顿。
- **媒体显示与原片分离**：画布只降低显示层负载，原图、原视频、下载和生成输入继续使用原始媒体，不损失产物质量。
- **视频播放不被缩放打断**：正在播放的视频保持真实播放器，不会因为缩放进入低细节外壳而重置播放进度。

## User-facing Highlights (en)

- **Smoother dense-canvas zooming**: Images use project-scoped thumbnails at low zoom and videos use lightweight background-captured stills, reducing rendering work across large node graphs.
- **Display-only media reduction**: The canvas lowers display cost while original images, videos, downloads, and generation inputs remain unchanged.
- **Playback survives zooming**: A playing video keeps its live player instead of being replaced by a low-detail shell that resets the playhead.

## Bug Fixes

- 修复低缩放图片仍直接解码大原图、导致主线程和显存压力随节点数量线性增长的问题。
- 修复项目静态媒体 URL 在缩略图请求中解析不到正确项目目录的问题。
- 修复冷缩略图请求阻塞事件循环的问题：首次请求立即回退原图并在后台有界预热。

---
version: 1.1.14
attention: low
---
# v1.1.14

## User-facing Highlights (zh)

- **低缩放画布更顺滑**：画布平移和缩放时使用轻量节点外壳，减少大图、视频和复杂节点在手势期间的渲染压力。
- **画布参考拾取**：图片生成与视频节点可以直接从画布选择、添加或移除真实参考边，引用会沿现有提交链路传递到模型。
- **模型能力按真实合同展示**：尺寸和媒体能力来自已确认的上游模型合同；未探测完成的协议不会被伪装成可用能力。

## Bug Fixes

- 修复低缩放时可视节点频繁挂载导致的卡顿和帧率抖动。
- 修复图片/视频节点参考素材只能依赖隐式连接、难以操作的问题。
- 修复未确认视频协议被默认当作 OpenAI 视频协议的问题。

---
version: 1.1.13
attention: low
---
# v1.1.13

## User-facing Highlights (zh)

- **AutoDL ComfyUI 视频协议接入**：模型中心支持发现 AutoDL 工作流、保存工作流 ID，并按上游协议提交与查询视频任务。
- **视频能力映射完整**：任务 runner 会传递分辨率、音频和参考素材，AutoDL 的横竖屏分辨率按画幅自动映射，避免前端选择与上游请求脱节。
- **上游失败信息可诊断**：保留脱敏后的响应码、消息、数据字段和请求合同，区分提交拒绝、查询失败与结果缺失。

## User-facing Highlights (en)

- **AutoDL ComfyUI video protocol**: The model center can discover AutoDL workflows, persist workflow IDs, and submit and query tasks through the provider contract.
- **Complete video capability mapping**: Resolution, audio, and reference media now flow through the task runner, with AutoDL portrait/landscape resolution selected from the requested aspect ratio.
- **Actionable upstream diagnostics**: Redacted response codes, messages, data keys, and request contracts are retained to distinguish submit rejection, query failure, and missing results.

## Bug Fixes

- 修复直连视频模型在 AutoDL 地址下被错误识别为通用或 OpenAI 协议的问题。
- 修复 AutoDL 本地参考素材仍被编码为 data URL、导致上游无法读取的问题；现在会通过已配置的媒体中转生成可访问 URL。
- 修复模型能力缓存中的供应商方向分辨率污染共享能力合同的问题，同时保留供应商原生选项供提交映射使用。

---
version: 1.1.12
attention: low
---
# v1.1.12

## User-facing Highlights (zh)

- **澄清真正按任务自适应**：聊天 Agent 只在当前任务明确要求且确实缺失时追问，不再把受众、风格、画幅、声音组成固定面试清单。
- **旧澄清历史不再回放成题库**：历史会话只保留当前有效的一条问题，已经进入真实执行的旧问题自动隐藏。

## Bug Fixes

- 修复旧版本遗留的多条 `director-preflight` 消息在新会话中同时显示的问题。
- 修复 `audio_required` 被误当成已填写音频方案的问题。

---
version: 1.1.11
attention: low
---
# v1.1.11

## User-facing Highlights (zh)

- **导演澄清严格一次一问**：移除后续问题队列的传输与回放，旧会话中的预设问题不会再同时出现在 Agent 界面。

## User-facing Highlights (en)

- **Strict one-question director clarification**: follow-up question queues are no longer transported or replayed, so preset questions from older sessions cannot surface together in the Agent UI.

## Bug Fixes

- 澄清事件、API 发布和旧事件恢复统一丢弃 `next_questions` 与旧建议答案字段；当前轮只保留真实的一个阻塞问题。

# v1.1.10

## User-facing Highlights (zh)

- **成长记忆改为真实蒸馏闭环**: “记住这个”和执行反馈先保存为可追溯学习事件，由专用成长模型提炼触发条件、动作、槽位和验收标准；未经过蒸馏与执行证据的原话不会直接变成正式规则。
- **Agent 欢迎入口回归模型理解**: 移除固定创作预设，避免用户意图尚未澄清就套用模板或工作流。
- **待整理状态更诚实**: 蒸馏中的 `pending`、`processing` 和可重试事件会持续显示，模型失败不会被误报为没有待处理记忆。

## User-facing Highlights (en)

- **Growth memory now uses a real distillation loop**: teaching phrases and execution feedback are stored as traceable learning events, distilled by the dedicated growth model into triggers, actions, slots, and acceptance checks, and kept out of formal rules until evidence exists.
- **Model-led Agent entry**: fixed creative presets are removed so the Agent interprets intent before choosing a workflow.
- **Honest pending state**: pending, processing, and retryable distillation events remain observable instead of being reported as an empty queue after a model failure.

## Bug Fixes

- Feedback corrections no longer bypass the growth model by creating a searchable candidate directly.
- Manual memory input now returns an accepted learning event (`202`) and is drained by the same outbox worker as conversational learning.
- Growth queue statistics include retryable and processing events, preventing silent loss of learning work in the UI.

# v1.1.9

## User-facing Highlights (zh)

- **已有节点优化闭环**: Agent 现在先绑定真实画布 revision 和目标节点，再以最小命令更新；服务端返回持久 receipt，并对权威画布回读验证后才报告完成。
- **成长记忆获得执行证据**: 每次画布写入会记录命令、目标节点、前后 revision 和回读结果，区分真实执行、失败与仅讨论，避免把模型口头回复当成学习成果。

## User-facing Highlights (en)

- **Verified existing-node edits**: The Agent binds the authoritative canvas revision and target nodes before applying the smallest update, then reports completion only after a durable receipt and readback verification.
- **Execution evidence for growth memory**: Each canvas write records the command, targets, before/after revisions, and readback result so learning can distinguish real execution from failures or discussion-only turns.

## Bug Fixes

- 复用已有节点的写入缺少 revision 或目标绑定时现在会被 Gateway 明确拦截，避免静默覆盖或误写其他节点。
- 画布命令回执现在包含可持久化的回读验证结果，Agent 只接受带真实服务端证据的成功状态。

# v1.1.5

## User-facing Highlights (zh)

- **故事 Agent · 故事工坊**: 镜头工艺新增从一句创意生成故事圣经、大纲、分集/分章、完整成稿与质量审校的原生工作流，成稿可直接送入项目素材进入知识图谱和后续制片。
- **视频素材工作流更完整**: 视频节点可一次导入多张图片、视频和音频,并按模型能力提供全能参考、图片参考和首尾帧入口,提交前会明确拦截不支持的素材与越界音频。
- **画布查找与保存更可靠**: 历史资产支持按提示词和名称搜索,并修复自动保存并发造成的虚假 409 冲突。
- **小说导入可安全重建**: 知识图谱构建会识别上游失败、空图和不完整结果,失败后可复用原小说安全重试或重建。
- **登录与设置体验更稳定**: 登录过期后会停止重复请求并正确返回登录页,媒体存储凭据支持只更新需要变更的字段,关键界面的中英文显示也更完整。

## User-facing Highlights (en)

- **Story Lab authoring**: Build a story bible, outline, episodes or chapters, complete draft, and production audit from one idea, then publish the approved draft into the existing ingest and production pipeline.
- **More complete video reference workflows**: Import multiple images, videos, and audio clips directly into a video node, choose model-appropriate reference modes, and catch unsupported media or invalid audio durations before submission.
- **Faster asset discovery and safer saves**: Search generation history by prompt or name, while serialized canvas saves prevent false 409 conflicts caused by overlapping autosaves.
- **Safe novel import rebuilds**: Knowledge graph imports now detect provider failures, empty graphs, and incomplete runs, then reuse the original novel for a bounded retry or confirmed rebuild.
- **More reliable sessions and settings**: Expired sessions stop repeated background requests and return to sign-in, media credentials support partial updates, and key screens provide more complete Chinese and English localization.

## New Features

- 镜头工艺新增「故事 Agent · 故事工坊」，复用项目任务、模型中转、E 盘资产和项目素材知识图谱链路，不引入第二数据库或向量库。
- 历史资产支持按提示词和名称搜索,并提供跨分类命中提示 (#178).
- 视频节点支持一次选择多张本地图片、视频和音频,自动创建上游素材节点和分组 (#181).

## Bug Fixes

- 修复画布自动保存并发导致的虚假版本冲突和跨画布误写风险 (#179).
- 修复 Seedance 1.x 静默忽略视频、音频或多图素材的问题,提交前会给出明确原因 (#187).
- 修复 Seedance 2.0 音频参考时长越界后才由厂商返回错误的问题 (#196).
- 修复登录过期后任务流和后台请求持续重试,以及无效 cookie 无法清理的问题 (Fixes #197, #198).
- 修复 Freezone AI 摆件在发送模型请求前因失效导入而失败的问题 (#199).
- 修复知识图谱构建失败被误报成功,并支持复用原小说安全重试和重建 (#200).

## Improvements

- 视频空态入口按模型能力展示全能参考、图片参考和首尾帧等可用模式 (#185).
- OSS 与 Cloudinary 媒体存储凭据支持部分更新,无需重复填写整组配置 (Fixes #182, #183).
- 补齐角色统计、风格、Beat 工作台、分享弹窗和村长画布等关键界面的中英文文案 (#191).

## 架构说明

- 项目级 Cognee Embedding 已按模型、网关和向量维度隔离，避免多项目并发时串模型或串向量空间。
- 视频模型能力继续由 `videoCapabilityCompiler` 统一管理，并在其上接入外部素材按钮，避免形成两套能力真相源。
- 保留云飞媒体中转、55 种风格模板、Agent 实时画布联动和项目资产目录约束。
- EE 专属“更多信息”菜单在当前 CE 本地运行模式下不启用，避免增加无效登录页体积和维护面。

---
version: 1.1.41
attention: low
---
# v1.1.41

- 移除默认导演澄清中的“受众 / 发布平台 / 内部样片”固定问题；只有任务契约明确要求时才保留受众约束。
- 视觉风格、画幅、角色参考和声音等真正影响当前创作执行的字段继续按实际缺口追问。
- 更新导演澄清回归测试，确认新视频请求在主体确认后直接进入视觉风格判断，不再弹出无效预设。
