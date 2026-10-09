---
name: village-canvas-combat-director
description: Use for 打斗、武戏、动作戏、1v1 对决、高燃台词打戏的结构、因果链、战损连续性和镜头预算。把「机器能判的」写成可执行硬约束，把「只能人看的」明确标成人看，不让 agent 以为画面好坏会被自动检查。
version: 1.0.0
---

# Village Infinite Canvas 打斗导演规程

这个 Skill 只解决一件事：**一场打戏要按什么顺序立案、按什么字段声明、哪些判据会被机器拦下来、
哪些必须人眼看。** 它不负责生成媒体，不创建第二套镜头合同，也不把「结构合法」说成「打得好」。

**最重要的一条前提**：本仓的打斗闸门判的是**声明**，不是**像素**。声明补齐只证明「这份片包合法、
前后不自相矛盾」，永远不证明「这场打斗好看」。见 `src/novelvideo/production/combat_film_contract.py:8-14`。

## 一、何时加载

- 请求含打斗、武戏、打戏、格斗、对决、对战、1v1、短兵相接等明确战斗意图；
- 分镜里出现攻防、接触、受击、倒地；
- 需要判断一场打戏该拆几镜、每镜多长；
- 打戏里带台词，需要确认台词说得完；
- 打戏被闸门拦下（`combat_film_structure_ready` / `combat_film_declarations_complete`）。

台词、镜头语言、连续性的一般规则分别走 `village-canvas-story-director`、
`village-canvas-shotcraft`、`village-canvas-continuity`；本 Skill 只管打斗特有的那一层。

## 二、事实边界

- 战斗意图识别是**窄口径**（`combat_film_contract.py:370`）：需要「点名战斗的词」、
  「两个不同战斗动作」或「一个战斗动作＋对手标记」之一。实测「一个关于反击命运的故事」
  和「把这段格挡式教育故事拍成短片」都**不**触发（隐喻后缀会被过滤）。
- 否定判断是**按分句、向前 12 字**（`combat_film_contract.py:217`, `:286`）。实测两头的错：
  旧版只看前 3 字，「这个片子完全没有任何打斗」「不希望出现任何格斗镜头」会被误判成打斗；
  旧版又把「不停 / 不断 / 不得不 / 无不」里的 `不` 当成否定，「两人不停交手」「双方不断对打」
  「两人不得不打斗」这些真打斗会被漏掉。现已按分句边界 + 长词覆盖修正。
- 词表在共享语料之外**补了打斗动词**（`combat_film_contract.py:132`）：`缠斗 / 扭打 / 厮打 /
  互殴 / 挥拳 / 近身格斗`。实测「两人贴身缠斗」「两人扭打在一起」「两人挥拳互殴」在旧版全是
  False。补在合同侧而不是 `combat_prompt_kb`，因为后者是有 sha256 出处的蒸馏语料。
- 非战斗请求完全不受本 Skill 的闸门约束（`applies=False`，不带任何 gate）。
- 不许把声明当成已完成画面。声明是**拍摄意图**，不是验收证据。
- 不伪造 `cinematic.combat` 之外的新字段；声明只能写在既有载体里。

## 三、声明载体（唯一合法位置）

打斗声明写在**每个镜头的 `cinematic.combat`**。这是实测确认的唯一能穿过序列化的载体：

- `StoryboardShot.cinematic` 是自由 `dict`，写在里面的 `combat` 能活过
  `model_validate` 和 `model_dump`；写在镜头**顶层**的新参数会被静默丢掉。
- 战损连续性**不能**用 `continuity_in` / `continuity_out` 判：归一化会把上一镜的
  `continuity_out` 抄进下一镜的 `continuity_in`，拿它比较等于自己跟自己比，永远通过。

字段全集（`combat_film_contract.py:89-95`, `:861`）：

| 字段 | 合法值 | 说明 |
|---|---|---|
| `duel_scope` | `"1v1"` | 一对多必须另立合同，不能靠本闸门放行 |
| `combatant_count` | `2`（整数） | 其余角色必须退出画面 |
| `combatant_ids` | 两个互不相同的稳定资产 ID | 跨镜必须同一对；换人＝换了一场决斗 |
| `causality_chain` | `["contact","compression","failure","no_recovery"]` | 必须**逐字全序**，不能只写其中一段 |
| `damage_state` | `intact` / `minor` / `severe` / `down` | 只能保持或加重，禁止回退 |
| `dialogue_lines` | 字符串数组 | 请求里点了台词时**必须非空**，且不留空槽位 |

同时每个镜头还要有五个电影声明族，缺一律判失败（`combat_film_contract.py:93-99`）：
`lighting` / `color_look` / `screen_direction` / `edit` / `sound`。注意这与非战斗路径相反——
非战斗时「没写声明」等于静默通过，战斗时「没写声明」等于失败。

### 已实测通过的最小声明集

以下结构（6 镜 ×5.0s，30s）已在本仓实测得到
`combat_film_structure_ready=True`、`combat_film_declarations_complete=True`、`issues=[]`：

```json
{
  "lighting":       {"source_direction":"画面左后上方","color_temperature_k":4300,
                     "key_fill_ratio":"4:1","change_policy":"全片不变"},
  "color_look":     {"look_id":"rain-night-cold-v1","dominant":"冷灰","secondary":"暗青",
                     "accent":"灯笼暖橙","ratios":{"dominant":0.6,"secondary":0.3,"accent":0.1}},
  "screen_direction":{"axis_id":"corridor-axis","line_side":"轴线右侧","subject_facing":"面向彼此",
                     "eyeline":"视线相交","crossing_policy":"不越轴"},
  "edit":           {"cut_on":"动作","match_cut":"刀锋","beat_seconds":0.5},
  "sound":          {"ambience":["雨打瓦檐"],"diegetic_sources":["靴底碾砖"],"sfx_cues":["刀锋破风"]},
  "combat":         {"duel_scope":"1v1","combatant_count":2,"combatant_ids":["char-a","char-b"],
                     "causality_chain":["contact","compression","failure","no_recovery"],
                     "damage_state":"intact"}
}
```

战损序列按镜推进，例如 `intact → minor → severe → down → down → down`。

## 四、硬约束（机器会拦）

下表每一条都已在本仓实测或读源码确认。**判「声明」不判「画面」**是共同前提。

| # | 判据 | 阈值 | 判定来源 | 后果 |
|---|---|---|---|---|
| 1 | 每镜时长 | `4.0 ≤ s ≤ 15.0` | `combat_film_contract.py:82-83`（对齐 `direct_video_profiles.py:201` 的 `range(4,16)`） | 阻断该 gate |
| 2 | 镜头数 | `2 ≤ n ≤ 12` | `combat_film_contract.py:86-87` | 阻断 |
| **2b** | **总时长对得上请求点名的秒数** | 容差 **±1.0s** | `combat_film_contract.py:478`；`production_plan.py:38-70` 在 `freezone-*` 链上真拦 | 阻断 `combat_film_structure_ready`；freezone 链抛 `workflow_production_plan_duration_mismatch` |
| 3 | 1v1 + 两名战斗者 + 身份跨镜一致 | 逐项 | `combat_film_contract.py:820-855`, `:888` | 阻断 |
| 4 | 因果链逐字全序 | 四段全 | `combat_film_contract.py:861` | 阻断 |
| 5 | 战损不回退 | 单调 | `combat_film_contract.py:689` | 阻断 |
| 6 | 五族电影声明齐全 | 5/5 | `combat_film_contract.py:735-746` | 阻断 |
| 7 | 主光方向／色温跨镜一致 | 集合大小 1 | `cinematic_contract.py:649`, `:655` | 阻断 |
| 8 | 轴线跨镜不变 | 集合大小 1 | `cinematic_contract.py:686` | 阻断 |
| 9 | 色彩配额和 = 1.0 | 容差 0.03 | `cinematic_contract.py:569` | 阻断 |
| 10 | 动作节拍 | `0.3 ≤ beat ≤ 0.8` 秒 | `cinematic_contract.py:733` | 阻断 |
| 11 | 台词能在镜内说完（付费提交门） | `字数 ÷ 秒 ≤ 6.0` | `freezone/video_request_contract.py:1204-1205`, `:1278` | 抛错，拒绝提交付费任务 |
| 12 | 台词字数 ÷ 镜时长（打斗声明闸门内） | `≤ 4.5` 字/秒 | `dialogue_sound_contract.py:409` | 阻断 `combat_film_declarations_complete` |
| 13 | 台词无元指令混入 | 无镜头/口型/字幕词 | `dialogue_sound_contract.py:391` | 记入同一审计的 issues |
| 14 | 分镜 8 段式 / 运动稿 6 段式 | 段数精确 | `script_contract.py:29-30`, `:566`, `:695` | 阻断脚本合同 |
| 15 | 成片工程 QC | 17 项 | `workflow_runtime/final_film_qc.py:29-47` | **只记录**（见下） |

第 1–13 条的实际生效范围取决于走哪条链，见本节末尾的链对照表。另有三条**新加的空输入/坏输入
判据**，它们不是拦创作，是拦「没证据也算过」：镜头表为空 → `combat.damage.evidence_missing` /
`combat.cinematic.evidence_missing`（`combat_film_contract.py:662`, `:724`）；镜头条目不是对象 →
`combat.structure.shot_unreadable`（`:503`）。实测旧版对空输入返回 `passed=True`，与本模块自己写的
「缺证据不算通过」直接矛盾。

两条重要的**不成立**，别当硬门用：

- **参考图 ≤9 是劝告档不是阻断档**（`script_contract.py:794` 用 `SEVERITY_ADVISORY`），
  超了不会拦。它只在 `script.reference.budget.v1` 记为提示。
- **成片 17 项 QC 在 freezone 链上只写进 `delivery_qc`**，随后产物状态无条件置
  `completed`（`media_dispatch.py:2062`, `:2113`）。它是证据，不是门。

### 第 2b 条为什么非有不可（本轮实测）

这是本轮唯一一条**新加**的硬判据，来自一次真跑：请求「30 秒」的片子，链产出
**2.041 秒**的 MP4，所有闸门全绿。原因是两条链的时长行为不同：

- `one-click-film` 有 `_normalize_plan_duration`（`executor.py:372`），实测 34.02s 被收敛成
  30.0s —— 在这条链上判总时长确实恒真，所以**旧判据「不判总时长」在那边是对的**；
- `freezone-*` 链**从不调用**那个归一化，计划自己记的 `total_duration_seconds` 就是出片时长，
  而此前没有任何一处拿它跟请求比过。

修正的做法是：不判「总时长是否等于某个理想值」，只判「脚本合计是否对得上请求里点名的秒数」。
没点名秒数就**不判**（不是静默通过，也不是白给一个失败）。判据落在两处——
合同侧 `audit_combat_structure`（走一键链时生效），以及 `production_plan` 的
`_assert_requested_duration`（`production_plan.py:38`，走 freezone 链时真拦，在提交付费媒体之前）。
两处共用同一个解析函数 `requested_duration_seconds`（`combat_film_contract.py:268`），
它的正则与 `executor.py:360` 的同名函数逐字一致，且有测试钉住两者不许漂移。

### 台词有两个不同口径，打斗链上按严的那个走

第 11 条和第 12 条不是同一件事，**打斗声明闸门用的是更严的 4.5 字/秒**：

- 第 11 条（6.0 字/秒）是**付费提交门**，按整秒判断，超了抛错拒绝提交；
- 第 12 条（4.5 字/秒）在 `audit_combat_dialogue_declarations` 内部生效：台词被按
  4 字/秒估长，一旦镜头时长短于估长，语速就被重算成 `字数 ÷ 镜时长`，**超过 4.5 即阻断**。

实测边界（22 字纯汉字台词）：**4.9 秒起才通过**，4.5 秒仍是 `dialogue.rate_too_fast`。
换算成通式就是 **镜时长 ≥ 字数 ÷ 4.5**（22 ÷ 4.5 ≈ 4.89）。按 5 秒镜头算，**一句不超过 22 字**——
实测 21 字在 4.7 秒已通过，23 字在 5.0 秒仍报 `rate_too_fast`。所以本仓打斗台词的实际下限是
**镜时长 ≥ 字数 ÷ 4.5**，不是 ÷6.0。

注意计数口径（`dialogue_sound_contract.py:110-113`）：**中文按字、西文按词、标点不计**。
用 `_text_length` 算的是「可念字数」，不是 `len(text)`。实测同一句 22 字的台词，
纯汉字写法要 4.9 秒、带标点与说话人前缀的写法 4.5 秒就过——
**别按 `len(text)` 估，按 `_text_length` 估**，两者实测差 3 个字符。

另有两条实测陷阱：

- **声明了 `dialogue_lines`，该镜的 `cinematic.sound` 就必须有内容**（`ambience` /
  `diegetic_sources` / `sfx_cues` / `music` 至少一层），否则另报
  `sound.layers_missing` 照样阻断。台词与声音声明要一起补。
- 「台词」二字一出现在请求里，`require_dialogue` 即为真，**没有任何镜头声明台词就是失败**
  （`combat.dialogue.missing`），不能靠全程无台词绕开。

### 两条链的生效范围不同（实测，别混用）

两条链的步骤清单实测如下：

- `one-click-film`：`understand / canvas_structure / story_and_shots / asset_slots /
  media_generation / quality_review / delivery`
- `freezone-final-film`：`understand / script_contract / production_plan /
  storyboard_images / shot_videos / final_film`

| 判据 | `one-click-film` 链 | `freezone-*` 链 |
|---|---|---|
| 打斗结构＋声明闸门（第 1–10 条） | ✅ 在 `quality_review` 阻断 | ❌ **该链没有 `quality_review` 步骤，完全不判** |
| **总时长对得上请求（第 2b 条）** | ✅ 合同侧判 | ✅ **`production_plan.py:38` 抛 `workflow_production_plan_duration_mismatch`，提交付费媒体之前** |
| 4.5 字/秒与元指令、声音层（第 12–13 条） | ✅ 随打斗闸门一起拦 | ⚠️ 审计会跑（`services/film_production.py:70`），但结果只塞进 `film_production` 载荷，**不阻断** |
| 台词付费提交门（第 11 条，6.0 字/秒） | ✅ 提交前抛错 | ✅ 提交前抛错 |
| 分镜 8 段式／运动稿 6 段式（第 14 条） | ❌ 该链不走 `script_contract`，**不适用** | ✅ 阻断脚本合同（`freezone_script.py:283-295`） |
| 成片工程 QC（第 15 条） | 只记录 | 只记录 |

关键含义：**第 14 条只对 `freezone-*` 链有意义**；反过来，**打斗闸门只对 `one-click-film` 链有意义**。
一条请求走哪条链，决定了哪些判据真的被执行——不要把另一条链的判据当成自己这条的保护网。

`one-click-film` 的 `quality_review` 会求值并抛 `workflow_quality_gates_failed`
（`executor.py:1768` 合并 `required_gates`、`:1803` 求值、`:1823` 抛错）。

### 当前必须注意的实操陷阱

**没有任何上游会替你写 `cinematic.combat`。** 分镜编译提示词（`executor.py:678-700`）列了
`lighting / color_look / screen_direction / edit / sound`，**没有 `combat` 键**；全仓搜索
`duel_scope` / `causality_chain` / `damage_state` 也只在合同与测试里出现。
而合同是**失败关闭**的：实测把一个真实的「30 秒极限打斗，带高燃台词」请求
（去掉 `combat` 块）跑一遍质量验收，`draft` 与 `production` 两种模式都被拦：

```
workflow_quality_gates_failed  blocking=['combat_film_declarations_complete']
```

补上声明后同一请求 `blocking=[]` 通过。

**所以：打斗请求一立案，就先把 `cinematic.combat` 逐镜写全，再谈别的。**
另外该请求含「台词」二字，会触发 `combat.dialogue.missing`——必须有镜头声明
非空 `dialogue_lines`，否则同样被拦。

**别把「闸门绿了」误读成「片包已经能开工」。** 本轮实测把一个 6 镜的声明片包跑过合同，
返回 `required_gates=[两个 gate]`、观测值都 `true`、`ready_for_media=True`、
`issues=[]`——**但没有任何一帧像素产生**，`ready_for_delivery` 是 `None`。
「声明齐全」是开工许可，不是交付证据；两者的距离就是第五节那十条人看项。

**也别把「跑出一个 MP4」当成「生成链路通了」。** 本轮拿到的两个真实可播放 MP4
（2.041s / 30.041s）里，每一帧画面都来自桩造的纯色块：图像与视频生成三步都是
monkeypatch 换掉的，成片是「容器真、内容假」。链路证明的只是管道接得通、ffmpeg 编得动。
判断一个 MP4 是不是真产物，看它每一帧的来路，不要看它能不能播。

为什么它连草稿都拦：合同把「缺证据」写成 `False` 而不是 `None`
（`combat_film_contract.py:933` 起），而质量闸门只对 `None` 宽容——
`blocking = failed + (not_run if strict else [])`。实测：`False` → 阻断，
`None` → 不阻断。所以打斗闸门不依赖 strict 模式，草稿运行同样会红。

### 30 秒到底能装几镜（算术，别猜）

`combat_structure_capacity(30)` 实测返回 `max_shots = 7`、`min_shots = 2`。
因为 `30 ÷ 4 = 7.5`，**8 镜必然把某镜压到 4 秒以下**，那 8 个镜头会全部报
`combat.structure.shot_too_short`。要更多镜只能抬总时长或另立合同。

**上限被夹到镜头数上限 12**（`combat_film_contract.py:420`）。旧版实测 180 秒会返回
`max_shots = 45`——一个结构闸门自己会拒绝的窗口，等于叫你去建 45 镜再判你不过。
现在 `max_shots ≤ 12`，60 秒实测返回 `min_shots = 4`、`max_shots = 12`（不再是 15）。
12 镜 × 15 秒 = 180 秒是天花板；**181 秒起 `feasible = False`**，
`reason = combat.structure.target_unreachable`（实测）。

**注意**：一键链上的时长归一（`executor.py:372-403`）把每镜夹在 **1.0–20.0** 秒，
**不是 4–15**。实测「3 镜 / 目标 60 秒」会被归一成 `[20.0, 20.0, 20.0]`，
三镜全部越出模型能力窗（`duration=tuple(range(4,16))`）。
所以「总时长对了」不等于「每镜合法」——每镜时长必须自己按第四节第 1 条核。

**两条链的「总时长」行为不同，别背错**（第四节第 2b 条）：

| | `one-click-film` | `freezone-*` |
|---|---|---|
| 是否把计划收敛到请求时长 | ✅ `_normalize_plan_duration`，实测 34.02s → 30.0s | ❌ 从不调用 |
| 总时长判据在哪生效 | `audit_combat_structure`（合同侧） | `production_plan` 的 `_assert_requested_duration`，提交付费媒体前抛错 |
| 请求没点名秒数时 | 不判 | 不判 |

## 五、只能人看的（机器判不了，必须看画面）

**这一节没有任何自动化。** 闸门绿了也不代表下面任何一条成立。铁律 7 条里有 6 条在此。

| 看什么 | 具体判据 | 现状 |
|---|---|---|
| 物理因果链是否真拍出来 | 接触→身体压缩→功能失效→恢复失败，受击者要踉跄、挣扎、试图稳住重心，禁止「碰一下就僵直」 | 只判声明的链是否写全，不判画面 |
| 目标归因 | 被击倒者倒地、其余人保持站立，不共享伤害 | 只判 `combatant_ids` 是否为两名且跨镜一致 |
| 战损连续性 | 前一秒破裂后一秒绝不复原 | 只判 `damage_state` 不回退 |
| 双人同框 | 击中特写必须同时出现攻击者攻击肢体＋目标全身/面部 | 无任何实现 |
| 5 秒绝杀 | 5 秒 1v1 禁止一对多、禁止多个来回 | 无；本仓口径是每镜 4–15s |
| 导演节拍器 | 原速底片、后期提速、抽帧定格只定敌人 | 无；且属后期，不在生成链 |
| 声音叙事 | 无 BGM、纯物理音效、情绪断点真空死寂 | 只判 `sound` 五层是否声明 |
| 对手戏 | 刺激 → 微延迟 → 反应 | 无；`village-canvas-expression-director` 有设计方法，无检测 |
| 物理细节推理 | 发力方式／接触点／受力反作用／重心转移／环境反馈／残留效果 | 无；须逐动作人工推理后写进提示词 |
| 打得好不好看 | 招式可信、力量感、节奏 | 无 |

**本轮实测补充：还有三条机器判不了，但很容易被误当成「已经通过」。**

- **镜数与镜内是否真的换了镜**：成片里既没有转场标识也没有字幕，镜数只能靠时间线 JSON 推。
  实测 30 秒 6 镜探针里 6 个「镜头」颜色一模一样，**成片上看不出有 6 个镜**——
  结构闸门却是绿的。结构合法 ≠ 观众看得出是 6 个镜。
- **画面里有没有运动**：实测同一段 48 帧里 45 帧哈希完全相同，本质是一张静图。
  「每镜 4–15 秒」这条判据对此毫无感知——它只数秒数，不看那一秒里有没有发生事。
- **台词有没有真的被念出来**：7 步链没有台词载体，`dialogue_lines` 只是声明。
  实测成片音轨是 -91 dB 数字静音（`jobs.py:917-930` 在无音轨时补 `anullsrc`），
  而声明闸门可以是绿的。**声明通过 ≠ 有人说了那句话。**

人工复核的最小做法：把 4–15 秒的生成片段逐镜看一遍，按上表逐行打勾；
任何一行没看过就写「未复核」，不要写成通过。

## 六、提示词编译

按外部手艺的 H3 分支改写成本仓字段，顺序固定：

```text
镜号 + 电影声明族（lighting/color_look/screen_direction/edit/sound）
  -> t=0 起始状态（承接上一镜结束姿态，不提前演结果）
  -> 按镜时长的 2-5 个因果动作段（每段一个主动作 + 一个主运镜）
  -> 接触点 / 发力方式 / 重心转移 / 环境反馈
  -> 结束状态 + 精简负面约束
```

硬规则：

- 一个生成片段只承担一个叙事目的；**单段只选一个主运镜**，禁止「推近+拉远+环绕」并列；
- 动作要写成可拍摄的物理链，不用「快速连击」「强大能量」替代具体动作；
- 效果（尘土、碎片、火花、声）必须由接触或环境运动触发，不做脱离主体的装饰层；
- 角色外观由参考图锁定，提示词只写动作、位置、气色——**人物身上不写 hex 色值**，
  AI 会把它当材质指令；
- 负面约束只写该镜的高风险漂移，不堆否定词、不整包复制；
- 台词写进独立字段（`dialogue_lines` / 行内台词列），**不写进 `prompt`**——
  一键链的编译提示词明确禁止把对白原文写进 `prompt`。

## 七、返修矩阵

| 症状 | 首选修复 | 禁止做法 |
|---|---|---|
| `shot_count_below_min` | 把因果链拆成至少两次可见接触 | 把整场对打塞进一镜 |
| `shot_too_short` | 并入相邻因果段，或抬总时长 | 硬压时长让镜头数好看 |
| `total_duration_mismatch` | 按请求点名的秒数重分每镜时长 | 把请求里的秒数改掉去迁就脚本 |
| `workflow_production_plan_duration_mismatch` | 同上；这是 freezone 链提交付费媒体前的最后一道 | 绕过计划直接提交媒体 |
| `shot_unreadable` | 把镜头写成对象 | 让坏条目静默消失后照常通过 |
| `damage.evidence_missing` / `cinematic.evidence_missing` | 先给出镜头表 | 把空镜头表当通过 |
| `causality_chain_invalid` | 逐字补齐 `contact→compression→failure→no_recovery` | 只写命中的那一段 |
| `damage_state.regressed` | 战损保持或加重 | 用 `continuity_out` 绕开（会自比自，假通过） |
| `combatant_identity_drift` | 锁定双方资产 ID | 中途换人 |
| `family_missing` | 补齐五族声明 | 以为「没声明＝不适用」——战斗时是失败 |
| `dialogue.missing` | 在需要说话的镜头声明 `dialogue_lines` | 把台词塞进 `prompt` 让模型自己念 |
| `dialogue.rate_too_fast` | 让镜时长 ≥ 字数 ÷ 4.5（5 秒镜 ≤22 字，按去标点字数） | 只按 6 字/秒核，会在声明闸门被拦 |
| `sound.layers_missing` | 给有台词的镜补 `sound` 至少一层 | 只补 `dialogue_lines` 不补声音 |
| 战斗意图被漏判 | 检查是不是写成了隐喻（「格挡式教育」）或落进否定短语 | 为绕过判据而堆打斗关键词 |
| 镜头时长越出 4-15s | 按第四节算术重新分配 | 相信「总时长对了就行」 |
| 打戏不好看 | 回到第五节逐条人看 | 加更多形容词和特效 |

## 八、验收清单

- [ ] 战斗意图判定为真（不是「反击命运」式隐喻，也不是被「没有打斗」这类否定句挡住）。
- [ ] 每镜 `cinematic.combat` 六字段齐全，五族声明齐全。
- [ ] 逐镜时长落在 4–15 秒，镜数在 2–12，且与目标时长算术相容。
- [ ] **脚本合计时长与请求点名的秒数相差 ≤1.0s**；请求没点名秒数则此项不适用。
- [ ] 战损单调不回退；战斗者 ID 跨镜一致。
- [ ] 点名的台词有非空 `dialogue_lines`，镜时长 ≥ 字数 ÷ 4.5（付费提交门另按 ÷6.0）。
- [ ] 台词没有混进镜头/口型/字幕类元指令。
- [ ] 分镜 8 段式、运动稿 6 段式段数精确。
- [ ] 明确区分：以上是**声明**通过，不是画面通过。
- [ ] 第五节逐条人看并标注「已复核 / 未复核」，未看的写未看。
- [ ] 没有伪造 `cinematic.combat` 之外的新字段或工具回执。

## 来源与边界

本 Skill 是对外部导演手艺 §6.3 动作戏铁律 7 条、§6.4 物理细节推理清单、
§6.5 对手戏铁律、§11.1 视频提示词编译、§11.5 镜头合同、§11.6 提示词总监合同的
**去模板化蒸馏**：只保留能落到本仓既有字段与既有判据上的部分，经验数字、平台语法和
固定模板一律不继承。

配套的本仓能力（不重复造）：

- `src/novelvideo/production/combat_film_contract.py` —— 打斗结构与声明合同；
- `src/novelvideo/production/combat_prompt_kb.py` —— 条件触发的打斗提示词规则（8 条，仅在
  明确战斗意图时注入，被 `freezone/prompt_optimizer.py` 引用）；
- `docs/knowledge/combat_prompt_distilled.md` —— 武戏知识卡，`combat_prompt_kb` 的语料来源。

本 Skill 不复制外部参考库，不引入外部运行链，不改动外部技能目录。
