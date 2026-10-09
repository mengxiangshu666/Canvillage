# ADR: G-01 分镜阶段引用 Filmcraft 规则的跨域边界

- 状态：accepted（**2026-09-26 第七版：按 G-01-R9 实际落地的代码修订**）
- 日期：2026-09-26
- 决策范围：`workflow_runtime` 的分镜编译器引用 `production.filmcraft_kb` 规则；`action_context` 触发条件的误报修复（G-01-R4）、决策集中化（G-01-R5）、小句级否定／词表补齐／门面调用统一／文本截断共享（G-01-R6）、否定附着判定／`、` 列举语义（G-01-R7）、直接禁止的脚手架分类／列举项描述语（G-01-R8）与词条最长匹配／无介词场景框架（G-01-R9）
- 本文件是本仓 `docs/arch/` 下的第一份 ADR（该目录此前不存在）

> **第二版说明（必读）**：初版写于动手之前，选的是「新增 `services/filmcraft_rules.py` 门面 +
> `render_filmcraft_rules_block` + `services/__init__.py` 导出 + 清单登记 + T4 守卫测试」。
> 实际落地的不是这套：门面沿用**已有的** `src/novelvideo/services/production_contracts.py`，
> 没有新文件、没有 `render_filmcraft_rules_block`、没有 `services/__init__.py` 改动、
> 没有 `architecture_manifest.json` 登记、没有 T4 守卫测试。第二版按实况重写；初版的这些
> 主张要么标为 **已否决**，要么标为 **未实施（deferred）**，不再当作已发生的事实陈述。
>
> **第三版说明（必读）**：独立评审在 R4 之后又指出四个问题——(1) 收窄词表把
> `动作片/武打动作/摔跤/对决/扭打` 这类明确打戏措辞从旧调用方手里删掉了；(2) 纯子串匹配
> 把「全片不要打斗，也不要追逐」当成打戏请求；(3) `filmcraft_kb` 与 `storyboard_prompt`
> 各存一份 `_ACTION_CONTEXT_CUES`，没有防漂移机制；(4) `params={"action_context": "false"}`
> 因为 `bool("false")` 为真而被当成「要打戏」。R5 把决策收到 production 一处、经门面暴露，
> 并只认真正的 bool。**第二版里「两份拷贝」「R11 漂移风险仍在」的说法在本版作废**，
> 已改为门面路线 + 行为级漂移守卫测试。
>
> **第四版说明（必读）**：独立评审在 R5 之后又指出六个问题——(1) 否定沿用「词前 8 字符扁平窗口」，
> 让否定跨逗号压掉下一小句，`不要温情，要打斗` 判 False；(2) 同一个扁平窗口又漏掉
> `全片不得出现打斗` 这类否定，判 True；(3) R4 收窄词表时砍掉的明确打斗措辞没有补全，
> `摔打/肉搏/对打/打起来/开打/群殴/决战/交战` 八个词条全部漏报；(4) `storyboard_prompt.py`
> 用 `from ... import inject_filmcraft_rules` 在 import 期绑定函数对象，而动作决策走模块属性，
> 改门面属性对分镜注入无效，两条路径不一致；(5) 门面决策扫全文，规则选择走
> `_text(..., limit=12_000)`，12,000 字之后的词条会让两者分歧；(6) 本 ADR 第三版的
> 「executor 只读未改」与工作树实况不符。R6 逐条修掉，并补 52 条新增用例（含参数化展开）。
> **第三版里「否定窗口有界且保守」的说法在本版作废**，改为小句级作用域 + 统一门面调用路径
> + 共享 12,000 字截断。
>
> **第五版说明（必读）**：独立评审在 R6 之后又指出三个问题——(1) R6 只把小句当作用域边界，
> 却没问「否定到底在否定谁」，于是「没有台词的三镜打斗」「避免煽情的三镜打斗」
> 「不做慢动作的肉搏戏」「没有武器的两人对打」「没有台词的巷口决战」「不要温情，没有台词的打斗」
> 六个**明确要打**的请求全被判成禁止打斗；(2) 同一个「同小句即生效」的规则又把 `、` 当成小句边界，
> 于是「全片不要打斗、追逐」和「不要出现打斗、爆炸、群殴」两个**纯禁止**被判成打斗请求；
> (3) 本 ADR 第四版没有记录这两类输入的实测读数与 R7 的状态。R7 逐条修掉并补回归用例。
> **第四版里「同小句内的否定一律压掉词条」的说法在本版作废**：否定改为
> 「小句作用域 + 附着判定 + 列举感知」三条同时成立才压词条。
>
> **第六版说明（必读）**：独立评审在 R7 之后复现了两类**漏判**（都是纯禁止被判成打斗请求）——
> (1) R7 把内容名词与方位字（`画面/镜头/场面/里/中`）一起放进全局桥接词表，于是
> `不要中景打斗`、`不要特写打斗`、`不要在画面里打斗`、`不要在画面里出现打斗` 四条纯禁止
> 全判 `True`；(2) R7 的列举判定要求**最后一项**也只有功能词，于是
> `不要出现打斗、爆炸、两人群殴`、`不要出现打斗、爆炸、多人群殴`、`全片不要打斗、三人追逐`
> 三条带数量词的列举禁止漏判成 `True`。R8 逐条修掉并补 22 条回归用例。
> **第五版里「否定与词条之间只有功能词才算附着（`画面/里/中` 属于功能词）」的说法在本版作废**：
> 桥接词表只保留功能词，`在…里/中` 的介词框架、景别词（`中景/特写`）与数量词
> （`两人/多人/三人`）改为**结构性**判据；列举判定改为看**前面各项是不是已列举的词条**，
> 而不是看最后一项的措辞。
>
> **第七版说明（必读）**：独立评审在 R8 之后又复现了**两类**误报（仍是纯禁止被判成打斗请求）——
> (1) `_strip_tokens` 按元组顺序替换，短词先吃掉长词的前半截：`特写` 先于 `大特写`、`近景`
> 先于 `中近景`，残留的 `大`/`中` 被当成实词，于是 `不要大特写打斗`、`不要中近景打斗`
> 判 `True`；同样地，列举项 `武打动作` 被 `武打` 削成 `动作`（`动作` 已不在词表里），
> 于是 `不要打斗、武打动作、三人群殴` 判 `True`；
> (2) R8 只认 `在…里` 这种**带介词**的框架，`不要镜头里出现打斗`、`不要画面里出现打斗`、
> `不要场面打斗` 三条**无介词**场景框架漏判成 `True`。R9 逐条修掉并补 31 条回归用例。
> **第六版里「短词表按书写顺序替换是安全的」与「框架必须有介词」两条说法在本版作废**：
> 词条删除一律**最长匹配**（等长保持表内顺序），框架在原有的介词框架之外再补一类
> **有界无介词场景框架**（场景名词 + 可选方位后缀 + 可选 `出现/有`，封顶 5 字）。

## Context

### 这次变更实际做了什么（第三版实况，行数是 R5 落地时的快照）

> **行数已过期**：下表是 R5 当时的读数。R6 之后的读数见「第四版实况（R6）」，
> R7 之后的读数见「第五版实况（R7）」（最新）。第三版把 `executor.py` 写成「未改」是漂移，
> 见第四版表的更正。

| 文件 | 状态 | 实况 |
| --- | --- | --- |
| `src/novelvideo/production/filmcraft_kb.py` | M，695 行（R5 时） | 唯一权威词表 `_ACTION_CONTEXT_CUES`（R5 时 `:30-61`，29 个短语，新增 `动作片/动作短片/动作剧/武打/武打动作/交手/对决/扭打/摔跤/混战/厮杀`）；否定窗口 `_NEGATION_WINDOW=8` 与 `_ACTION_CONTEXT_NEGATIONS`、`别` 的非否定前缀例外；`_negated_action_cue`、`_has_filmcraft_action_context`；`_rule_applies` 的 `action_context` 分支改为「只有真 bool 才算决策」 |
| `src/novelvideo/services/production_contracts.py` | M，375 行（R5 时） | `:181-194` 新增懒转发 `has_filmcraft_action_context(source_text) -> bool`（函数体内 `from novelvideo.production.filmcraft_kb import _has_filmcraft_action_context`），名字进 `__all__`（`:359`）；`inject_filmcraft_rules` 转发仍在 `:150-178` |
| `src/novelvideo/workflow_runtime/storyboard_prompt.py` | **未跟踪的新文件**，74 行（R5 时） | 本地 `_ACTION_CONTEXT_CUES` 已删除；`_action_context`（`:25-34`）改调门面（`:34`，按模块属性调用）。`build_storyboard_filmcraft_contract` 签名不变，`params["action_context"]` 仍是显式 bool（`:53`）。**该文件在 `git status` 里是 `??`（untracked），不是已跟踪文件的修改** |
| `src/novelvideo/workflow_runtime/executor.py` | M，3000 行 | **更正第三版**：相对 HEAD 它带着 R4/R5 的改动（`git diff --numstat` = 2 增 1 删：新增 `:96` 的 `build_storyboard_filmcraft_contract` 导入、改写 `:701-702` 的 `system_prompt` 字面量并内联调用）。R6 **没有再动它**，它仍是 3000 行。第三版写成「未改」与工作树实况不符 |
| `tests/test_filmcraft_kb.py` | M，431 行（R5 时） | 新增否决/召回/非 bool/漂移守卫测试（见下） |
| `docs/arch/g01-filmcraft-workflow-boundary.md` | M | 本文件（第三版） |

R5 的写入面是五个文件：`filmcraft_kb.py`、`production_contracts.py`、
`storyboard_prompt.py`、`tests/test_filmcraft_kb.py`、本 ADR。其余文件（含 `canvas_reads.py`、
画布侧 `freezone/prompt_optimizer.py` 与 `freezone/text_node.py`）R5 只读未改。

### 这次变更实际做了什么（第四版实况，R6）

| 文件 | 状态（R6 之后实测） | 实况 |
| --- | --- | --- |
| `src/novelvideo/production/filmcraft_kb.py` | M，**767 行** | 词表 `_ACTION_CONTEXT_CUES`（`:33-71`，**37 条**：在 R5 的 29 条上补回 `摔打/肉搏/对打/打起来/开打/群殴/决战/交战`）；`_CLAUSE_BOUNDARIES`（`:80-93`，12 个小句边界）+ `_ACTION_CONTEXT_NEGATIONS`（`:94-119`，24 条，新增 `不得/严禁/不可/不准/不许/不出现/不再/切勿/莫`）；`_NON_NEGATING_PREFIXES`（`:124`）保留；`ACTION_CONTEXT_TEXT_LIMIT = 12_000`（`:459`）与公开 helper `normalize_action_context_text`（`:462`）；小句级 `_negated_action_cue`（`:493`）；`_has_filmcraft_action_context`（`:521`，改收 `object` 并自己归一）；`inject_filmcraft_rules`（`:721`）改用同一个归一 helper；`__all__`（`:761`）加两个公开名 |
| `src/novelvideo/services/production_contracts.py` | M，**378 行** | 函数体签名/转发不变（`inject_filmcraft_rules` `:150`、`has_filmcraft_action_context` `:181`）；R6 只改 `:181` 的 docstring——原先写「否定窗口」，改写成「小句级否定 + 与注入同源的 12,000 字归一」。`__all__`（`:352`）未动 |
| `src/novelvideo/workflow_runtime/storyboard_prompt.py` | **untracked 新文件**，**77 行** | 删除 `from novelvideo.services.production_contracts import inject_filmcraft_rules`（import 期绑定）；注入改为按模块属性调用 `production_contracts.inject_filmcraft_rules(...)`（`:59`），与 `:33` 的决策调用同一条门面路径 |
| `src/novelvideo/workflow_runtime/executor.py` | **M（R4/R5 遗留 diff，R6 未动）**，3000 行 | `:96` 导入、`:701-702` 调用；`check_file_sizes` 不报它 |
| `tests/test_filmcraft_kb.py` | M，**670 行** | R5 的 10 组测试全部保留；R6 新增 52 条用例（含参数化展开，见「Tests」一节）；**唯一一处既有断言改动**是把 `不要打斗，要看追逐` 从 `False` 更正为 `True`（R6 明确要求的小句语义，不是放宽） |
| `docs/arch/g01-filmcraft-workflow-boundary.md` | M | 本文件（第四版，414 行） |

R6 的写入面严格等于工作单允许的五个文件，且**没有**碰 `executor.py`（行数仍是 3000）、
`canvas_reads.py`、`freezone/prompt_optimizer.py`、`freezone/text_node.py`、任何 task/backend/frontend
文件、架构配置/基线/测试、`scripts/acceptance/tour_8784.py`。

### 这次变更实际做了什么（第五版实况，R7）

| 文件 | 状态（R7 之后实测） | 实况 |
| --- | --- | --- |
| `src/novelvideo/production/filmcraft_kb.py` | M，**933 行** | 词表 `_ACTION_CONTEXT_CUES` 未动（`:33-71`，仍是 R6 的 37 条）；`_CLAUSE_BOUNDARIES` **去掉 `、`**（`:88-100`，11 个小句边界）；新增 `_ENUMERATION_SEPARATORS = ("、",)`（`:102`）、`_NEGATION_BRIDGE_TOKENS`（`:111-146`，桥接/功能词白名单）、`_AFFIRMATIVE_CONTEXT_MARKERS`（`:148-152`）；`_negated_action_cue`（`:657`）改判「最新否定 + 附着」；新增私有 helper `_latest_negation`（`:553`）、`_strip_tokens`（`:586`）、`_only_bridge_tokens`（`:604`）、`_only_bridge_tokens_or_cues`（`:612`）、`_has_affirmative_context_marker`（`:626`）、`_negation_attached`（`:630`） |
| `src/novelvideo/services/production_contracts.py` | M，**378 行，R7 未改** | 文件 mtime 18:55，早于本次会话（19:12 起）；diff 内容仍是 R5 的门面 + R6 的 docstring，本次一个字都没动 |
| `src/novelvideo/workflow_runtime/storyboard_prompt.py` | **untracked 新文件**，77 行，**R7 未改** | 决策/注入仍走 `production_contracts.` 模块属性；R7 没有碰它 |
| `src/novelvideo/workflow_runtime/executor.py` | **M（R4/R5 遗留 diff，R7 未动）**，3000 行 | mtime 18:00；`check_file_sizes` 仍不报它 |
| `tests/test_filmcraft_kb.py` | M，**804 行** | R5/R6 的 80 条用例一条未改、一条未删；R7 追加 26 条（含参数化展开，见「Tests」一节），单文件 106 passed |
| `docs/arch/g01-filmcraft-workflow-boundary.md` | M | 本文件（第五版） |

R7 的写入面严格等于工作单允许的三个文件（`filmcraft_kb.py`、`tests/test_filmcraft_kb.py`、本 ADR），
且**没有**碰 `production_contracts.py`、`storyboard_prompt.py`、`executor.py`、任何 freezone 调用方、
架构配置/基线/测试、`canvas_reads.py`、`scripts/acceptance/tour_8784.py`。

### 这次变更实际做了什么（第六版实况，R8）

| 文件 | 状态（R8 之后实测） | 实况 |
| --- | --- | --- |
| `src/novelvideo/production/filmcraft_kb.py` | M，**1038 行**（R7 时 933 行） | 词表 `_ACTION_CONTEXT_CUES` 未动（`:33-71`，仍是 37 条）；否定短语 `_ACTION_CONTEXT_NEGATIONS` 未动（`:201`，仍是 24 条）；`_CLAUSE_BOUNDARIES`（`:97`）与 `_ENUMERATION_SEPARATORS`（`:111`）未动；`_NEGATION_BRIDGE_TOKENS`（`:129`）**删掉 `画面/镜头/场面/里/中`**，只剩功能词；新增 `_NEGATION_FRAME_PREPOSITIONS`／`_NEGATION_FRAME_SUFFIXES`／`_NEGATION_FRAME_MAX_ADJUNCT`（`:164-166`）与 `_NEGATION_CUE_ADJUNCT_TOKENS`（`:173`）；新增 helper `_strip_locative_frames`（`:651`）、`_strip_negation_scaffolding`（`:682`）；改写 `_only_bridge_tokens`（`:693`）、`_only_bridge_tokens_or_cues`（`:708`）、`_negation_attached`（`:727`）的列举分支 |
| `src/novelvideo/services/production_contracts.py` | M，**378 行，R8 未改** | 门面 `has_filmcraft_action_context`（`:181`）仍只做懒加载转发；本次一个字都没动 |
| `src/novelvideo/workflow_runtime/storyboard_prompt.py` | **untracked 新文件**，77 行，**R8 未改** | 决策/注入仍走 `production_contracts.` 模块属性 |
| `src/novelvideo/workflow_runtime/executor.py` | **M（R4/R5 遗留 diff，R8 未动）**，3000 行 | `check_file_sizes` 仍不报它（本轮实跑确认它不在 finding 列表） |
| `tests/test_filmcraft_kb.py` | M，**954 行**（R7 时 804 行） | R5/R6/R7 的 106 条用例一条未改、一条未删；R8 追加 22 条（含参数化展开，见「Tests」一节），单文件 128 passed |
| `docs/arch/g01-filmcraft-workflow-boundary.md` | M | 本文件（第六版） |

R8 的写入面严格等于工作单允许的三个文件（`filmcraft_kb.py`、`tests/test_filmcraft_kb.py`、本 ADR），
且**没有**碰 `production_contracts.py`、`storyboard_prompt.py`、`executor.py`、任何 freezone 调用方、
架构配置/基线/测试、`canvas_reads.py`、`scripts/acceptance/tour_8784.py`；没有新增依赖、没有重启 8784、
没有触发任何模型生成、没有提交或推送。

### 这次变更实际做了什么（第七版实况，R9）

| 文件 | 状态（R9 之后实测） | 实况 |
| --- | --- | --- |
| `src/novelvideo/production/filmcraft_kb.py` | M，**1137 行**（R8 时 1038 行） | 词表 `_ACTION_CONTEXT_CUES` 未动（`:33-71`，仍是 37 条）；否定短语 `_ACTION_CONTEXT_NEGATIONS` 未动（`:216`，仍是 24 条）；`_NEGATION_BRIDGE_TOKENS`（`:129`）**一个词都没加**（`画面/镜头/场面/里/中` 仍未在表里）；`_CLAUSE_BOUNDARIES`／`_ENUMERATION_SEPARATORS`／`_NEGATION_FRAME_*`／`_NEGATION_CUE_ADJUNCT_TOKENS` 全部未动；新增 `_NEGATION_SCENE_NOUNS`／`_NEGATION_SCENE_BARE_NOUNS`／`_NEGATION_SCENE_BRIDGE_WORDS`／`_NEGATION_SCENE_FRAME_MAX`（`:201-204`）；`_strip_tokens`（`:646`）改为**最长匹配**；新增 `_inside_listed_cue`（`:673`）、`_scene_frame_end`（`:691`）、`_strip_scene_frames`（`:723`）；`_strip_locative_frames`（`:737`）改为「介词框架 + 场景框架」，原介词循环抽成 `_strip_prepositional_frames`（`:755`，逐行未改） |
| `src/novelvideo/services/production_contracts.py` | M，**378 行，R9 未改** | 门面 `has_filmcraft_action_context`（`:181`）仍只做懒加载转发；本次一个字都没动 |
| `src/novelvideo/workflow_runtime/storyboard_prompt.py` | **untracked 新文件**，77 行，**R9 未改** | 决策/注入仍走 `production_contracts.` 模块属性 |
| `src/novelvideo/workflow_runtime/executor.py` | **M（R4/R5 遗留 diff，R9 未动）**，3000 行 | `check_file_sizes` 仍不报它（本轮实跑确认它不在 finding 列表） |
| `tests/test_filmcraft_kb.py` | M，**1110 行**（R8 时 954 行） | R5/R6/R7/R8 的 128 条用例一条未改、一条未删；R9 追加 31 条（含参数化展开，见「Tests」一节），单文件 159 passed |
| `docs/arch/g01-filmcraft-workflow-boundary.md` | M | 本文件（第七版） |

R9 的写入面严格等于工作单允许的三个文件（`filmcraft_kb.py`、`tests/test_filmcraft_kb.py`、本 ADR），
且**没有**碰 `production_contracts.py`、`storyboard_prompt.py`、`executor.py`、任何 freezone 调用方、
架构配置/基线/测试、`canvas_reads.py`、`scripts/acceptance/tour_8784.py`；没有新增依赖、没有新增 import、
没有重启 8784、没有触发任何模型生成、没有提交或推送。

### 工作树状态（R6 收工时的 `git status --short`，非推断）

```text
 M src/novelvideo/production/filmcraft_kb.py
 M src/novelvideo/services/production_contracts.py
 M src/novelvideo/workflow_runtime/executor.py          # R4/R5 遗留 diff，R6 未动
 M tests/test_filmcraft_kb.py
?? src/novelvideo/workflow_runtime/storyboard_prompt.py # 未跟踪的新文件
?? docs/arch/                                           # 未跟踪目录（含本 ADR）
```

相对 HEAD 的改动量（`git diff --numstat`）：

```text
214  3   src/novelvideo/production/filmcraft_kb.py     # R5 + R6 累计
 52  0   src/novelvideo/services/production_contracts.py # R5 的 49 行 + R6 的 3 行 docstring
  2  1   src/novelvideo/workflow_runtime/executor.py    # R4/R5，R6 未动
576  0   tests/test_filmcraft_kb.py                     # R5 的 337 行 + R6 的 239 行
```

**两条容易说错的**：`storyboard_prompt.py` 与 `docs/arch/` 是 **untracked**（`??`），
不是「已跟踪文件的修改」；`executor.py` 是 **M**，不是「未改」。
`git diff --check` 只覆盖已跟踪文件，因此**不覆盖** `storyboard_prompt.py`。

### 现场证据（R5 实跑，非推断）

```text
$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py tests/test_workflow_runtime.py tests/test_architecture_boundaries.py -q
128 passed in 18.21s
# R4 之后同一批是 109 passed；R5 新增 19 条（含参数化展开）后为 128 passed

$ .venv/Scripts/python.exe -m pytest tests/test_freezone_prompt_optimizer.py tests/test_freezone_script_contract.py -q
105 passed in 14.52s
# 画布侧旧调用方兼容性：这三处直连 production 的调用方行为不变

$ .venv/Scripts/python.exe -m ruff check src/novelvideo/production/filmcraft_kb.py src/novelvideo/services/production_contracts.py src/novelvideo/workflow_runtime/storyboard_prompt.py src/novelvideo/workflow_runtime/executor.py tests/test_filmcraft_kb.py
All checks passed!            # exit 0

$ .venv/Scripts/python.exe scripts/architecture/check_import_boundaries.py --fail-on high,medium
finding_count=0  severity_counts={}  files_scanned=660  import_edges=1821   # exit 0
# 边数从 1819 变 1821：storyboard_prompt 多了一条 novelvideo.services（模块级）导入边

$ .venv/Scripts/python.exe scripts/architecture/check_file_sizes.py --fail-on high,medium
finding_count=1  severity_counts={"high": 1}                              # exit 1
[high] unrecorded-large-file src/novelvideo/agent_tools/village_canvas/canvas_reads.py observed=3056 baseline=3000
# executor.py 不在 finding 列表里；wc -l src/novelvideo/workflow_runtime/executor.py = 3000

$ .venv/Scripts/python.exe -c "…行为直读…"
warm ordinary      -> False
warm negated       -> False
explicit action    -> True
legacy 动作片      -> True
legacy 摔跤        -> True
non-bool override 'false' -> False
non-bool override 'true' -> False
non-bool override 'no' -> False
non-bool override 0 -> False
non-bool override 1 -> False
explicit False + 打斗 -> False
explicit True  + warm -> True

$ git diff --check
exit 0
```

**不要把这组读数说成「门禁全绿」**：`check_file_sizes --fail-on high,medium` 仍是 exit 1，
原因仍是预存的 `canvas_reads.py` 3056 行——该文件 `git diff --stat` 为空，HEAD 版本即 3056 行，
最后一次改动是 `1ad4d9d fix(agent): let the canvas agent actually see project images`。
`tests/test_architecture_file_sizes.py` 仍会红那一条。`executor.py` 恰好 3000 行 = 阈值，
没有余量。

### 现场证据（R6 实跑，非推断）

```text
$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py tests/test_workflow_runtime.py tests/test_architecture_boundaries.py -q
180 passed in 19.21s
# R5 时同一批是 128 passed；R6 新增 52 条用例（含参数化展开），既有测试一组未删

$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py -q
80 passed in 1.86s

$ .venv/Scripts/python.exe -m pytest tests/test_freezone_prompt_optimizer.py tests/test_freezone_script_contract.py -q
105 passed in 14.34s
# 画布侧旧调用方兼容性：词表加词与否定改语义都没有改变这批调用方的行为

$ .venv/Scripts/python.exe -m pytest tests/test_combat_film_contract.py tests/test_creative_contract.py tests/test_director_clarification.py tests/test_global_video_optimizer_prompt_contract.py tests/test_prop_extraction_contract.py tests/test_workflow_production_plan.py tests/test_agent_events.py tests/test_canvas_template_store.py tests/test_memory_index.py -q
219 passed in 12.04s
# 相邻打斗/合同类测试文件（含用打斗措辞做输入的那些）全绿

$ .venv/Scripts/python.exe -m pytest tests/ -q -k "prompt or filmcraft or storyboard"
1 failed, 510 passed, 3 skipped, 4942 deselected in 44.05s
# 唯一失败是 tests/test_scene_reference_prompt.py::test_scene_reference_newapi_uses_normalized_gateway_base_url，
# 与本变更无关：它在本机因「未配置生图模型」抛 RuntimeError
# （_scene_image_model('master', None, None) 实测返回 ''），且该模块不导入 filmcraft_kb / storyboard_prompt

$ .venv/Scripts/python.exe -m ruff check src/novelvideo/production/filmcraft_kb.py src/novelvideo/services/production_contracts.py src/novelvideo/workflow_runtime/storyboard_prompt.py src/novelvideo/workflow_runtime/executor.py tests/test_filmcraft_kb.py
All checks passed!            # exit 0

$ .venv/Scripts/python.exe scripts/architecture/check_import_boundaries.py --fail-on high,medium
finding_count=0  severity_counts={}  files_scanned=660  import_edges=1820   # exit 0
# 边数从 R5 的 1821 回到 1820：storyboard_prompt 删掉了 import 期绑定的那条 from ... import 边

$ .venv/Scripts/python.exe scripts/architecture/check_file_sizes.py --fail-on high,medium
finding_count=1  severity_counts={"high": 1}                              # exit 1
[high] unrecorded-large-file src/novelvideo/agent_tools/village_canvas/canvas_reads.py observed=3056 baseline=3000
# executor.py 不在 finding 列表里；wc -l src/novelvideo/workflow_runtime/executor.py = 3000

$ .venv/Scripts/python.exe - <<'PY'   # 行为直读（工作单要求逐项打印）
== seven negated cases (must be False) ==
False  全片不要打斗，也不要追逐
False  全片不得出现打斗
False  全片严禁打斗
False  不可有打斗
False  不准打斗
False  不许打斗
False  不出现打斗
== affirmative after a negated clause (must be True) ==
True   不要温情，要打斗
True   不要慢动作，两人打斗
True   不要打斗，要看追逐
True   不要温情；第三镜两人追逐打斗
== warm ordinary (must be False) ==
False  三镜温情短片：母亲推开门，走到窗边坐下，慢慢说了一会儿话
== eight added combat cues (must be True) ==
True   第三镜两人摔打在一起  | rule_on=True
True   两人近身肉搏  | rule_on=True
True   第三镜两人对打  | rule_on=True
True   两人一言不合打起来  | rule_on=True
True   两人开打  | rule_on=True
True   多人群殴  | rule_on=True
True   最终决战  | rule_on=True
True   双方交战  | rule_on=True
== non-bool overrides (must be False) ==
action_context='false'  -> False      action_context='true'   -> False
action_context='no'     -> False      action_context=0        -> False
action_context=1        -> False      action_context=''       -> False
action_context=None     -> False      action_context=[]       -> False
action_context={}       -> False
== text limit 12000 ==
len(filler)=16000  trailing cue beyond limit: decision=False facade_rule=False direct_rule=False
leading cue within limit: decision=True facade_rule=True direct_rule=True

$ git diff --check
exit 0
```

同样**不得把这组读数说成「门禁全绿」**：`check_file_sizes --fail-on high,medium` 仍是 exit 1，
且失败项与本变更无关（预存的 `canvas_reads.py` 3056 行）。另外 `git diff --check` 只看已跟踪文件，
而 `storyboard_prompt.py` 与 `docs/arch/` 在本次工作树里都是 untracked，不受它覆盖。

### 现场证据（R7 实跑，非推断）

```text
$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py tests/test_workflow_runtime.py tests/test_architecture_boundaries.py -q
206 passed in 26.09s
# R6 时同一批是 180 passed；R7 在 test_filmcraft_kb.py 里追加 26 条（含参数化展开），既有用例一条未删未改

$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py -q
106 passed in 1.91s
# R6 时单文件 80 passed

$ .venv/Scripts/python.exe -m pytest tests/test_freezone_prompt_optimizer.py tests/test_freezone_script_contract.py -q
105 passed in 14.88s
# 画布侧三个直连 production 的旧调用方：R7 改语义后行为仍不变

$ .venv/Scripts/python.exe -m pytest tests/test_combat_film_contract.py tests/test_creative_contract.py tests/test_director_clarification.py tests/test_global_video_optimizer_prompt_contract.py tests/test_prop_extraction_contract.py tests/test_workflow_production_plan.py tests/test_agent_events.py tests/test_canvas_template_store.py tests/test_memory_index.py -q
219 passed, 8 warnings in 13.51s

$ .venv/Scripts/python.exe -m pytest tests/ -q -k "prompt or filmcraft or storyboard"
1 failed, 536 passed, 3 skipped, 4942 deselected, 12 warnings in 46.69s
# 唯一失败仍是 R6 记过的那条环境项，与本变更无关，见「Assumptions not verified」

$ .venv/Scripts/python.exe -m ruff check src/novelvideo/production/filmcraft_kb.py src/novelvideo/services/production_contracts.py src/novelvideo/workflow_runtime/storyboard_prompt.py src/novelvideo/workflow_runtime/executor.py tests/test_filmcraft_kb.py
All checks passed!            # exit 0

$ .venv/Scripts/python.exe scripts/architecture/check_import_boundaries.py --fail-on high,medium
finding_count=0  severity_counts={}  files_scanned=660  import_edges=1820   # exit 0
# 与 R6 的读数逐位相同：R7 没有新增任何 import

$ .venv/Scripts/python.exe scripts/architecture/check_file_sizes.py --fail-on high,medium
finding_count=1  severity_counts={"high": 1}                              # exit 1
[high] unrecorded-large-file src/novelvideo/agent_tools/village_canvas/canvas_reads.py observed=3056 baseline=3000
# executor.py 仍不在 finding 列表里；wc -l src/novelvideo/workflow_runtime/executor.py = 3000

$ git diff --check
exit 0
```

行为直读（工作单要求逐项打印；`rule=` 是分镜合同里 `craft.action_fragment_shots.v1` 的有无）：

```text
== six clear combat with another negated noun (must be True) ==
True  rule=True  不需要对白的三镜打斗短片
True  rule=True  避免煽情的三镜打斗短片
True  rule=True  不做慢动作的肉搏戏
True  rule=True  没有武器的两人对打
True  rule=True  没有台词的巷口决战
True  rule=True  不要温情，没有台词的打斗
== three enumeration prohibitions (must be False) ==
False rule=False 全片不要打斗，也不要追逐
False rule=False 全片不要打斗、追逐
False rule=False 不要出现打斗、爆炸、群殴
== R6 affirmative after negation (must be True) ==
True  rule=True  不要温情，要打斗
True  rule=True  不要慢动作，两人打斗
True  rule=True  不要打斗，要看追逐
True  rule=True  不要温情；第三镜两人追逐打斗
== R6 forbidden negations (must be False) ==
False rule=False 全片不要打斗，也不要追逐
False rule=False 全片不得出现打斗
False rule=False 全片严禁打斗
False rule=False 不可有打斗
False rule=False 不准打斗
False rule=False 不许打斗
False rule=False 不出现打斗
== warm ordinary (must be False) ==
False rule=False 三镜温情短片：母亲推开门，走到窗边坐下，慢慢说了一会儿话
```

**同样不得说成「门禁全绿」**：`check_file_sizes` 仍是 exit 1（预存 `canvas_reads.py` 3056 行）；
真实模型生成一次都没跑；`architecture_manifest.json` 里仍无 filmcraft 条目，T4 守卫测试仍未落地。

### 现场证据（R8 实跑，非推断）

```text
$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py tests/test_workflow_runtime.py tests/test_architecture_boundaries.py -q
228 passed in 19.38s
# R7 时同一批是 206 passed；R8 在 test_filmcraft_kb.py 里追加 22 条（含参数化展开），既有用例一条未删未改

$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py -q
128 passed in 1.77s
# R7 时单文件 106 passed

$ .venv/Scripts/python.exe -m pytest tests/test_freezone_prompt_optimizer.py tests/test_freezone_script_contract.py -q
105 passed in 13.77s
# 与 R7 的读数逐位相同：画布侧三个直连 production 的旧调用方行为不变

$ .venv/Scripts/python.exe -m pytest tests/ -q -k "prompt or filmcraft or storyboard"
1 failed, 558 passed, 3 skipped, 4942 deselected, 12 warnings in 40.35s
# 唯一失败仍是 R6/R7 记过的环境项 test_scene_reference_prompt.py::
# test_scene_reference_newapi_uses_normalized_gateway_base_url（未配置生图模型），
# 该测试模块对 filmcraft/storyboard_prompt 的引用数为 0（grep -c 实测 0）

$ .venv/Scripts/python.exe -m ruff check src/novelvideo/production/filmcraft_kb.py src/novelvideo/services/production_contracts.py src/novelvideo/workflow_runtime/storyboard_prompt.py src/novelvideo/workflow_runtime/executor.py tests/test_filmcraft_kb.py
All checks passed!            # exit 0

$ .venv/Scripts/python.exe scripts/architecture/check_import_boundaries.py --fail-on high,medium
finding_count=0  severity_counts={}  files_scanned=660  import_edges=1820   # exit 0
# 与 R7 的读数逐位相同：R8 没有新增任何 import

$ .venv/Scripts/python.exe scripts/architecture/check_file_sizes.py --fail-on high,medium
finding_count=1  severity_counts={"high": 1}                              # exit 1
[high] unrecorded-large-file src/novelvideo/agent_tools/village_canvas/canvas_reads.py observed=3056 baseline=3000
# 唯一失败项仍是这条**预存**红门禁；executor.py 不在 finding 列表里（本轮输出里没有它）

$ git diff --check
exit 0
```

行为直读（`rule=` 是分镜合同里 `craft.action_fragment_shots.v1` 的有无）：

```text
== R8 six reproductions + 不要在画面里出现打斗 (must be False) ==
False rule=False 不要中景打斗
False rule=False 不要特写打斗
False rule=False 不要在画面里打斗
False rule=False 不要在画面里出现打斗
False rule=False 不要出现打斗、爆炸、两人群殴
False rule=False 不要出现打斗、爆炸、多人群殴
False rule=False 全片不要打斗、三人追逐
== R7 enumeration prohibitions (must stay False) ==
False rule=False 全片不要打斗，也不要追逐
False rule=False 全片不要打斗、追逐
False rule=False 不要出现打斗、爆炸、群殴
== R7 clear combat with another negated noun (must stay True) ==
True  rule=True  没有台词的三镜打斗短片
True  rule=True  没有武器的两人对打
True  rule=True  不需要对白的三镜打斗短片
True  rule=True  避免煽情的三镜打斗短片
True  rule=True  不做慢动作的肉搏戏
True  rule=True  不要温情，没有台词的打斗
== affirmative enumeration (must stay True) ==
True  rule=True  不要温情、要打斗
True  rule=True  不要打斗、要看追逐
True  rule=True  不要温情，而是两人追逐打斗
True  rule=True  不要温情、两人打斗          # 第一项不是词条 → 不是禁止清单（见下）
False rule=False 不要打斗、要温情
== R6 forbidden negations / warm (must stay False) ==
False rule=False 全片不得出现打斗
False rule=False 全片不要有打斗
False rule=False 不可有打斗
False rule=False 不出现打斗
False rule=False 禁止任何打斗
False rule=False 不要打斗，也不要追逐
False rule=False 三镜温情短片：母亲推开门，走到窗边坐下，慢慢说了一会儿话
```

**R8 同样不得说成「门禁全绿」**：上面五条命令里有两条不是全绿——`check_file_sizes` 是 exit 1，
失败项是**预存**的 `canvas_reads.py` 3056 行（不是本轮引入，本轮也没有登记进 baseline 掩盖）；
真实模型生成一次都没跑（只验证提示词组装与规则选择，没有触发任何模型出片，也没有付费生成）；
`architecture_manifest.json` 里仍无 filmcraft 条目，T4 守卫测试仍未落地。

### 现场证据（R9 实跑，非推断）

```text
$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py tests/test_workflow_runtime.py tests/test_architecture_boundaries.py -q
259 passed in 18.09s
# R8 时同一批是 228 passed；R9 在 test_filmcraft_kb.py 里追加 31 条（含参数化展开），既有用例一条未删未改

$ .venv/Scripts/python.exe -m pytest tests/test_filmcraft_kb.py -q
159 passed in 1.93s
# R8 时单文件 128 passed；R9 的 31 条里 6+6 条是六条复现的决策/分镜合同/单元级三条钉法

$ .venv/Scripts/python.exe -m pytest tests/test_freezone_prompt_optimizer.py tests/test_freezone_script_contract.py -q
105 passed in 21.29s
# 与 R6/R7/R8 的读数逐位相同：画布侧三个直连 production 的旧调用方行为不变

$ .venv/Scripts/python.exe -m ruff check src/novelvideo/production/filmcraft_kb.py src/novelvideo/services/production_contracts.py src/novelvideo/workflow_runtime/storyboard_prompt.py src/novelvideo/workflow_runtime/executor.py tests/test_filmcraft_kb.py
All checks passed!            # exit 0

$ .venv/Scripts/python.exe scripts/architecture/check_import_boundaries.py --fail-on high,medium
finding_count=0  severity_counts={}  files_scanned=660  import_edges=1820   # exit 0
# 与 R7/R8 的读数逐位相同：R9 没有新增任何 import

$ .venv/Scripts/python.exe scripts/architecture/check_file_sizes.py --fail-on high,medium
finding_count=1  severity_counts={"high": 1}                              # exit 1
[high] unrecorded-large-file src/novelvideo/agent_tools/village_canvas/canvas_reads.py observed=3056 baseline=3000
# 唯一失败项仍是这条**预存**红门禁；executor.py 不在 finding 列表里，filmcraft_kb.py（1137 行）也未上榜

$ git diff --check
exit 0
```

行为直读（`rule=` 是分镜合同里 `craft.action_fragment_shots.v1` 的有无）：

```text
== R9 six reproductions (must be False) ==
False rule=False 不要大特写打斗
False rule=False 不要中近景打斗
False rule=False 不要打斗、武打动作、三人群殴
False rule=False 不要镜头里出现打斗
False rule=False 不要画面里出现打斗
False rule=False 不要场面打斗
== R8 scaffolded prohibitions (must stay False) ==
False rule=False 不要中景打斗
False rule=False 不要特写打斗
False rule=False 不要在画面里打斗
False rule=False 不要在画面里出现打斗
False rule=False 不要出现打斗、爆炸、两人群殴
False rule=False 不要出现打斗、爆炸、多人群殴
False rule=False 全片不要打斗、三人追逐
== R7/R8 enumeration + affirmative (unchanged) ==
False rule=False 全片不要打斗、追逐
False rule=False 不要出现打斗、爆炸、群殴
False rule=False 不要打斗、近身肉搏
True  rule=True  不要温情、要打斗
True  rule=True  不要打斗、要看追逐
True  rule=True  不要温情、两人打斗          # 第一项不是词条 → 不是禁止清单（R8 语义原样保留）
False rule=False 不要打斗、两人追逐
== clear combat kept (must be True) ==
True  rule=True  没有台词的三镜打斗短片     # 工作单点名的三条
True  rule=True  没有武器的两人对打
True  rule=True  不要温情，没有台词的打斗
True  rule=True  三镜动作片
True  rule=True  第三镜是武打动作
True  rule=True  两人在雨里摔跤
== warm ordinary / forbidden negations (must be False) ==
False rule=False 全片不得出现打斗
False rule=False 不可有打斗
False rule=False 不要打斗，也不要追逐
False rule=False 三镜温情短片：母亲推开门，走到窗边坐下，慢慢说了一会儿话
```

helper 直读（单元级，不经决策函数）：

```text
_only_bridge_tokens("大特写")  True      _strip_negation_scaffolding("大特写")  ""
_only_bridge_tokens("中近景")  True      _strip_negation_scaffolding("中近景")  ""
_only_bridge_tokens_or_cues("武打动作")  True
_strip_tokens("武打动作", _ACTION_CONTEXT_CUES)  ""      # 修复前会剩 "动作"
_only_bridge_tokens("镜头里出现")  True   _only_bridge_tokens("画面里出现")  True
_only_bridge_tokens("场面")        True   # 无后缀的场景框架变体
_only_bridge_tokens("镜头") False  _only_bridge_tokens("画面") False  _only_bridge_tokens("台词") False
_only_bridge_tokens("慢动作") False
_only_bridge_tokens("镜头里出现打斗") False   _only_bridge_tokens("画面里出现一群人打斗") False
_only_bridge_tokens_or_cues("动作场面") True  _strip_negation_scaffolding("动作场面") "动作场面"
```

**R9 同样不得说成「门禁全绿」**：上面命令里有两条不是全绿——`check_file_sizes` 是 exit 1，
失败项是**预存**的 `canvas_reads.py` 3056 行（不是本轮引入，本轮也没有登记进 baseline 掩盖）；
真实模型生成一次都没跑（只验证提示词组装与规则选择，没有触发任何模型出片，也没有付费生成）；
`architecture_manifest.json` 里仍无 filmcraft 条目，T4 守卫测试仍未落地。

### R9 主题：词条最长匹配 + 无介词场景框架

**独立评审复现的输入（R9 动手前，lead 与本次会话各实跑一次）**：

```text
has_filmcraft_action_context("不要大特写打斗")                     True   # 1 纯禁止，景别词被短词吃掉
has_filmcraft_action_context("不要中近景打斗")                     True   #   同上
has_filmcraft_action_context("不要打斗、武打动作、三人群殴")       True   # 2 纯禁止，列举项被短词条吃掉
has_filmcraft_action_context("不要镜头里出现打斗")                 True   # 3 纯禁止，无介词场景框架没被识别
has_filmcraft_action_context("不要画面里出现打斗")                 True   #   同上
has_filmcraft_action_context("不要场面打斗")                       True   #   同上
```

**根因（两处，都不是词表缺词）**：

1. **替换顺序 ≠ 匹配语义**。`_strip_tokens` 按元组**书写顺序**做 `str.replace`，
   而表里短词排在长词前面（`特写` 在 `大特写` 前、`近景` 在 `中近景` 前、`武打` 在
   `武打动作` 前）。短词先把长词削掉一半，剩下的 `大`/`中`/`动作` 不在任何词表里，
   于是间隔「非空」→ 判「否定不附着」→ 纯禁止漏判成打斗请求。列举那一例更隐蔽：
   `、` 前面的列举项要求「脚手架 + 已列举词条」，`武打动作` 被削成 `动作`（`动作`
   已不在词条表里）后，整串禁止就不再成立。
2. **框架必须有介词**。R8 的 `_strip_locative_frames` 只认 `在` 引出的框架，
   而同一类状语在中文里常常不带介词（`镜头里`、`画面里`、`场面`）。

**R9 修复后的判据（在 R8 三条之上，只动这两处）**：

1. **最长匹配（`_strip_tokens`，`:646`）**：每次按**长度降序**尝试 token，等长保持表内顺序
   （`sorted` 稳定）。删除动作本身不变（仍是多轮直到不再变化），所以「删一个暴露另一个」
   （`出现任何打斗`）照旧生效。同一条规则同时作用于三类调用点：脚手架
   （`_NEGATION_CUE_ADJUNCT_TOKENS + _NEGATION_BRIDGE_TOKENS`）与列举项的词条表
   （`_ACTION_CONTEXT_CUES`），因此 `大特写`/`中近景`/`武打动作` 都整体识别。
2. **有界无介词场景框架（`_scene_frame_end`，`:691`）**：形状固定为
   `场景名词(镜头|画面|场面)` + **可选**方位后缀（`里/中/内/上/下/外/间`）+ **可选**桥接词
   （`出现|有`），总长封顶 `_NEGATION_SCENE_FRAME_MAX = 5`（`:204`）。三处明确的边界：
   - **裸 `画面`/`镜头` 仍是实词**：没有后缀、没有 `出现/有` 时不构成框架，所以
     `_only_bridge_tokens("画面")` 仍是 `False`（R8 用例原样保留、原样通过）。
   - **裸 `场面` 是框架变体**：`场面` 单独出现时本来就是「场面/场面调度」这类**调度词**，
     不是被否定的宾语，因此 `不要场面打斗` 判 `False`。这是本版唯一的语义放宽点。
   - **不吞词条**：场景名词也是真实词条的一部分（`动作场面`），所以框架命中前先过
     `_inside_listed_cue`（`:673`）——只有当这段字不是某个**更长词条**的一部分时才剥；
     `动作场面` 因此保持可读，`_only_bridge_tokens_or_cues("动作场面")` 仍是 `True`。
   框架里没有任何字符能匹配 `、`，所以它天然不会跨列举分隔符，也不会吞掉整个小句
   （`镜头里出现打斗` 只剥掉 `镜头里出现`，剩 `打斗` → 仍判「不是脚手架」）。
   原来的介词循环抽成 `_strip_prepositional_frames`（`:755`），**逐行未改**，
   `_strip_locative_frames` 只是「先介词框架、后场景框架」的组合。

**语义变化要明说（三处，均由新增用例与 ADR 显式记录）**：

- `不要大特写打斗` / `不要中近景打斗` 由 `True` 更正为 `False`：景别词整体识别，不再留碎片。
- `不要打斗、武打动作、三人群殴` 由 `True` 更正为 `False`：列举项的词条删除同样最长匹配。
- `不要镜头里出现打斗` / `不要画面里出现打斗` / `不要场面打斗` 由 `True` 更正为 `False`：
  无介词场景框架按状语处理。**`不要场面、打斗` 这类写法也随之变成禁止**（`场面` 被当框架剥掉），
  这是裸 `场面` 变体的直接后果，已如实记在 Risks 表 R25。
- 明确打斗**一条没动**：`没有台词的三镜打斗短片`、`没有武器的两人对打`、
  `不要温情，没有台词的打斗` 仍判 `True` 并带出打斗规则（R9 用例 `:1095` 钉住）；
  `不要温情、两人打斗` 仍判 `True`（第一项不是词条）。

**已知残余风险（诚实记录，见 Risks 表 R24–R26）**：这套判据仍是**有界字符串解析**，
没有分词器、没有 NLP 依赖、没有新增 import；分类是显式白名单，白名单外的一律按实词处理，
方向仍是「保住词条」（召回优先），不是「多压一次」。

### R8 主题：直接禁止的脚手架分类，列举项可带描述语

**独立评审复现的输入（R8 动手前，lead 实跑）**：

```text
has_filmcraft_action_context("不要中景打斗")                       True   # 1 纯禁止，被当成打斗请求
has_filmcraft_action_context("不要特写打斗")                       True   #   同上
has_filmcraft_action_context("不要在画面里打斗")                   True   #   同上
has_filmcraft_action_context("不要出现打斗、爆炸、两人群殴")       True   # 2 纯禁止，列举项带数量词后漏判
has_filmcraft_action_context("不要出现打斗、爆炸、多人群殴")       True   #   同上
has_filmcraft_action_context("全片不要打斗、三人追逐")             True   #   同上
```

**根因（不是词表缺词，是判据分类错）**：R7 用「否定末尾到词条之间只剩桥接词」当附着判据，
而桥接词表里混着**内容名词**（`画面/镜头/场面`）与**方位字**（`里/中`）：

1. 全局替换是**无上下文**的：「中景」被削掉 `中` 只剩 `景`，「特写」不含桥接词原样保留，
   于是这两条纯禁止的间隔非空 → 判「否定不附着」→ 漏判；「在画面里」则被整段抹掉，
   间隔只剩 `在`，同样非空 → 漏判。
2. 列举判据要求**最后一项**也只有功能词，而列举项常带数量词（`两人群殴`/`三人追逐`），
   于是整串禁止在最后一项上失效 → 漏判。

**R8 修复后的判据（三条同时成立才压词条）**：

1. **小句作用域**（R6，未改）：否定与词条同处 `，。；！？,.;!?\n` 之间的小句。
2. **附着判定（R8 重写分类）**：取词条之前最新那个否定，看它末尾到词条起点之间的间隔，
   间隔必须是**禁止脚手架**，由三类结构分别认定——
   (a) 功能词（`_NEGATION_BRIDGE_TOKENS`，`:129`，**已删掉 `画面/镜头/场面/里/中`**）：
   `全片/出现/有/也/再/任何/…`；
   (b) **介词框架**（`_strip_locative_frames`，`:651`）：`在` + 至多 4 个字符 + 可选方位后缀
   （`里/中/内/上/下/外/间`）。介词引出的只能是**状语**——「在哪里/在什么里发生」——
   所以框架里的名词不会成为否定的宾语：`不要在画面里打斗` 禁的是打斗，不是画面；
   框架有界、遇 `、` 即停，吞不掉整句；
   (c) **词条自身的附属语**（`_NEGATION_CUE_ADJUNCT_TOKENS`，`:173`）：景别词
   `中景/特写/近景/远景/全景/中近景/大特写` 与数量词 `两人/三人/多人/…`。景别说的是
   「这镜头怎么拍」、数量说的是「几个人」，都长在词条身上，不构成第二个被否定的宾语。
   间隔里出现**其它实词**时仍是 R7 的读法：否定修饰的是那个实词，词条照常命中
   （`没有台词的打斗`、`避免煽情的三镜打斗短片`）。
3. **列举判定（R8 改为看前项）**：间隔里出现 `、` 时，`、` **前面**的每一段必须仍是
   「脚手架 + 已列举的词条」（`_only_bridge_tokens_or_cues`，`:708`）——那才是「这段文字是
   一个禁止清单」的证据；**最后一项不再重新审查实词**，因此 `两人群殴`/`三人追逐`/
   `近身肉搏` 都被这一条禁止覆盖。列举的第一项不是词条时（`不要温情、两人打斗`），
   这段文字不是禁止清单，`、` 之后按肯定读——这是 R7 已有的语义，R8 原样保留并在
   `tests/test_filmcraft_kb.py:899` 显式钉住。
   肯定标记（`要/而是/需要/改成`）仍然**先**判，`不要打斗、要看追逐` 判 `True`；
   同一段里出现更靠后的否定时，`_latest_negation` 会换人，否定重新落到那个否定身上。

**语义变化要明说（两处，均由新增用例与 ADR 显式记录）**：

- `不要中景打斗` / `不要特写打斗` / `不要在画面里打斗` / `不要在画面里出现打斗` 由 `True` 更正为
  `False`：景别词与介词框架算词条的附属语，不再算「另一个被否定的实词」。
- `不要出现打斗、爆炸、两人群殴` / `多人群殴` / `全片不要打斗、三人追逐` 由 `True` 更正为 `False`：
  列举是否属于同一个禁止，看前项是不是词条，不看最后一项的措辞。
- `不要温情、两人打斗` **保持** `True`（R7 语义不变）：第一项 `温情` 不是词条，所以这不是禁止清单。

**已知残余风险（诚实记录，见 Risks 表 R20–R23）**：这套判据仍是**有界字符串解析**，
没有分词器、没有 NLP 依赖、没有新增 import；分类是显式白名单，白名单外的一律按实词处理，
方向是「保住词条」（召回优先），不是「多压一次」。

### R7 主题：否定要附着在词条上，`、` 是列举分隔符

**评审复现的输入（R7 动手前，本会话实跑）**：

```text
has_filmcraft_action_context("不需要对白的三镜打斗短片")     False  # 1 否定的是「对白」，却压掉了「打斗」
has_filmcraft_action_context("避免煽情的三镜打斗短片")       False  #   否定的是「煽情」
has_filmcraft_action_context("不做慢动作的肉搏戏")           False  #   否定的是「慢动作」
has_filmcraft_action_context("没有武器的两人对打")           False  #   否定的是「武器」
has_filmcraft_action_context("没有台词的巷口决战")           False  #   否定的是「台词」
has_filmcraft_action_context("不要温情，没有台词的打斗")     False  #   否定的是「温情 / 台词」
has_filmcraft_action_context("全片不要打斗、追逐")           True   # 2 纯禁止，却因为 `、` 被当成小句边界而漏判
has_filmcraft_action_context("不要出现打斗、爆炸、群殴")     True   #   同上
```

**R7 修复后**：六条漏报全部转 `True`（并带出 `craft.action_fragment_shots.v1`），两条纯禁止转 `False`
（工作单点的三个列举禁止里，`全片不要打斗，也不要追逐` 在 R6 时已正确判 `False`，
R7 新增用例把它一并钉住）。

**语义变化要明说**：R6 的规则是「否定与词条在同一小句里就压掉词条」，R7 收紧为
**三条同时成立才压**——(a) 否定在词条自己所在的小句里（`，。；！？,.;!?\n` 仍是边界）；
(b) 否定**附着**在词条上，即否定末尾到词条起点之间为空，或只有功能/桥接词
（`全片/出现/有/也/再/任何/一个/一切/进行/加入/包含/…`）；(c) 那段间隔里没有
肯定标记（`要/而是/需要/改成`）。`、` 从边界集合移到 `_ENUMERATION_SEPARATORS`：
一个禁止词要管住整串列举项（`不要打斗、追逐`、`不要出现打斗、爆炸、群殴`），
只有同一段里出现肯定标记才重新打开后面的词条（`不要打斗、要看追逐` = True）。
判据仍是「最新一个否定短语」：`不要温情，没有台词的打斗` 里两个否定，只有更近的那个在谈打斗，
所以它不压词条，而更早那个也够不到（跨了小句）。

实现是**有界的本地字符串解析**：没有分词器、没有 NLP 库、没有新增依赖，
全部逻辑都在 `filmcraft_kb.py` 一个文件里。

### R6 主题：小句级否定、词表召回、门面调用路径与文本截断

**评审复现的输入（R6 动手前，本会话实跑；第 4/5/6 条是静态事实与本 ADR 表述）**：

```text
has_filmcraft_action_context("不要温情，要打斗")            False  # 1 第二小句是肯定，却判否
has_filmcraft_action_context("全片不得出现打斗")            True   # 2 禁止项被当成请求
has_filmcraft_action_context("全片严禁打斗")                True
has_filmcraft_action_context("不可有打斗")                  True
has_filmcraft_action_context("不准打斗")                    True
has_filmcraft_action_context("不许打斗")                    True
has_filmcraft_action_context("不出现打斗")                  True
has_filmcraft_action_context("第三镜两人摔打在一起")        False  # 3 八个明确打斗措辞全部漏报
has_filmcraft_action_context("两人近身肉搏")                False
has_filmcraft_action_context("第三镜两人对打")              False
has_filmcraft_action_context("两人一言不合打起来")          False
has_filmcraft_action_context("两人开打")                    False
has_filmcraft_action_context("多人群殴")                    False
has_filmcraft_action_context("最终决战")                    False
has_filmcraft_action_context("双方交战")                    False
# 4/5 是调用路径与截断不一致（静态事实，见「第四版实况」表），第 6 条是本 ADR 的表述漂移
```

**R6 修复后**：上表八条漏报全部转 `True`，七条禁止说法全部转 `False`，`不要温情，要打斗`
转 `True`；分镜合同同步变化（新增测试逐条断言 `craft.action_fragment_shots.v1` 的有无）。
**语义变化要明说**：`不要打斗，要看追逐` 由 R5 的 `False` 更正为 `True`——
R5 把否定当成「词前 8 字符扁平窗口」，跨过了逗号；R6 认定否定只在同一小句内生效。
这是工作单明确要求的行为更正，已同步改掉 R5 写下的那条断言（不是放宽阈值）。

### R4/R5 主题：`action_context` 的误报、漏报与决策归属

**R4 修复前的问题（工作单提供的前置实测）**：单字 `推/拉/走/跑/摔` 与两字词 `动作` 被当成动作信号，
`三镜温情短片：母亲推开门，走到窗边坐下…` 会带出打斗专用的
`craft.action_fragment_shots.v1`。

**R4 修复后、R5 动手前（本次会话实跑，复现评审的四个问题）**：

```text
legacy 三镜动作片：主角在仓库里与人对决   False      # 1 漏报：明确打戏措辞被删掉了
legacy 第三镜是武打动作                   False
legacy 两人在雨里摔跤                     False
nonbool false（params={"action_context":"false"} + 文本含「打斗」） True   # 4 字符串真值
negated storyboard（…全片不要打斗，也不要追逐）          True   # 2 否定被当命中
warm storyboard（…推开门，走到窗边坐下）                 False  # R4 的修复仍然有效
```

**R5 修复后（本次实跑，见上「现场证据」）**：四类输入分别得到
漏报→True、否定→False、非 bool→False、温情→False；分镜合同与门面决策同步变化。

### 边界规则原文与现有机制

- 规则：`scripts/architecture/domain_boundaries.json:90-103`，`id=workflow-to-production-implementation`，
  `severity=medium`，reason：“WorkflowRuntime 与 Production 应通过阶段合同或应用门面交互，避免互相穿透。”
- 现有豁免（`domain_boundaries.json:96-101`）：`production.metadata`、`production.asset_passport`、
  `production.shot_contract`、`production.cost_receipt` 四个模块。
- 豁免的语义与实现：`scripts/architecture/check_import_boundaries.py:136-140`（模块名精确相等）。
- 该门禁**没有 baseline 棘轮**：`load_config` 只认 `domains` 与 `rules`，`exclude_target_modules`
  是后端唯一的例外通道。
- 清单里的 production 合同目前只有 `cinematic_production_contract`；`filmcraft_kb` **没有**登记
  （本次也没有加，见「未实施」一节）。
- `services` 属 foundation 域（`domain_boundaries.json:55`），workflow→foundation 无规则；
  `storyboard_prompt.py:16` 的 `from novelvideo.services import production_contracts` 因此不产生 finding。

### 被引用模块的真实性质

`src/novelvideo/production/filmcraft_kb.py`（R5 时 695 行，R6 后 767 行，R7 后 933 行，**R8 后 1038 行**）：

- 只依赖 `collections.abc.Mapping` 与 `typing.Any`，无 I/O、无状态、无随机、无时间。
- 规则表 `RULES` + 纯选择函数 `inject_filmcraft_rules(*, node_type, params, director_vision,
  project_dna, source_text, creation_stage) -> list[dict[str, str]]`，返回字段固定为
  `rule_id/stage/trigger/instruction/avoid`。
- `shot_count` 判定是 `int(params.get("shot_count", 0) or 0) > 1`——省略该键与传 0 等价，与传 ≥2 不等价。
- `action_context` 判定（R6 后 `:582-593`）：
  1. 键存在且值是**真 bool** → 原样返回（`True` 开、`False` 关）；
  2. 键存在但值不是 bool（字符串 `"false"/"true"/"no"`、数字 0/1、`None`…）→ 返回 `False`，
     不再走 `bool(...)` 真值转换；
  3. 键不存在 → 调 `_has_filmcraft_action_context(source_text)`（R6 后 `:521`），即唯一权威词表
     + **小句级**否定的关键词路径。
- 三处消费者的语义因此分开：分镜侧**总是**传 bool；画布侧三处从不传该键，走关键词路径。
  R6 把两条路径的**输入**也对齐了：决策与规则选择都先过 `normalize_action_context_text`
  （strip + 12,000 字截断），见下。

### 权威词表、否定与显式 bool 合同（R5 的三条决策，R6 修订）

1. **唯一词表**（`_ACTION_CONTEXT_CUES`）：R5 时 29 条（`:30-61`），R6 后 **37 条**（`:33-71`）。
   R4 的 18 条
   （`打斗/打戏/格斗/战斗/搏斗/追逐/奔跑/格挡/枪战/爆炸/挥拳/出拳/踢腿/冲撞/袭击/厮打/动作戏/动作场面`）
   在 R5 **加回** 11 条 `动作片/动作短片/动作剧/武打/武打动作/交手/对决/扭打/摔跤/混战/厮杀`；
   R6 再补 8 条 `摔打/肉搏/对打/打起来/开打/群殴/决战/交战`。仍然**不含**裸 `推/拉/走/跑/摔`
   与裸 `动作`。裸 `动作` 不是词条，但「动作片」这类**命名了动作片/打斗**的短语算命中。
2. **否定（R6 起是小句级，R7 起再加附着与列举判定；R5 的 8 字符扁平窗口作废）**：
   作用域是词条**自己所在的小句**——从小句左边界到词条起点之间出现否定短语就忽略这次命中，
   边界字符为 `，。；！？,.;!?\n`（`_CLAUSE_BOUNDARIES`，R6 时 12 个含 `、`；**R7 后 11 个，
   `、` 移入 `_ENUMERATION_SEPARATORS`**）。否定短语扩到 24 条，在 R5 的
   `不要/不用/不做/不加入/不带/不含/不需要/无需/没有/禁止/避免/勿/别/without/no ` 之上
   新增 `不得/严禁/不可/不准/不许/不出现/不再/切勿/莫`。
   **R7 补充（附着判定）**：同小句只是必要条件。取词条之前**最新**的那个否定短语，
   看它末尾到词条起点之间那段文字：空、或只有功能/桥接词（`_NEGATION_BRIDGE_TOKENS`）时
   才算「否定附着在词条上」，压掉这次命中；夹着实词（`台词/对白/武器/温情/煽情/慢动作/三镜` …）
   时说明否定修饰的是那个实词，词条照常命中；夹着肯定标记（`_AFFIRMATIVE_CONTEXT_MARKERS`：
   `要/而是/需要/改成`）时否定失效。`全片不得出现打斗` 里的 `出现`、`不可有打斗` 里的 `有`
   正是白名单要放行的直接形式。
   **R7 补充（列举判定）**：`、` 不再是边界。否定末尾到词条之间若含 `、`，则最后一段必须
   仍只有功能词，前面的各段只允许「功能词 + 已列举的词条」（`不要出现打斗、爆炸、群殴`），
   并且整段里不能出现肯定标记（`不要打斗、要看追逐` 因此判 True）。
   **R8 修订（第五版这两条被取代）**：桥接词表只留功能词，`画面/镜头/场面/里/中` 移出；
   间隔的放行改为三类**结构**判据——功能词、`在`+≤4 字+可选方位后缀的介词框架
   （`_strip_locative_frames`，`:651`）、词条自身的附属语（`:173`：景别词 `中景/特写/…`
   与数量词 `两人/三人/多人/…`）。于是 `不要中景打斗`、`不要特写打斗`、`不要在画面里打斗`、
   `不要在画面里出现打斗` 判 `False`；其余实词仍按第五版的读法（`没有台词的打斗` = True）。
   列举判定改为：`、` **前面**的每一段必须是「脚手架 + 已列举的词条」，
   **最后一项不再重新审查实词**，所以 `两人群殴`/`多人群殴`/`三人追逐` 被同一禁止覆盖；
   `不要温情、两人打斗` 仍判 `True`（第一项不是词条，不是禁止清单）。
   **R9 修订（第六版这两条被取代）**：脚手架与词条的删除一律**最长匹配**
   （`_strip_tokens`，`:646`：按长度降序尝试，等长保持表内顺序），短词不再吃掉
   `大特写`/`中近景`/`武打动作` 的前半截；间隔放行的结构判据之外再补一类
   **有界无介词场景框架**（`_scene_frame_end`，`:691`：`镜头|画面|场面` + 可选方位后缀
   + 可选 `出现|有`，封顶 5 字），于是 `不要镜头里出现打斗`、`不要画面里出现打斗`、
   `不要场面打斗` 判 `False`；裸 `画面`/`镜头` 仍是实词
   （`_only_bridge_tokens("画面")` 仍 `False`），场景名词落在更长词条里时（`动作场面`）
   不剥。其余语义一条未动。
   形状仍借用仓库既有惯例
   `agent_tools/village_canvas/workflow_dispatch.py:735-764` 的 `_has_affirmative_marker`，
   但不照抄它的固定窗口——固定窗口正是 R6 要修的两类错误的来源。
   评审点名的输入
   `三镜温情短片：母亲推开门，走到窗边坐下，全片不要打斗，也不要追逐` 判 `False`，
   `不要温情，要打斗` 判 `True`，
   `没有台词的巷口决战` 与 `全片不要打斗、追逐` 分别判 `True` / `False`（均为实跑）。
   `别` 另有一条例外：`别` 前一个字符属于 `特/告/分/个/辨/区/识/性/道/级/差/离`
   （`_NON_NEGATING_PREFIXES`，R5 时 `:89-101`，R6 后 `:124-137`，R7 后 `:184-197`）时不算否定，
   否则「特别激烈的打斗」会丢命中。没有引入任何 NLP 依赖。
3. **显式 bool 合同**：只有真 bool 算决策；其余值一律关（见上「被引用模块的真实性质」）。
   原因：`params` 可以来自客户端 JSON（`dict[str, Any]`），`bool("false")` 为真。
4. **共享文本归一（R6 新增）**：`ACTION_CONTEXT_TEXT_LIMIT = 12_000`（`:459`）与
   `normalize_action_context_text(source_text)`（`:462`，公开）。`inject_filmcraft_rules`
   与 `_has_filmcraft_action_context` 都先过它，因此门面决策和真正被选中的规则读的是
   **同一段字符**；12,000 字之后的词条对两边都不可见（实跑：trailing cue → 三条路径全
   `False`，leading cue → 三条路径全 `True`）。
5. **门面调用路径唯一（R6 新增）**：`storyboard_prompt.py` 只保留
   `from novelvideo.services import production_contracts`，决策与注入都按**模块属性**在调用点取，
   import 期不再绑定 `inject_filmcraft_rules` 函数对象。这样一次
   `monkeypatch.setattr(production_contracts, "inject_filmcraft_rules", fake)` 就能同时控制
   决策（`has_filmcraft_action_context`）与合同渲染。

### 仓库已有的门面惯例（本决策复用）

| 文件 | 形态 |
| --- | --- |
| `src/novelvideo/services/continuity_contract.py` | 自述中立门面，**函数体内**导入 production 实现 |
| `src/novelvideo/services/production_contracts.py` | 「Neutral application facade for production-stage contracts」，`executor.py:30` 等已在用；filmcraft 两个转发在 `:150-194` |
| `src/novelvideo/services/canvas_assets.py:57`、`video_tasks.py:20,28,40` | 门面函数体内导入 production/canvas 的**私有** helper（`_probe_image_size` 等），本次的 `_has_filmcraft_action_context` 同形 |

### 初版验收判据的实际达成情况

| 初版判据 | 实况 |
| --- | --- |
| `check_import_boundaries --fail-on high,medium` exit 0、`finding_count=0` | **达成**（R5 实跑：finding_count=0，import_edges=1821；R6 实跑：finding_count=0，import_edges=1820；R7 实跑：finding_count=0，import_edges=1820——逐位相同） |
| `tests/test_architecture_boundaries.py` 全绿 | **达成**（在 128 passed 的 R5 批次、180 passed 的 R6 批次、206 passed 的 R7 批次里） |
| `executor.py` ≤3000 行、finding 消失 | **达成**，恰好 3000 行；恰好等于阈值、没有余量 |
| `tests/test_filmcraft_kb.py` 全绿且提示词字节不变 | **部分达成**：测试全绿，但提示词**不是**字节不变——R4/R5/R6/R7 有意改变 `action_context` 的判定，温情场景少一条规则、明确打戏措辞多回若干条，这是修复目的，不是回归 |
| 不改 `domain_boundaries.json`、不改任何断言/期望/夹具 | **R5 达成**（未触碰架构配置；新增断言是追加）。**R6 有唯一一处既有断言被更正**：`tests/test_filmcraft_kb.py:332` 里 `不要打斗，要看追逐` 由 `False` 改为 `True`，是工作单点名的小句语义，不是放宽——阈值、期望集合与夹具均未改。**R7 没有改动任何既有断言**：只追加 26 条用例，R6 的 80 条原样全绿 |

## Decision

**G-01（已完成）：分镜提示词组装移到 `src/novelvideo/workflow_runtime/storyboard_prompt.py`，
规则选择经 `src/novelvideo/services/production_contracts.py:150-178` 的 `inject_filmcraft_rules`
门面；`executor.py` 只留一行调用点（`:96` 导入、`:701` 调用）。**

门面**不是**新文件：`production_contracts.py` 已经是 WorkflowRuntime 在用的生产阶段门面，
里面已有多个「函数体内 lazy import」的转发，再加一个只有单个转发函数的文件收益为零。

**G-01-R4（已完成）：`action_context` 的显式键优先 + 短语级安全词表。**
剔除单字 `推/拉/走/跑/摔` 与两字词 `动作`，温情走位不再误带打斗规则。

**G-01-R5（已完成）：决策集中到 production 一处，经既有门面暴露；词表只此一份；显式值只认 bool。**
（R5 的「否定保守、8 字符窗口」一条已由 R6 取代，见下。）

1. `filmcraft_kb` 持有唯一权威词表 `_ACTION_CONTEXT_CUES`、否定规则与
   `_has_filmcraft_action_context`。词表补回被 R4 删掉的明确打戏措辞（11 条，见上）。
2. `production_contracts.has_filmcraft_action_context(source_text) -> bool`（R6 后 `:181`）是
   **唯一面向 workflow 的动作语境决策 API**；函数体内导入 production 实现，避免模块级绑定把
   monkeypatch 钉死。
3. `storyboard_prompt.py` **不再**自带词表；`_action_context` 通过
   `production_contracts.has_filmcraft_action_context(request)` 取决策（模块属性调用，
   import 期不绑定函数对象）。`build_storyboard_filmcraft_contract` 的签名与
   `params["action_context"]` 行为（总是传 bool）保持不变。
4. 为什么选择「门面 + 单份词表」而不是保留两份拷贝：两份拷贝的唯一保护是行为测试，
   而词表每加一个词都要改两处、漏改不会红；R4→R5 之间已经真实发生过一次漏改
   （R4 收窄时删掉了画布侧的合法措辞）。集中后，加词只改一处，分镜侧自动跟随。
5. 为什么保留关键词路径而不是全局关掉：规则本身是产品要的手艺约束；
   全局关掉会让画布侧不传键的调用方静默丢失打戏规则，属于另一种回归。

**G-01-R6（本次）：否定的作用域改成小句、补回八个明确打斗措辞、门面调用路径只留一条、决策与注入共享 12,000 字归一。**

1. **否定改小句级**（`_CLAUSE_BOUNDARIES` `:80-93`，`_negated_action_cue` `:493`）：
   否定只压自己小句里的词条。这条同时修两类错误——漏报（`不得/严禁/不可/不准/不许/不出现`
   这些短语原先根本不在表里）与跨句泄漏（`不要温情，要打斗` 原先被上一句的否定压掉）。
2. **词表补 8 条**（`:33-71`，37 条）：`摔打/肉搏/对打/打起来/开打/群殴/决战/交战`。
   仍是短语级，**没有**把 R4 删掉的裸 `推/拉/走/跑/摔` 或裸 `动作` 加回来。
3. **门面调用路径唯一**（`storyboard_prompt.py:16,33,59`）：去掉 import 期绑定的
   `from novelvideo.services.production_contracts import inject_filmcraft_rules`，
   注入与决策都走 `production_contracts.` 模块属性。理由：R5 只统一了**决策**的调用路径，
   注入仍是 import 期绑定，于是「门面 monkeypatch 有效」这个不变量只对一半代码成立——
   同一个模块里两条路径行为不同，是更难查的漂移。
4. **文本归一共享**（`ACTION_CONTEXT_TEXT_LIMIT`、`normalize_action_context_text`）：
   截断只做一次、只定义一处，决策与规则选择读同一段字符。
5. **不动无关触发族**：`_CLAUSE_BOUNDARIES` 与 `_ACTION_CONTEXT_NEGATIONS` 只被
   `_negated_action_cue` 使用，其它 30 多个 trigger 仍走原来的 `_keyword_hit` 路径，
   不受本次改动影响（收益/风险见 R6 评价矩阵）。

**G-01-R7（本次）：否定改成「附着判定」，`、` 改成列举分隔符。**

1. **最新否定 + 附着判定**（`_latest_negation` `:553`、`_negation_attached` `:630`、
   `_negated_action_cue` `:657`）：取词条之前最新的否定短语，再看它与词条之间的那段文字。
   空或只有功能词 → 压掉命中；夹着实词 → 否定修饰的是那个实词，命中保留。
   这样「没有台词的三镜打斗」是「没有台词的打斗戏」，「不要打斗」才是禁止打斗。
2. **`、` 移出小句边界**（`_CLAUSE_BOUNDARIES` `:88-100`、`_ENUMERATION_SEPARATORS` `:102`）：
   列举出来的是一串并列项，不是两个小句。禁止词在第一个列举项前时要管住后面所有项，
   除非同一段里出现肯定标记。
3. **词表与 API 一律不动**：`_ACTION_CONTEXT_CUES` 仍是 R6 的 37 条短语，
   没有加回裸 `推/拉/走/跑/摔` 或裸 `动作`；`production_contracts` 的两个门面函数签名、
   `storyboard_prompt` 的调用路径、`params["action_context"]` 的 bool 合同全部未动
   （工作单只允许改 `filmcraft_kb.py`、测试与本 ADR）。
4. **有界本地解析**：只用 `str.rfind/find/replace/split` 与元组常量，没有分词器、
   没有正则爆炸、没有新依赖；解析范围严格限定在「词条之前、最近一个小句边界之后」这段文字。

**G-01-R8（本次，第六版）：把间隔判据从「一张扁平白名单」拆成「功能词 / 介词框架 / 词条附属语」三类结构，列举判定改为看前项。**
（下面 R7／R6 段落里的「本次」指各自那一版。）

1. **桥接词表只留功能词**（`_NEGATION_BRIDGE_TOKENS` `:129`）：删掉 `画面/镜头/场面/里/中`。
   全局字符串替换没有上下文，`中` 会从「中景」里被削掉、`画面`+`里` 会把「在画面里」整段抹平，
   于是纯禁止的间隔非空 → 漏判。内容名词与方位字不再进这张表。
2. **介词框架**（`_strip_locative_frames` `:651`、`_NEGATION_FRAME_PREPOSITIONS/SUFFIXES/MAX_ADJUNCT`
   `:164-166`）：`在` + ≤4 字 + 可选方位后缀。介词引出的是状语，框架内的名词（`画面/人群/巷口` …）
   不会成为否定的宾语；框架有界、遇 `、` 即停。`不要在画面里出现打斗` 因此重新判 `False`。
3. **词条附属语**（`_NEGATION_CUE_ADJUNCT_TOKENS` `:173`，先于功能词剥离）：景别词
   `中景/特写/近景/远景/全景/中近景/大特写` 与数量词 `两人/三人/多人/几人/数人/众人/一个人/…`。
   它们长在词条身上（怎么拍、几个人），不是第二个被否定的宾语，所以 `不要中景打斗`、`不要特写打斗`
   判 `False`。其余实词仍按 R7 读：否定修饰那个实词，命中保留。
4. **列举判定改为看前项**（`_only_bridge_tokens_or_cues` `:708`、`_negation_attached` `:727`）：
   `、` 前面的每一段必须是「脚手架 + 已列举的词条」，最后一项不再重新审查实词。
   于是 `两人群殴`/`多人群殴`/`三人追逐`/`近身肉搏` 都被同一禁止覆盖；
   `不要温情、两人打斗` 因第一项 `温情` 不是词条而保持 `True`（R7 语义不变）。
5. **肯定标记仍先判、否定短语与小句边界一律不动**：`_AFFIRMATIVE_CONTEXT_MARKERS`（`:195`）、
   `_ACTION_CONTEXT_NEGATIONS`（`:201`，24 条）、`_CLAUSE_BOUNDARIES`（`:97`）、
   `_ENUMERATION_SEPARATORS`（`:111`）、37 条词表（`:33-71`）全部未改；没有加回裸 `推/拉/走/跑/摔`
   或裸 `动作`；`production_contracts`、`storyboard_prompt`、`executor` 一个字都没动。

**G-01-R9（本次，第七版）：词条删除改最长匹配；间隔判据补一类有界无介词场景框架。**
（下面 R8／R7／R6 段落里的「本次」指各自那一版。）

1. **最长匹配**（`_strip_tokens` `:646`）：每次调用把 token 按长度降序排好再走原来的多轮替换循环，
   等长保持表内顺序。三处调用点（脚手架 = 附属语 + 功能词、列举项词条、`_ACTION_CONTEXT_CUES`）
   一起受益，于是 `大特写`/`中近景`/`武打动作` 整体识别，不再留 `大`/`中`/`动作` 碎片。
   删除动作、重复轮次、「删一个暴露另一个」的语义都没变。
2. **有界无介词场景框架**（`_scene_frame_end` `:691`、`_strip_scene_frames` `:723`、
   `_strip_locative_frames` `:737`）：形状固定为 `镜头|画面|场面` + 可选方位后缀
   + 可选 `出现|有`，`_NEGATION_SCENE_FRAME_MAX = 5`（`:204`）封顶。
   三条界同时成立：(a) 裸 `画面`/`镜头` 不算框架（R8 的词表卫生用例继续通过）；
   (b) 只有裸 `场面`（`_NEGATION_SCENE_BARE_NOUNS` `:202`）是框架变体——它是调度词，不是宾语；
   (c) 命中前先过 `_inside_listed_cue`（`:673`），落在更长词条里的场景名词（`动作场面`）不剥。
   框架内没有字符能匹配 `、`，因此天然不跨列举分隔符，也不会吞掉整个小句。
3. **R8 的介词框架逐行保留**：原循环抽成 `_strip_prepositional_frames`（`:755`），
   `_strip_locative_frames` 只是「先介词、后场景」的组合，`在画面里` 的既有行为一位没变。
4. **否定短语、词条表、小句边界、肯定标记一律不动**：`_ACTION_CONTEXT_NEGATIONS`（`:214`，24 条）、
   37 条词表（`:33-71`）、`_CLAUSE_BOUNDARIES`（`:97`）、`_ENUMERATION_SEPARATORS`（`:111`）、
   `_AFFIRMATIVE_CONTEXT_MARKERS`（`:208`）全部未改；`_NEGATION_BRIDGE_TOKENS`（`:129`）一个词都没加
   （工作单的 STOP 条件）；`production_contracts`、`storyboard_prompt`、`executor` 一个字都没动。

### R9 评价矩阵

| 选项 | 正确性风险 | 改动面 | 可逆性 | 结论 |
| --- | --- | --- | --- | --- |
| A 把 `大特写/中近景/武打动作` 提到各自短词前面（只调表内顺序） | med：修好本次六条里的三条，但下一个「长词排短词后面」的新词条会再犯一次；且顺序依赖是隐式约定，没人会记得 | low | high | rejected：治标，且把正确性挂在元组书写顺序上 |
| B 位置感知的长词优先扫描（自己写一遍 `startswith` 循环 + 跳过重叠） | low-med：与 A 的语义等价，但多一段自研匹配逻辑，替代不了一行 `sorted`；收益只是「不必每轮排序」 | low-med | high | rejected：本仓没有性能压力，简单性优先 |
| **C 每次调用按长度降序（等长稳定）后复用原替换循环 + 有界无介词场景框架（chosen）** | low-med：最长匹配本身只影响**重叠**词条（`武打`/`武打动作`），已用单元用例钉住；场景框架有 `_NEGATION_SCENE_FRAME_MAX` 与「裸 `画面/镜头` 不算框架」两条界（见 Risks R25） | low：1 个生产文件 + 1 个测试文件 + 本 ADR | high：还原 `_strip_tokens` 与 `_strip_locative_frames` 即回退 | **chosen（实际落地）** |
| D 把 `镜头/画面/场面/里/中` 重新放回全局桥接词表来覆盖无介词框架 | high：R8 的 R20 会原样复发（`不要中景打斗` 被削成 `景`、`不要画面` 被整段抹掉），工作单也明确禁止「把内容名词加回全局桥接词表」 | low | high | rejected：与 R8 的目标和 STOP 条件直接冲突 |
| E 用正则一次匹配 `(镜头|画面|场面)[里中内上下外间]?(出现|有)?` 而不设长度上限 | med-high：`场面` 单独成词时会把 `动作场面` 这类**真实词条**削成 `动作`，列举判定随之失效（正是 R9 的第三类复现同源问题） | low | high | rejected：必须配 `_inside_listed_cue` 守卫才安全，不如把界写进形状本身 |

### R8 评价矩阵

| 选项 | 正确性风险 | 改动面 | 可逆性 | 结论 |
| --- | --- | --- | --- | --- |
| A 把 `中景/特写/画面/镜头/场面/里/中` 继续加进桥接词表（R7 白名单扩容） | high：`没有画面的打斗`、`不要镜头特写` 这类把名词当宾语的输入会被一并压掉；且解决不了列举项数量词问题 | low | high | rejected：改了错的地方，问题在判据分类不在词表长度 |
| B 只要间隔里没有 `的` 就算附着 | high：`没有台词打斗`（无 `的`）会被判成禁止，等于把 R7 刚修好的漏报换个形状改回来 | low | high | rejected：与 R7 的语义直接冲突 |
| C 引入分词器／句法分析判断否定辖域 | high：新增依赖 + 不确定性，本仓无同类先例 | med-high | low | rejected：与 R7 评价矩阵 C 同一理由，仍未证明必要 |
| **D 功能词 + 有界介词框架 + 词条附属语三类结构判据，列举看前项（chosen）** | low-med：附属语白名单是枚举的，白名单外的描述语（`血腥/暴力`）仍按实词处理 = 保住召回；另有 `在`+≤4 字的框架边界（见 Risks R21/R22） | low：1 个生产文件 + 1 个测试文件 + 本 ADR | high：还原三个 helper 即回退 | **chosen（实际落地）** |
| E 列举一律按「前面的项是词条就压全部，否则全不压」，不做附属语白名单 | high：`不要中景打斗`／`不要在画面里打斗` 仍判 `True`，工作单六条复现里四条修不好 | low | high | rejected：修不了本次目标 |

### R7 评价矩阵

| 选项 | 正确性风险 | 改动面 | 可逆性 | 结论 |
| --- | --- | --- | --- | --- |
| A 维持「同小句即压掉词条」，只把 `、` 从边界里删掉 | high：六个明确要打的输入仍全判 False（工作单的头号问题一个字没修） | low | high | rejected：修不了本次目标 |
| B 只做附着判定，`、` 仍当边界 | high：「全片不要打斗、追逐」「不要出现打斗、爆炸、群殴」两个纯禁止仍判 True | low | high | rejected：工作单点名的三类禁止里有两类修不好 |
| C 引分词/句法分析判断否定辖域 | high：新增依赖 + 不确定性，本仓无同类先例；中文否定辖域本身就是难题 | med-high | low | rejected：附着实词表 + 桥接词白名单已覆盖全部点名输入 |
| **D 最新否定 + 桥接词白名单 + 列举感知（chosen）** | low-med：白名单是枚举的，可能漏掉没列进去的功能词（见 Risks R17），但漏掉时的行为是「不压词条」= 找回召回，不是误报 | low：1 个生产文件 + 1 个测试文件 + 本 ADR | high：还原函数即回退 | **chosen（实际落地）** |
| E 把否定做成「只有紧邻词条（0 间隔）才算否定」 | high：`全片不得出现打斗`、`不可有打斗`、`不要打斗，也不要追逐` 会重新判 True，等于把 R6 修好的漏报改回来 | low | high | rejected：与 R6 的七条禁止用例直接冲突 |

### R6 评价矩阵

| 选项 | 正确性风险 | 改动面 | 可逆性 | 结论 |
| --- | --- | --- | --- | --- |
| A 保留 8 字符扁平窗口，只把新否定短语加进表 | high：跨逗号泄漏修不掉（`不要温情，要打斗` 仍 False），漏报只修一半 | low | high | rejected：工作单点名的四类输入有两条修不好 |
| B 引入分词/NLP 依赖做小句切分 | med：新增依赖与不确定性，且本仓无同类先例 | med-high | low | rejected：纯字符串的小句切分足够，不需要依赖 |
| **C 小句边界字符 + 表内短语匹配（chosen）** | low：边界集合是固定 12 个字符，覆盖中英常见句读；例外只有既有的 `别` 规则 | low：1 个生产文件 + 1 个门面 docstring + 1 个 workflow 文件 | high：还原函数即回退 | **chosen（实际落地）** |
| D 把决策整体搬到 `combat_film_contract._negated` | med：那条实现带 12 字符窗口与 `_NON_NEGATING_PHRASES` 覆盖逻辑，语义与本次要求不同（会重新引入窗口截断） | med | med | rejected：借形可以，借语义会带回本次要修的窗口问题 |

### R5 评价矩阵

| 选项 | 正确性风险 | 改动面 | 可逆性 | 结论 |
| --- | --- | --- | --- | --- |
| A 保留两份 `_ACTION_CONTEXT_CUES` + 行为测试 | med：漏改一处不报警（R4 已实际发生） | low | high | rejected：评审点名，且无机制保证 |
| B 把词表搬到中立域（`services/` 或 `ports/`），两侧都导入 | low-med：多一次搬迁 + 6 个导入点；`services` 放纯常量仍需回答 owner 问题 | med-high | med | rejected：改动面大于收益，门面已经能承载决策 |
| **C 决策留在 `filmcraft_kb`，经 `production_contracts` 门面暴露（chosen）** | low：只多一个纯转发；新失败模式（import 期绑定）用行为级守卫测试覆盖 | low：1 个生产文件 + 1 个门面文件 + 1 个 workflow 文件 | high：还原调用即回退 | **chosen（实际落地）** |
| D 把词表塞进 `storyboard_prompt` 再反向导入 | high：workflow→production 直连，规则边界红 | low | high | rejected：等于把 R4 的违规改回来 |

## Rejected options

- **A（维持直连 + 精确豁免）** — 否决，因为它是改门禁配置让门禁变绿，不是记录一次已被批准的例外；
  且对行数棘轮毫无作用。
- **B（新建 `services/filmcraft_rules.py`，第二版未采纳的形态）** — 其形态设计本身无误，
  但对本次目标而言是多余文件；`production_contracts.py` 已承担同一角色。
- **C（迁移 `production/filmcraft_kb.py` 到中立域）** — 改动面比 B2 大，对行数棘轮零贡献，
  会把画布侧的无关 diff 混进评审。消费者达到三个及以上域时重新有竞争力。
- **D（port + 依赖注入）** — 没有第二种实现，抽象不为自己买单。
- **E（规则作为 run inputs）** — 把一行 import 的耦合换成跨层数据合同与历史 run 兼容问题。
- **F（删规则 / 全局关关键词路径）** — 见 Decision 末段。
- **R5-A（两份词表 + 测试兜底）** — 否决：R4→R5 已实证漏改不可见（见 R5 评价矩阵）。
- **R5-B（词表搬到中立域）** — 否决：搬迁成本高于「把决策放到已有门面」，
  且不解决「谁决定、决定什么」的语义归属。
- **R6-A（只加否定短语，不动窗口）** — 否决：见 R6 评价矩阵 A，跨逗号泄漏修不掉。
- **R6-B（引 NLP/分词）** — 否决：见 R6 评价矩阵 B，收益不足，新增依赖。
- **R6-D（复用 `combat_film_contract._negated`）** — 否决：那条实现自带 12 字符窗口，
  会把 R6 刚修掉的窗口截断问题原样带回来。借它的**形状**（先找小句左边界）可以，
  借它的**窗口**不行。
- **R6-E（把 `normalize_action_context_text` 提到门面再暴露给 workflow）** — 否决：
  workflow 侧只需要一个 bool，暴露归一函数等于把「怎么归一」变成第二份合同；
  归一留在 production 内部，由决策与注入共用一个实现即可（当前落地形态）。
- **R7-A（只删 `、` 边界，不做附着判定）** — 否决：见 R7 评价矩阵 A，
  工作单的六条漏报一条都修不好。
- **R7-B（只做附着判定，保留 `、` 边界）** — 否决：见 R7 评价矩阵 B，
  纯禁止里的 `不要打斗、追逐` 与 `不要出现打斗、爆炸、群殴` 仍会误判成打戏请求。
- **R7-E（要求否定与词条零间隔）** — 否决：见 R7 评价矩阵 E，
  会把 R6 已经修好的 `全片不得出现打斗`/`不可有打斗` 漏报原样带回来，
  与既有的七条 R6 禁止用例直接冲突（工作单的 STOP 条件之一）。
- **R9-A（只把 `大特写/中近景/武打动作` 在表里提到短词前面）** — 否决：见 R9 评价矩阵 A，
  只修好本次六条里的三条，正确性仍挂在元组书写顺序这一隐式约定上。
- **R9-B（自研位置感知的长词优先扫描）** — 否决：见 R9 评价矩阵 B，
  与按长度排序语义等价，却要多一段自研匹配逻辑，本仓也没有性能压力。
- **R9-D（把 `镜头/画面/场面/里/中` 加回全局桥接词表）** — 否决：见 R9 评价矩阵 D，
  直接触发 R8 的 R20 复发，且违反本次工作单的 STOP 条件（不得把内容名词加回全局桥接词表）。

## 关于 `exclude_target_modules`：是记录已批准的例外，还是规避门禁

**结论：不动该配置，走门面。**

1. **机制层面**：`check_import_boundaries.py:136-140` 只做模块名精确相等匹配，条目就是一根字符串，
   没有 owner/批准人/到期时间/豁免级理由，且作用于整个 workflow 域对该模块的全部调用点。
2. **门禁层面**：`load_config` 只认 `domains`/`rules`，没有 baseline；豁免是唯一出口，出口上没有审批字段。
3. **纪律层面**：先直连 → 测试红 → 加一行配置 → 绿，这个顺序本身就是规避；规则原文点名的正解是
   「阶段合同或应用门面」，而本仓已有 40+ 处门面先例，绕开门面的代价是零。
4. **语义层面**：`filmcraft_kb` 确实具备已发布合同模块的特征（无内部依赖、确定性纯函数），
   但「像合同」不等于「已批准」。现在的实现只把 `rule_id` **展示**在提示词块的方括号头里
   （`storyboard_prompt.py:68`），**不落库、不做归因引用**。
5. **技术细节（若将来真走豁免）**：豁免匹配的是 `ast.ImportFrom.module`，所以只有
   `from novelvideo.production.filmcraft_kb import ...` 会被豁免；
   `from novelvideo.production import filmcraft_kb` 的 `target_module` 是 `novelvideo.production`，
   仍然报 finding。

## Interface contracts（按实际代码）

### 实际：`src/novelvideo/services/production_contracts.py`（域：foundation）

```python
def inject_filmcraft_rules(
    *, node_type: str, params: Mapping[str, Any] | None = None,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    source_text: str = "", creation_stage: str = "",
) -> list[dict[str, str]]

def has_filmcraft_action_context(source_text: str) -> bool
```

- `inject_filmcraft_rules`：`:150-178`。逐字转发 `production.filmcraft_kb.inject_filmcraft_rules`，
  **函数体内** `from ... import`（模块级导入会缓存函数对象，`tests/test_filmcraft_kb.py:180` 的
  monkeypatch 就失效）。
- `has_filmcraft_action_context`：`:181-194`。函数体内导入
  `production.filmcraft_kb._has_filmcraft_action_context`（私有 helper，与 `canvas_assets.py:57`
  的同形先例一致），返回 `bool(...)`。自身不捕获、不包装异常；两个名字都在 `__all__` 里
  （`:359`、`:360`）。
- 分镜侧拿到的**只是这个 bool**；词表与否定规则都不跨域。R6 只改了 `:181` 的 docstring
  （把「否定窗口」改成「小句级否定 + 与注入同源的 12,000 字归一」），签名与 `__all__` 未动。

### 实际：`src/novelvideo/workflow_runtime/storyboard_prompt.py`（域：workflow，R5 时 74 行，R6 后 77 行）

```python
def build_storyboard_filmcraft_contract(
    request: str,
    reuse_target_node_ids: Sequence[str] = (),
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
) -> str
```

- 入参是**位置参数**（非 keyword-only），初版第 212–218 行的 keyword-only 形态不对。
- 规则块逐条格式（与画布侧 `prompt_optimizer.py` 同形），以 `"\n"` 连接：

  ```
  [{rule_id} | stage={stage} | trigger={trigger}]
  执行：{instruction}
  禁忌：{avoid}
  ```

- 非空时前缀是**当前**头部（`storyboard_prompt.py:18-21`）：

  ```
  \n全片手艺规则（与图像/视频节点编译器同源，不是风格建议），逐条落到 shots 字段里：\n
  ```

  `rule_id` 只显示在每条的方括号头里，不写进任何持久化字段。
- 无匹配 → 返回 `""`（不产生空标题）。
- `shot_count` 语义不变：`reuse_target_node_ids` 非空才写 `params["shot_count"]=len(...)`，
  为空时**不传该键**（不是传 0）。
- `action_context`（R4 引入、R5 收口、R6 对齐路径）：总是传 bool（R5 时 `:53`，R6 后 `:53`）。
  判定来自 `production_contracts.has_filmcraft_action_context(request)`（R5 时 `:34`，R6 后 `:33`）——
  本模块**没有**本地词表，也没有子串实现。
- 导入形态（R6 后）：只剩 `:16` `from novelvideo.services import production_contracts`。
  决策（`:33`）与注入（`:59`）都按模块属性在调用点取，import 期不绑定任何门面函数。
  **R5 的 `:17` `from novelvideo.services.production_contracts import inject_filmcraft_rules`
  已删除**——它就是「门面 monkeypatch 对注入无效」的原因。
- `__all__ = ["build_storyboard_filmcraft_contract"]`；**没有** `render_filmcraft_rules_block`。

### 实际：`src/novelvideo/production/filmcraft_kb.py`（域：production，R5 时 695 行，R6 后 767 行，R7 后 933 行，R8 后 1038 行，R9 后 1137 行）

```python
_ACTION_CONTEXT_CUES: tuple[str, ...]         # R5 时 :30-61/29 条；R6 后 :33-71/37 条；R7/R8/R9 均未动，唯一权威
_CLAUSE_BOUNDARIES: tuple[str, ...]           # R6 :80-93，12 个小句边界（取代 _NEGATION_WINDOW=8）
                                              # R7 :88-100，11 个（`、` 移出）；R8 后 :97，R9 未动
_ENUMERATION_SEPARATORS: tuple[str, ...]      # R7 :102，("、",)；R8 后 :111，R9 未动
_NEGATION_BRIDGE_TOKENS: tuple[str, ...]      # R7 :111-146，功能词 + 内容名词；R8 后 :129，
                                              #   只留功能词（删 画面/镜头/场面/里/中）；R9 一个词都没加
_NEGATION_FRAME_PREPOSITIONS/SUFFIXES/MAX_ADJUNCT  # R8 新增 :164-166，介词框架（在 + ≤4 字 + 方位后缀）；R9 未动
_NEGATION_CUE_ADJUNCT_TOKENS: tuple[str, ...] # R8 新增 :173，景别词 + 数量词（词条自身的附属语）；R9 未动
_NEGATION_SCENE_NOUNS/BARE_NOUNS/BRIDGE_WORDS/FRAME_MAX  # R9 新增 :201-204，无介词场景框架（封顶 5 字）
_AFFIRMATIVE_CONTEXT_MARKERS: tuple[str, ...] # R7 :148-152，要/而是/需要/改成；R8 后 :195；R9 后 :208，未动
_ACTION_CONTEXT_NEGATIONS: tuple[str, ...]    # R6 :94-119，24 条；R7 后 :154-179；R8 后 :201；R9 后 :214，内容未动
_NON_NEGATING_PREFIXES: tuple[str, ...]       # R6 :124-137；R7 后 :184-197（`别` 的普通词例外）；R9 后 :244
ACTION_CONTEXT_TEXT_LIMIT = 12_000            # R6 :459；R7 后 :519；R8 后 :566；R9 后 :579

def normalize_action_context_text(source_text: object) -> str      # R6 :462；R7 后 :522；R8 后 :569；R9 后 :582，公开
def _latest_negation(clause, clause_start, lowered) -> tuple[int, int] | None   # R7 :553；R8 后 :600；R9 后 :613
def _strip_tokens(text, tokens) -> str                            # R7 :586；R8 后 :633；R9 后 :646（最长匹配）
def _inside_listed_cue(text, start, end) -> bool                  # R9 新增 :673
def _scene_frame_end(text, start) -> int                          # R9 新增 :691
def _strip_scene_frames(text) -> str                              # R9 新增 :723
def _strip_locative_frames(text) -> str                           # R8 新增 :651；R9 后 :737（介词 + 场景两类）
def _strip_prepositional_frames(text) -> str                      # R9 抽出 :755（R8 的原循环逐行未改）
def _strip_negation_scaffolding(text) -> str                      # R8 新增 :682；R9 后 :779
def _only_bridge_tokens(text) -> bool                             # R7 :604；R8 后 :693；R9 后 :790（三类结构判据）
def _only_bridge_tokens_or_cues(text) -> bool                     # R7 :612；R8 后 :708；R9 后 :807
def _has_affirmative_context_marker(text) -> bool                 # R7 :626；R8 后 :723；R9 后 :822
def _negation_attached(lowered, cue_index, negation) -> bool       # R7 :630；R8 后 :727（列举分支改写）；R9 后 :826
def _negated_action_cue(lowered: str, index: int) -> bool          # R5 时 :439-453；R6 后 :493；R7 后 :657；R8 后 :762；R9 后 :861
def _has_filmcraft_action_context(source_text: object) -> bool     # R5 时 :456-471；R6 后 :521；R7 后 :687；R8 后 :792；R9 后 :891
```

- `_has_filmcraft_action_context` 先过 `normalize_action_context_text`（strip + 12,000 字截断），
  再大小写不敏感地扫全部词条；任一**未被压掉**的命中即 `True`；空文本 `False`。
  R7 起「未被压掉」= 否定不在同一小句，**或**否定虽在同一小句但没附着在词条上
  （`_negation_attached`，`:826`）；`_latest_negation`（`:613`）保证只有最近的否定参与判断。
  R8 起「附着」的判据是结构（功能词 / 有界介词框架 / 词条附属语），而不是 R7 那张含内容名词的扁平白名单；
  R9 再补一类有界无介词场景框架，并把所有 token 删除改成最长匹配（`:646`）。
- `__all__` 从 R5 的 `["FILMCRAFT_KB_SCHEMA", "RULES", "inject_filmcraft_rules"]`
  扩到 `["ACTION_CONTEXT_TEXT_LIMIT", "FILMCRAFT_KB_SCHEMA", "RULES",
  "inject_filmcraft_rules", "normalize_action_context_text"]`（R6 时 `:761`；R7 后 `:927`；
  R8 后 `:1032`；R9 后 `:1131`，R7/R8/R9 都没有增删任何公开名）。两个 helper 里，
  `_negated_action_cue`/`_has_filmcraft_action_context` 仍是私有名、由门面显式导入；
  归一 helper 与上限常量是公开名（同域内共享，且让「共享截断」这件事可被测试直接钉住）。

### 未实施（初版主张，本版明确记录）

| 初版主张 | 现状 |
| --- | --- |
| 新增 `src/novelvideo/services/filmcraft_rules.py` | **不存在**；改用 `production_contracts.py` |
| `render_filmcraft_rules_block(rules, *, separator)` | **未实现**；拼装直接写在 `storyboard_prompt.py` |
| `services/__init__.py` 导出三个门面名 | **未实施** |
| `architecture_manifest.json` 追加 `filmcraft_rule_contract` | **未实施**：清单里 `grep filmcraft` 无命中 |
| T4 守卫测试（钉死豁免集合 + 门面可发现性） | **未实施**：两条 architecture 测试文件 R5、R6 均未触碰；R5/R6 的漂移守卫都是行为级测试，不在此列 |
| `build_storyboard_system_prompt` 承接整个提示词字面量 | **未实施**：字面量仍在 `executor.py` |

未实施不等于被否决：T3/T4 类登记与守卫测试仍是有价值的后续项，只是没有随 R5/R6 落地，
不得在验收记录里写成「已做」。

### 变更：`src/novelvideo/workflow_runtime/executor.py`（R6 只读，但相对 HEAD 并非未改）

- `:96` `from novelvideo.workflow_runtime.storyboard_prompt import build_storyboard_filmcraft_contract`
- `:701-702` `... {vision_context}{build_storyboard_filmcraft_contract(request, reuse_target_node_ids, director_vision, project_dna)}"`
- 文件 3000 行（阈值 3000），`check_file_sizes` 不再报它。
- **诚实更正**：`git status` 里该文件是 `M`，`git diff --numstat` = `2 1`（R4/R5 遗留的
  import + 调用点改动）。第三版 ADR 把它写成「未改／只读」是错的；R6 确实没有再动它。

## Work breakdown（实际状态）

| 任务 | 归属 | 状态 | 证据 |
| --- | --- | --- | --- |
| 门面（初版 T1） | `production_contracts.py:150-178` | **已完成（形态改为 B2）** | 无新文件；import 边界 `finding_count=0` |
| 组装外移 + executor 回落（T2） | `storyboard_prompt.py`、`executor.py` | **已完成** | `executor.py` 3000 行；focused 128 passed |
| 清单登记（T3） | `architecture_manifest.json` | **未实施** | 清单无 filmcraft 条目 |
| T4 守卫测试 | `tests/test_architecture_boundaries.py` | **未实施** | 未触碰该文件（R6 也未碰） |
| `action_context` 误报修复（G-01-R4） | `filmcraft_kb.py`、`storyboard_prompt.py` | **已完成** | focused 109 passed（R4 后实测） |
| 决策集中化 + 词表召回 + 非 bool 合同（G-01-R5） | `filmcraft_kb.py`、`production_contracts.py:181-194,359`、`storyboard_prompt.py:16,25-34,53`、`tests/test_filmcraft_kb.py:219-386` | **已完成** | focused 128 passed、freezone 105 passed |
| 小句级否定 + 补 8 词条 + 统一门面路径 + 共享 12,000 字归一（G-01-R6） | `filmcraft_kb.py:33-71,80-119,459-462,493,521,582`、`production_contracts.py:181`（仅 docstring）、`storyboard_prompt.py:16,33,59`、`tests/test_filmcraft_kb.py:437-670` | **已完成** | focused **180 passed**、freezone 105 passed、相邻合同批 219 passed、行为直读（见「现场证据（R6）」） |
| 否定附着判定 + `、` 列举语义（G-01-R7） | `filmcraft_kb.py:88-102,111-152,553,586,604,612,626,630,657`、`tests/test_filmcraft_kb.py:673-804` | **已完成** | focused **206 passed**、单文件 106 passed、freezone 105 passed、相邻合同批 219 passed、行为直读（见「现场证据（R7）」） |
| 直接禁止的脚手架分类 + 列举项描述语（G-01-R8） | `filmcraft_kb.py:129,164-173,651,682,693,708,723,727`、`tests/test_filmcraft_kb.py:807-954` | **已完成** | focused **228 passed**、单文件 128 passed、freezone 105 passed、行为直读（见「现场证据（R8）」） |
| 词条最长匹配 + 无介词场景框架（G-01-R9） | `filmcraft_kb.py:201-204,646,673,691,723,737,755`、`tests/test_filmcraft_kb.py:956-1110` | **已完成** | focused **259 passed**、单文件 159 passed、freezone 105 passed、行为直读（见「现场证据（R9）」） |
| T5 收 `canvas_reads.py`（G-01 之外） | `canvas_reads.py` | **未实施（预存债务）** | `check_file_sizes` 仍报 3056 行 |

## Tests（R5 钉住的行为 + R6 新增 + R7 新增 + R8 新增 + R9 新增）

`tests/test_filmcraft_kb.py`（R5 时 431 行，R6 后 670 行，R7 后 804 行，R8 后 954 行，R9 后 1110 行；R9 后单文件 159 passed）：

| 行（R6 后） | 测试 | 钉住什么 |
| --- | --- | --- |
| `:149` | `test_storyboard_handler_prompt_carries_filmcraft_rules` | 分镜提示词仍带 filmcraft 规则；规则为空时不残留标题 |
| `:186` | `test_storyboard_prompt_ignores_ordinary_motion_words` | 温情走位不带 `craft.action_fragment_shots.v1`（R4 的原始误报） |
| `:206` | `test_storyboard_prompt_keeps_combat_rule_for_explicit_action_scene` | 明确打戏仍带规则 |
| `:219` | `test_storyboard_prompt_ignores_negated_combat_wording` | 「全片不要打斗，也不要追逐」不带规则/「刀锋特写」（评审输入，走真实 handler 路径） |
| `:233` | `test_inject_filmcraft_rules_honors_explicit_action_context` | `False` 压制「打斗」；`True` 在温情文本上也能开；不传键走关键词路径 |
| `:261` | `test_inject_filmcraft_rules_never_enables_on_non_bool_action_context` | `"false"/"true"/"no"/0/1/""/None/[]/{}` 一律不开 |
| `:294` | `test_legacy_keyword_path_keeps_clear_combat_wording` | 旧调用方召回 `动作片/武打动作/摔跤/动作短片/动作剧/扭打` |
| `:315` | `test_legacy_keyword_path_ignores_warm_ordinary_blocking` | 旧调用方在温情走位上仍不开 |
| `:332` | `test_action_context_decision_ignores_negated_combat_wording` | 门面决策本身：否定=False、温情=False、明确打戏=True；**R6 把 `不要打斗，要看追逐` 由 False 更正为 True** |
| `:350` | `test_storyboard_contract_follows_the_facade_decision` | 漂移守卫（行为级）：monkeypatch 门面**决策**，分镜合同跟着变。**不 import production 私有词表** |
| `:389` | `test_storyboard_handler_selects_rules_for_reuse_existing_targets` | `reuse_existing` 时 `shot_count` 被真的传进规则选择器 |
| `:526` | `test_action_context_decision_rejects_every_forbidden_negation`（7 参数） | R6：七种禁止说法决策为 False，分镜合同也不含打斗规则/「刀锋特写」 |
| `:540` | `test_action_context_decision_keeps_affirmative_clause_after_negation`（4 参数） | R6：否定小句之后的肯定小句判 True，并带出分镜打斗规则 |
| `:554` | `test_negation_never_crosses_a_clause_boundary`（12 参数，12 个边界字符） | R6：标点即边界，否定既不越界压下一句，也不被越界忽略 |
| `:564` | `test_every_required_negation_phrase_controls_its_own_clause`（18 参数） | R6：工作单要求的 18 个否定短语逐个生效，且只在自己小句内生效 |
| `:573` | `test_clause_scope_keeps_the_ordinary_word_exception_for_bie` | R6：`别` 的普通词例外仍在（`特别激烈的打斗` = True，`别打斗` = False） |
| `:583` | `test_r6_combat_cues_reach_legacy_callers_and_storyboard`（8 参数） | R6：八个补回词条对门面决策、直接注入、门面注入、分镜合同四条路径都命中 |
| `:599` | `test_storyboard_contract_follows_the_facade_injection` | R6：monkeypatch 门面**注入器**必须控制分镜合同（R5 的 import 期绑定会让它失效） |
| `:644` | `test_action_context_text_limit_is_shared_by_decision_and_injection` | R6：12,000 字之后的词条对决策/门面注入/直接注入都不可见，之内的都可见 |
| `:723` | `test_r7_other_negated_noun_does_not_suppress_the_combat_cue`（6 参数） | R7：六个「否定的是别的名词」的明确打斗输入判 True，且门面决策/直接注入/门面注入/分镜合同四条路径都带 `craft.action_fragment_shots.v1`（并含「刀锋特写」） |
| `:745` | `test_r7_enumeration_prohibition_covers_every_listed_cue`（3 参数） | R7：三个 `、` 列举的纯禁止判 False，四条路径都不带打斗规则 |
| `:760` | `test_r7_direct_attachment_still_negates`（8 参数） | R7：直接附着与跨 `、` 列举仍算禁止（`不得出现打斗`/`不可有打斗`/`不要打斗，也不要追逐`/`禁止任何打斗` …） |
| `:769` | `test_r7_reports_the_negated_other_noun_not_the_cue`（5 参数） | R7：直接调 `_negated_action_cue` 断言「否定 + 实词 + 词条」不压词条（附着判定的单元级钉法） |
| `:784` | `test_r7_enumeration_affirmative_marker_reopens_the_cue`（3 参数） | R7：同一列举段里的肯定标记重新打开词条（`不要打斗、要看追逐` = True） |
| `:795` | `test_r7_enumeration_separator_is_not_a_clause_boundary` | R7：`、` 不在 `_CLAUSE_BOUNDARIES` 里、`_ENUMERATION_SEPARATORS == ("、",)`，且 R6 的 `不要温情、要打斗` / `不要打斗、要温情` 两个期望仍成立 |
| `:845` | `test_r8_scaffolded_prohibition_suppresses_the_cue`（7 参数） | R8：六条复现 + `不要在画面里出现打斗` 判 False，且门面决策/门面注入/直接注入/分镜合同四条路径都不带打斗规则 |
| `:863` | `test_r8_every_cue_occurrence_in_a_prohibition_is_negated`（7 参数） | R8：单元级钉法——禁止项里出现的**每个**词条都被 `_negated_action_cue` 判为已否定（覆盖列举的每一项） |
| `:886` | `test_r8_enumeration_item_may_carry_descriptor_wording`（2 参数） | R8：列举项可带描述语（`不要打斗、近身肉搏`、`不要出现打斗、爆炸、特写群殴`）仍是纯禁止 |
| `:899` | `test_r8_enumeration_without_a_listed_cue_stays_affirmative` | R8：`不要温情、两人打斗` 保持 True（第一项不是词条 → 不是禁止清单）；对照 `不要打斗、两人追逐` 判 False |
| `:917` | `test_r8_affirmative_marker_still_reopens_the_cue`（3 参数） | R8：肯定标记仍重新打开同一列举段里的词条 |
| `:926` | `test_r8_bridge_tokens_keep_no_content_nouns` | R8：`画面/镜头/场面/里/中` 不在桥接词表里；`_only_bridge_tokens` 对 `在画面里`/`中景`/`两人` 放行，对 `画面`/`台词`/`慢动作` 不放行 |
| `:942` | `test_r8_keeps_the_r6_cue_and_negation_vocabulary` | R8：37 条词条、24 条否定短语、小句边界与列举分隔符一条未动；裸 `推/拉/走/跑/摔/动作` 仍不在词表里 |
| `:989` | `test_r9_prohibition_suppresses_the_cue_and_the_storyboard_rule`（6 参数） | R9：六条复现全部判 False，且分镜合同/门面注入/直接注入三条路径都不带 `craft.action_fragment_shots.v1` |
| `:1004` | `test_r9_every_cue_occurrence_in_a_prohibition_is_negated`（6 参数） | R9：单元级钉法——禁止项里出现的**每个**词条都被 `_negated_action_cue` 判为已否定（含列举项 `武打动作`） |
| `:1026` | `test_r9_longest_match_keeps_multi_character_adjuncts_whole`（2 参数） | R9：`大特写`/`中近景` 整段删除（`_strip_negation_scaffolding` 返回空串），不再留下 `大`/`中` |
| `:1039` | `test_r9_longest_match_applies_to_cue_removal_too` | R9：词条删除同样最长匹配——`武打动作` 先于 `武打`，`_strip_tokens(...) == ""`，`_only_bridge_tokens_or_cues("武打动作")` 为 True |
| `:1055` | `test_r9_no_preposition_scene_frame_is_scaffolding`（5 参数） | R9：`镜头里`/`镜头里出现`/`画面里`/`画面里出现`/裸 `场面` 被当脚手架放行 |
| `:1064` | `test_r9_content_nouns_are_still_not_scaffolding`（4 参数） | R9：裸 `镜头`/`画面`/`台词`/`慢动作` 仍不是脚手架（没有把内容名词写回全局词表） |
| `:1073` | `test_r9_scene_frame_is_bounded`（2 参数） | R9：`镜头里出现打斗`、`画面里出现一群人打斗` 判「不是脚手架」——框架有定长上限，吞不掉整个小句 |
| `:1082` | `test_r9_scene_nouns_stay_out_of_the_bridge_table` | R9：`镜头/画面/场面/里/中` 仍不在桥接词表里；`动作场面` 这类含场景名词的词条保持可读（`_inside_listed_cue` 守卫） |
| `:1095` | `test_r9_clear_combat_keeps_the_rule`（3 参数） | R9：`没有台词的三镜打斗短片`/`没有武器的两人对打`/`不要温情，没有台词的打斗` 仍判 True 并带出打斗规则 |
| `:1104` | `test_r9_warm_ordinary_stays_without_the_rule` | R9：温情描述仍判 False、不带出打斗规则 |

R5 的 431 行里没有任何一条测试被删除；R6 只**更正**了 `:332` 里的一个期望值（见上）；
**R7、R8 与 R9 一条既有用例都没有改动、没有删除**——R6 的 80 条（含 `_R6_CLAUSE_BOUNDARIES`
里把 `、` 当边界的那组参数化期望）、R7 的 26 条与 R8 的 22 条原样全绿。
R9 只**追加** 31 条（`:956-1110`）：六条复现各两条（决策+分镜合同、单元级已否定），
最长匹配 2 条、词条侧最长匹配 1 条、无介词框架 5 条、内容名词 4 条、有界性 2 条、
词条完整 1 条、明确打斗 3 条、温情 1 条（余数为参数化展开后的计数）。

## Risks and detection

| 风险 | 怎么发现 | 怎么处置 |
| --- | --- | --- |
| R1 门面用模块级导入，monkeypatch 失效、提示词永远带规则 | `tests/test_filmcraft_kb.py:180` 的 `"手艺规则" not in empty_prompt` 失败 | 用函数体内导入；现状已通过 |
| R2 组装外移改变提示词字节 | 现状：R4/R5/R6 **有意**改变 `action_context` 的判定结果 | 认可的行为变化由回归测试显式钉住；其余文案不变 |
| R3 `executor.py` 顶破 3000 行棘轮 | `check_file_sizes.py --fail-on high,medium` 报 `executor.py` | 已解决：恰好 3000 行、finding 消失。**没有余量** |
| R4 门面只是把耦合挪进规则不约束的 foundation→production 方向 | 现有门禁看不到 | 记为已知缺口；要堵需新增 foundation→production 规则（独立立项） |
| R5 画布三处直连 production 继续存在且不被报告 | canvas→production 无规则 | 记入差集台账，本次不改这三个文件（工作单明确禁止） |
| R6 `canvas_reads.py` 3056 行使文件大小门禁长期红 | `check_file_sizes.py` finding 列表 | 单独立项收掉；**不得**登记进 baseline 掩盖 |
| R7 用 `from novelvideo.production import filmcraft_kb` 绕开豁免 | `check_import_boundaries.py` 取 `node.module` | 本次不涉及；将来加豁免时须一并考虑 |
| R9（R4 修复的对象）普通走位词注入打斗规则 | 修复前：温情场景提示词出现 `craft.action_fragment_shots.v1`；现由 `:186`、`:315` 两条测试钉住不带 | 词表只留短语；分镜侧决策经门面 |
| R10（R4 引入、R5 修掉的孪生风险）收窄词表误伤真打戏 | 修复前实测 `三镜动作片…` / `第三镜是武打动作` / `两人在雨里摔跤` 均为 `False`；现由 `:294` 参数化测试钉住为 `True` | 词表补回 11 条明确打戏措辞；**加词只改 `filmcraft_kb` 一处** |
| R11（第二版记为未解决的漂移风险）两份 `_ACTION_CONTEXT_CUES` 拷贝 | **已消除**：`rg -n "_ACTION_CONTEXT_CUES" src/` 只有 `production/filmcraft_kb.py:33` 一处定义 | 决策经 `production_contracts.has_filmcraft_action_context` 暴露；`:350` 行为级守卫测试证明分镜侧真的在问门面 |
| ~~R12 否定窗口是「词前 8 字符」的扁平查找~~ **R6 已修** | 修复前实测：`全片不得出现打斗` / `全片严禁打斗` / `不可有打斗` / `不准打斗` / `不许打斗` / `不出现打斗` 全判 `True`，`不要温情，要打斗` 判 `False` | 改成小句级作用域（`_CLAUSE_BOUNDARIES` + `_negated_action_cue`）。12 个边界字符与工作单要求的 18 个否定短语（表里共 24 条，含 R5 遗留的 `不做/不加入/不带/不需要/without/no `）分别由 `:554`、`:564` 参数化钉住；四类评审输入由 `:526`、`:540` 钉住。**R6 记下的「遗留语义变化」（`不要温情但要打斗` 无标点 → `False`）在 R7 已被修掉**：该输入现在判 `True`（实跑），因为否定与词条之间夹着肯定标记 `要`，R7 的附着判定会让否定失效——这不是放宽，而是 R7 的目标语义 |
| R13 非 bool 的显式值（客户端 JSON 字符串） | `tests/test_filmcraft_kb.py:261` 参数化 9 个非 bool 值，全部断言不开 | 只认真 bool；字符串 `"true"` 同样不开（宁保守不误开） |
| R14（R6 新记录）否定表里新增单字 `莫`、`勿` 可能撞上普通词（莫名/莫大/勿忘） | 无自动发现：本仓 `src/`、`tests/` 里 `grep 莫` 无命中，`勿` 只出现在否定语境 | 记为已知缺口；`莫` 是工作单要求识别的短语，暂不加普通词例外（要加应仿 `_NON_NEGATING_PREFIXES` 的形状，属独立改动） |
| R15（R6 新记录）小句切分不含 `：`、`——`、`…` | 无自动发现；这些标点前后语义上常属同一小句，未纳入边界是保守选择 | 若实测出现跨 `：` 泄漏，再按同一形状扩 `_CLAUSE_BOUNDARIES`，并由参数化测试钉住 |
| R16（R6 修掉的一致性问题）门面决策扫全文、注入截断到 12,000 字 | 修复前：12,000 字之后的词条只能让其中一条路径变化 | 两处共用 `normalize_action_context_text`；`:644` 用 16,000 字文本分别钉住「之内可见／之后不可见」 |
| R17（R7 新记录）否定到词条之间的**功能词白名单**是枚举的，可能漏词 | 无自动发现：白名单外的功能词会让否定「不附着」，行为是**保住**词条（召回），不是误报禁止 | 已知缺口；补词只改 `_NEGATION_BRIDGE_TOKENS` 一处。注意这是**单向**风险：漏词 = 少压一次，不会多压 |
| R18（R7 新记录）「最新否定」判定对同小句内的多个否定只认最近一个 | 无自动发现；`不要温情，没有台词的打斗`（跨小句）与 `没有台词的打斗` 都有 R7 用例钉住，构造上更复杂的多重否定未覆盖 | 记为已知缺口；如需更细的辖域判定，需要真正的句法分析（见 R7 评价矩阵 C，已否决） |
| R19（R7 新记录）`、` 不再是边界后，跨 `、` 的否定可能压掉非列举用法 | `不要打斗、追逐` 是列举（正确压掉），但 `、` 也用于书名/并列专名等场景 | 只有当否定与词条同处一段且中间只有功能词时才会压；R7 用例覆盖列举读法，其它读法未见实测输入 |
| R20（R8 新记录并已修）内容名词与方位字曾在全局桥接词表里 | 修复前实测：`不要中景打斗` / `不要特写打斗` / `不要在画面里打斗` / `不要在画面里出现打斗` 全判 `True` | `画面/镜头/场面/里/中` 移出词表，改为「有界介词框架 + 词条附属语」的结构判据；`:845` 七条参数化 + `:926` 词表卫生用例钉住 |
| R21（R8 新记录）介词框架的界是「`在` + ≤4 字 + 可选方位后缀」，超出这个长度就不再放行 | 无自动发现；`不要在离镜头很远的巷口打斗` 这类长状语会退化成「不附着」→ 判 `True`（保住召回，不是误报禁止） | 已知缺口；放宽只改 `_NEGATION_FRAME_MAX_ADJUNCT` 一处。这是**单向**风险：太短 = 少压一次，不会多压 |
| R22（R8 新记录）附属语白名单外的描述语仍按实词处理 | 实测：`不要血腥打斗`、`禁止暴力的打斗` 判 `True`（按「否定修饰那个实词」读），而 `不要中景打斗` 判 `False`；两者的区别只是白名单里有没有这个词 | 有意选择：宁可保住词条（召回优先），也不把没分类的描述语当脚手架。若要收紧，应扩 `_NEGATION_CUE_ADJUNCT_TOKENS` 并按 `:845` 的形状补用例 |
| R23（R8 新记录）列举的最后一项不再重新审查实词 | 实测：`不要打斗、温情片里的追逐`、`不要打斗、慢动作的肉搏` 判 `False`（被第一条禁止一并压掉），而单独看第二项本可按 R7 读成肯定；带**更靠后的否定**的 `不要打斗、没有台词的追逐` 仍是 `True`（`_latest_negation` 换成 `没有`） | 记为已知缺口；重开只能靠肯定标记或更靠后的否定，这与 R7 记录的 R18 同源 |
| R24（R9 新记录并已修）`_strip_tokens` 按表内书写顺序替换，短词先吃掉长词的前半截 | 修复前实测：`不要大特写打斗` / `不要中近景打斗`（残留 `大`/`中`）、`不要打斗、武打动作、三人群殴`（残留 `动作`）全判 `True` | 改为长度降序匹配（等长保持表内顺序）；`:989` 六条参数化 + `:1026`/`:1039` 两个最长匹配单元用例钉住 |
| R25（R9 新记录）无介词场景框架只在「场景名词 + 可选方位后缀 + 可选 `出现/有`、封顶 5 字」内成立，且裸 `场面` 被当成框架变体 | 无自动发现；框架外的写法仍退回 R8 读法，行为是**保住词条**（召回优先）。反向面：`不要场面、打斗` 这类把 `场面` 当宾语的写法也会被读成禁止（`场面` 被剥掉）——这是本版**唯一**放宽点 | 有意选择：`场面` 单独出现时是调度词而非宾语。若要收紧，只需把 `_NEGATION_SCENE_BARE_NOUNS`（`:202`）清空，并按 `:1055` 的形状补用例；界要放宽只改 `_NEGATION_SCENE_FRAME_MAX`（`:204`）一处 |
| R26（R9 新记录）场景名词也是真实词条的一部分（`动作场面`），框架剥离可能与词条识别相互踩 | 有自动钉法：`:1082` 断言 `_only_bridge_tokens_or_cues("动作场面") is True`、`_strip_negation_scaffolding("动作场面") == "动作场面"` | 框架命中前先过 `_inside_listed_cue`（`:673`）：只有当这段字不是某个更长词条的一部分时才剥。新增词条若含 `镜头/画面/场面`，该守卫自动生效，无需再改白名单 |

## Assumptions not verified

- **「G-01」编号在本仓查不到对应任务条目**——按工作单用法当作本次工作树变更的名字。
  核实命令：`rg -n "G-01" docs/ai/WORK_QUEUE.md docs/ai/specs/`。
- **R4/R5 之前的复现实测由工作单提供（lead 实跑）**；R6 的六个问题是本会话在 R5 后的工作树上
  逐条重跑的（见「R6 主题」的复现读数），没有回退到更早的提交再跑。
- **`canvas_reads.py` 顶破棘轮是既有红门禁而非 CI 上已被容忍**——本机实测支持该推断，
  但没有读取 GitHub Actions 历史 run。核实命令：`gh run list --workflow=quality-fast.yml --limit 5`。
- **`services/` 归入 foundation 是维护者的有意设计**——依据是 `domain_boundaries.json:55` 与既有门面，
  未验证。
- **`filmcraft_kb` 在可预见期内保持纯函数/无 I/O**——它没有 owner 登记，没有机制保证；
  初版设想的 T3 登记未落地，该假设仍然裸露。
- **完整 pytest 未重跑**：R7 只跑了 focused 批次（206 + 105 + 219 passed）、单文件 106 passed，
  以及一次 `-k "prompt or filmcraft or storyboard"` 的 **536 passed / 1 failed / 3 skipped /
  4942 deselected**。那 1 条失败（`test_scene_reference_prompt.py::
  test_scene_reference_newapi_uses_normalized_gateway_base_url`）与本变更无关，R7 实跑到的
  失败原因是 `RuntimeError: 场景参考图未配置图片模型；请先在模型中心添加并检测生图模型。`
  （`src/novelvideo/generators/scene_reference_images.py:690`），且该测试模块对
  `filmcraft` / `storyboard_prompt` 的引用数为 0（`grep -c` 实测 0）。它是环境缺配置，
  不是本次代码造成的失败，但**本次也没有在干净树上做对照重跑**。
  lead 记录的全量基线是 5311 passed / 24 failed / 45 skipped / 2 deselected（既有失败，未逐一核对）。
- **R4/R6/R7/R8 的门禁基线没有回退对照跑**：本机工作树含 R4/R5 未提交改动且 `core.autocrlf=input`，
  stash 会把 CRLF 改写成 LF 产生假 diff，因此**没有**做「干净树重跑一次对照」，
  改用「该测试不导入被改模块 + 失败原因是环境缺配置」两条证据判断它与本变更无关。
- **真实模型生成未跑**：R8 只验证了提示词组装与规则选择，没有触发任何模型中出片流程，
  也没有付费生成。规则在真实模型上的效果不在本次证据范围内。
- **R8 的脚手架分类在真实中文语料上的误判率未测**：只有 R6 的 52 条、R7 的 26 条与 R8 的 22 条
  构造用例，没有语料级评测；`_NEGATION_BRIDGE_TOKENS`、`_NEGATION_CUE_ADJUNCT_TOKENS` 与
  介词框架的界（`在` + ≤4 字）都没有做过语料统计。想补的话需要抽一批真实请求文本，
  逐条人工标注「这段是禁止还是肯定」，再算误判率——本轮没做。
- **R8 没有跑全量 pytest**：跑了工作单点名的两批（228 + 105 = 333 条，其中 228 那批含
  `test_workflow_runtime.py` 与 `test_architecture_boundaries.py`），外加一条更宽的
  `-k "prompt or filmcraft or storyboard"` 批次，读数是
  **1 failed / 558 passed / 3 skipped / 4942 deselected**。那 1 条失败仍是 R6/R7 记过的
  环境项 `test_scene_reference_prompt.py::test_scene_reference_newapi_uses_normalized_gateway_base_url`
  （`RuntimeError: 场景参考图未配置图片模型；请先在模型中心添加并检测生图模型。`，
  `src/novelvideo/generators/scene_reference_images.py:690`），该测试模块对
  `filmcraft` / `storyboard_prompt` 的引用数为 0（`grep -c` 实测 0），且本轮没有在干净树上做对照重跑。
  lead 记录的全量基线是 5311 passed / 24 failed / 45 skipped / 2 deselected。
  要跑全量：`.venv/Scripts/python.exe -m pytest tests/ -q`。
- **R8 没有新增 `architecture_manifest.json` 条目，也没有 T4 守卫测试**：`grep filmcraft`
  在该清单里仍无命中；`tests/test_architecture_boundaries.py` 未被触碰（`check_import_boundaries`
  与它都仍是通过状态，但这不等于「有登记」）。
- **`canvas_reads.py` 的文件大小红门禁仍在**（3056 行 > 3000 棘轮），R7/R8/R9 都未处理、
  也不在工作单范围内：`check_file_sizes --fail-on high,medium` 本轮仍是 exit 1，
  唯一 finding 就是它（`filmcraft_kb.py` R9 后 1137 行、`tests/test_filmcraft_kb.py` 1110 行，都没上榜）。
- **真实模型生成未跑（R9 同 R8）**：只验证提示词组装与规则选择，没有触发任何模型中出片流程，
  也没有付费生成。规则在真实模型上的效果不在本次证据范围内。想补的话需要一次真实分镜生成，
  并在生成结果里核对 `craft.action_fragment_shots.v1` 的实际效果——本轮没做。
- **R9 没有跑全量 pytest**：只跑了工作单点名的两批（259 + 105 = 364 条，其中 259 那批含
  `test_workflow_runtime.py` 与 `test_architecture_boundaries.py`）。要跑全量：
  `.venv/Scripts/python.exe -m pytest tests/ -q`（R8 记的全量同 `-k` 批次读数是
  1 failed / 558 passed / 3 skipped / 4942 deselected，那 1 条失败是环境缺配置的
  `test_scene_reference_prompt.py::test_scene_reference_newapi_uses_normalized_gateway_base_url`，
  本轮**没有**再跑这条更宽的批次，也没有在干净树上做对照重跑）。
- **R9 的最长匹配在真实中文语料上的影响面未测**：只有 R6 的 52 条、R7 的 26 条、R8 的 22 条
  与 R9 的 31 条构造用例。`_strip_tokens` 的调用点有三处（脚手架、列举项词条、`_ACTION_CONTEXT_CUES`），
  长度降序改变了「同一次调用里先删谁」的顺序，理论上只影响**重叠**词条
  （`武打`/`武打动作`、`动作戏`/`动作短片` 这类前缀关系）。要做语料级确认，需要抽一批真实请求文本
  逐条比对改动前后的 `_has_filmcraft_action_context` 结果差异——本轮没做。
- **R9 没有新增 `architecture_manifest.json` 条目，也没有 T4 守卫测试**：与 R6/R7/R8 同一缺口，
  `grep filmcraft` 在该清单里仍无命中，`tests/test_architecture_boundaries.py` 未被触碰。
