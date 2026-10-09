"""Small deterministic Filmcraft rule set used by the prompt compiler.

The checked-in references remain the source material.  These rules are the
runtime execution layer: each rule states when it applies, what to do, and
what to avoid.  Keeping them typed prevents a retrieved paragraph from being
mistaken for an executable instruction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


FILMCRAFT_KB_SCHEMA = "filmcraft_kb.v1"


# Cues that unambiguously describe staged combat/action.
#
# The first version of the ``action_context`` trigger also matched bare
# single-character verbs (推/拉/走/跑/摔) and the two-character word 动作, so
# ordinary blocking such as "推开门，走到窗边" pulled the combat-only rule
# ``craft.action_fragment_shots.v1`` into a warm-scene storyboard prompt.
# Keep this list phrase level: bare 动作 is not a cue, but a phrase that *names*
# an action film or a fight (动作片/武打/对决/摔跤...) is.  R6 added the eight
# clear combat phrases an independent review found missing
# (摔打/肉搏/对打/打起来/开打/群殴/决战/交战); they are still phrases, not the
# bare verbs 推/拉/走/跑/摔 the R4 fix removed.  This tuple is the one
# authoritative copy; the storyboard compiler
# (``workflow_runtime/storyboard_prompt.py``) asks for the decision through
# ``services.production_contracts.has_filmcraft_action_context`` instead of
# keeping a second list of its own.
_ACTION_CONTEXT_CUES: tuple[str, ...] = (
    "打斗",
    "打戏",
    "格斗",
    "战斗",
    "搏斗",
    "厮打",
    "厮杀",
    "混战",
    "交手",
    "对决",
    "扭打",
    "摔跤",
    "摔打",
    "肉搏",
    "对打",
    "打起来",
    "开打",
    "群殴",
    "决战",
    "交战",
    "追逐",
    "奔跑",
    "格挡",
    "枪战",
    "爆炸",
    "挥拳",
    "出拳",
    "踢腿",
    "冲撞",
    "袭击",
    "武打",
    "武打动作",
    "动作戏",
    "动作场面",
    "动作片",
    "动作短片",
    "动作剧",
)

# Negation convention adapted from ``agent_tools/village_canvas/workflow_dispatch``
# (``_has_affirmative_marker``): a cue is ignored when a negation sits in front of
# it.  R6 changed the scope from "the 8 characters directly in front of the cue"
# to "the cue's own clause": the flat window both missed real negations
# (「全片不得出现打斗」 kept its cue) and let a negation cross a comma
# (「不要温情，要打斗」 lost its affirmative second clause).  A negation now
# controls a cue only when both sit between the same two clause boundaries.
#
# R7 added the second half of that rule: being in the same clause is *not*
# enough, the negation also has to be attached to the cue.  Clause scope alone
# made every modifier phrase that happens to start with a negation word
# ("没有台词的三镜打斗", "避免煽情的三镜打斗") switch a clear combat request off.
# ``、`` is therefore no longer a boundary: it separates enumerated items of one
# prohibition ("不要打斗、追逐"), so the prohibition has to be carried across it
# instead of being cut in half.
#
# R8 kept that attachment rule but fixed how the gap is classified.  R7 decided
# it with one flat whitelist of "bridge" words and put content nouns in it, so a
# shot size ("不要中景打斗") and a real prohibition ("不要在画面里出现打斗") both
# leaked back into a hit.  R8 splits the gap into three checks that say *why* the
# text is allowed there — function words, a bounded prepositional frame, or a cue
# adjunct (景别词/数量词) — and enumerations are now judged by whether their
# earlier items are listed cues ("不要打斗、近身肉搏" is one prohibition) rather
# than by the wording of the last item.
_CLAUSE_BOUNDARIES: tuple[str, ...] = (
    "，",
    "。",
    "；",
    "！",
    "？",
    ",",
    ".",
    ";",
    "!",
    "?",
    "\n",
)
# Enumeration separators are punctuation that lists items inside one clause.
_ENUMERATION_SEPARATORS: tuple[str, ...] = ("、",)

# Function/bridge tokens that may sit between a negation and the cue it really
# governs in a direct prohibition ("全片不得``出现``打斗", "不可``有``打斗",
# "不要打斗，``也``不要追逐").  The list stays deliberately small: only tokens
# that carry no content of their own belong here.  Anything else between the
# negation and the cue means the negation was describing that other word, not
# the cue ("没有``台词``的打斗" is a fight without dialogue, not a ban on
# fights).
#
# R8 removed the content nouns (``画面``/``镜头``/``场面``) and the locative
# characters (``里``/``中``) that R7 had parked here.  Stripping them
# *everywhere* is what broke real prohibitions: "中景" lost its 中 and left a
# stray 景 behind, while "在画面里" lost 画面/里 and looked like scaffolding even
# when the negation was really about 画面.  Where such a word may still sit
# between the negation and the cue is now decided structurally by
# ``_only_bridge_tokens`` (prepositional frame, cue adjunct), never by a global
# replacement.
_NEGATION_BRIDGE_TOKENS: tuple[str, ...] = (
    "全片",
    "全剧",
    "整片",
    "整部",
    "出现",
    "有",
    "也",
    "再",
    "任何",
    "一个",
    "一切",
    "进行",
    "加入",
    "包含",
    "允许",
    "安排",
    "拍摄",
    "和",
    "与",
    "及",
    "或",
    "以及",
    "都",
    "还",
    "又",
    "的",
    "地",
)
# R8 direct-prohibition frames.  A preposition can only introduce an adjunct —
# where or in what the cue happens — so the noun inside the frame never becomes
# the object of the prohibition: 「不要``在画面里``打斗」 bans the fight, not the
# frame.  The frame is bounded (在 + at most four characters + an optional
# locative suffix) and stops at the enumeration separator, so the scanner can
# never swallow a whole clause.
_NEGATION_FRAME_PREPOSITIONS: tuple[str, ...] = ("在",)
_NEGATION_FRAME_SUFFIXES: tuple[str, ...] = ("里", "中", "内", "上", "下", "外", "间")
_NEGATION_FRAME_MAX_ADJUNCT = 4
# R8 cue adjuncts.  These words describe *who* and *how* the cue is staged, so
# they belong to the cue's own phrase instead of being a second object of the
# negation: 「不要``中景``打斗」 and 「不要出现打斗、爆炸、``两人``群殴」 prohibit the
# fight, not a shot size or a headcount.  Anything else in that gap still
# belongs to the other word (R7: 「没有``台词``的打斗」 bans dialogue, not the
# fight).
_NEGATION_CUE_ADJUNCT_TOKENS: tuple[str, ...] = (
    "中景",
    "特写",
    "近景",
    "远景",
    "全景",
    "中近景",
    "大特写",
    "两人",
    "三人",
    "多人",
    "几人",
    "数人",
    "众人",
    "一群人",
    "一个人",
    "两个人",
    "三个人",
)
# R9 scene frames.  R8 only recognised an adjunct frame behind a preposition, so
# the same adjunct written without one ("不要``镜头里``出现打斗") left an
# unclassified noun in the gap and a pure prohibition leaked back into a hit.
# A scene frame is a short scene noun plus one optional locative suffix plus one
# optional bridge word — at most ``_NEGATION_SCENE_FRAME_MAX`` characters — so it
# stays bounded exactly like the prepositional frame and can never swallow a
# clause or an arbitrary noun phrase.  ``画面``/``镜头`` on their own stay content
# nouns (R8: "不要``画面``" is about the picture, not about the cue); ``场面``
# alone is the staging word and closes the frame ("不要``场面``打斗").
_NEGATION_SCENE_NOUNS: tuple[str, ...] = ("镜头", "画面", "场面")
_NEGATION_SCENE_BARE_NOUNS: tuple[str, ...] = ("场面",)
_NEGATION_SCENE_BRIDGE_WORDS: tuple[str, ...] = ("出现", "有")
_NEGATION_SCENE_FRAME_MAX = 5
# Affirmative markers that re-open a negated enumeration or clause
# ("不要打斗，要看追逐", "不要温情，而是追逐").  They only count in the text
# *after* the negation, which is why they are checked on the intervening text.
_AFFIRMATIVE_CONTEXT_MARKERS: tuple[str, ...] = (
    "要",
    "而是",
    "需要",
    "改成",
)
_ACTION_CONTEXT_NEGATIONS: tuple[str, ...] = (
    "不要",
    "不用",
    "不做",
    "不加入",
    "不带",
    "不含",
    "不需要",
    "无需",
    "没有",
    "禁止",
    "避免",
    "勿",
    "别",
    "不得",
    "严禁",
    "不可",
    "不准",
    "不许",
    "不出现",
    "不再",
    "切勿",
    "莫",
    "without",
    "no ",
)
# ``别`` is also the second character of ordinary words (特别/告别/分别/个别/辨别/
# 区别/识别/性别/道别/级别/差别/离别).  Without this exception "特别激烈的打斗"
# would lose a clear fight cue, which is exactly the recall the phrase list is
# supposed to keep.
_NON_NEGATING_PREFIXES: tuple[str, ...] = (
    "特",
    "告",
    "分",
    "个",
    "辨",
    "区",
    "识",
    "性",
    "道",
    "级",
    "差",
    "离",
)


RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "craft.single_primary_action.v1",
        "stage": "shot_prompt",
        "trigger": "always",
        "instruction": "每镜明确当前叙事重点，按起始状态、动作因果与结束状态组织。可包含相互关联的多阶段动作、多人互动或有意义的静止；按故事需要和模型能力决定是否拆镜。",
        "avoid": "无因果的动作堆叠、遗漏接触与结果、用形容词代替可见行为。",
    },
    {
        "rule_id": "craft.continuity_handoff.v1",
        "stage": "shot_prompt",
        "trigger": "director_vision or multi_shot",
        "instruction": "连续时空动作承接上一镜退出状态；换场、跳时、回放或平行叙事则声明对应关系，不强行复制上一镜为本镜起点。写清本镜可见起始、因果推进、落点与交接。",
        "avoid": "无意重复上一镜、未说明的支撑或道具归属跳变、没有可见起止状态。",
    },
    {
        "rule_id": "craft.subject_identity_lock.v1",
        "stage": "shot_prompt",
        "trigger": "director_vision.continuity_locks.characters",
        "instruction": "角色身份、服装、发型、关键外观特征和屏幕方向沿用视觉锚点，只有合同声明时才改变。",
        "avoid": "每镜重新设计角色、泛化为‘一个人’、未声明换装。",
    },
    {
        "rule_id": "craft.space_prop_lock.v1",
        "stage": "shot_prompt",
        "trigger": "director_vision.continuity_locks.locations or props",
        "instruction": "保持场景几何、关键道具状态、光源方向和视线轴；变化必须写成动作结果。",
        "avoid": "背景随机换位、道具凭空出现或消失、光影跳变。",
    },
    {
        "rule_id": "craft.single_camera_move.v1",
        "stage": "shot_prompt",
        "trigger": "always",
        "instruction": "镜头位置、焦段与运动服务于当前叙事重点；允许固定观察、组合运镜和分阶段注意力转移，写清路径、目标及变化原因，并核对所选模型的执行能力。",
        "avoid": "彼此矛盾的摄影指令、无动机的炫技、摄影运动遮挡关键表演或动作关系。",
    },
    {
        "rule_id": "craft.causal_physics.v1",
        "stage": "shot_prompt",
        "trigger": "action or motion",
        "instruction": "动作按发力、接触、受力、重心变化、结果反馈表达，保留真实时间顺序。",
        "avoid": "瞬移、无接触受击、果冻形变、舞蹈化摆姿势。",
    },
    {
        "rule_id": "craft.visual_motif_recur.v1",
        "stage": "director_plan",
        "trigger": "director_vision.visual_motifs",
        "instruction": "视觉母题在构图、材质或光影中重复出现，作为跨镜识别线索，而不是每镜新增装饰。",
        "avoid": "母题只在第一镜出现、无关装饰堆叠、改变主叙事信息。",
    },
    {
        "rule_id": "craft.rhythm_beats.v1",
        "stage": "director_plan",
        "trigger": "director_vision.rhythm",
        "instruction": "按全片节奏曲线分配镜头时长；转折镜头保留动作前后余量，不用慢动作掩盖缺失的因果。",
        "avoid": "每镜同长度、无节奏转折、慢动作替代叙事推进。",
    },
    {
        "rule_id": "craft.lighting_motivation.v1",
        "stage": "shot_prompt",
        "trigger": "lighting_context",
        "instruction": "光线必须从画面内可解释的光源出发，写清光源位置、方向、色温和主辅光关系。",
        "avoid": "只说氛围光 / 电影光、无来源的万能顶光、亮暗关系互相矛盾。",
    },
    {
        "rule_id": "craft.lighting_continuity.v1",
        "stage": "shot_prompt",
        "trigger": "lighting_context or multi_shot",
        "instruction": "跨镜保持主光方向、色温和光比连续；色温混用或方向变化必须有可见动机并写成合同变化。",
        "avoid": "上一镜像窗外冷光、下一镜无解释变成暖侧光；阴影方向跨镜跳变。",
    },
    {
        "rule_id": "craft.screen_direction_axis.v1",
        "stage": "shot_prompt",
        "trigger": "video or multi_shot or action_or_dialogue",
        "instruction": "先声明轴线、机位侧和人物朝向，镜头不越轴；必须越轴时用中性镜或移动镜过渡，并保持视线方向可追踪。",
        "avoid": "两人对望却都朝同侧、追跑方向反跳、视线与下一镜落点不一致。",
    },
    {
        "rule_id": "craft.color_quota_60_30_10.v1",
        "stage": "shot_prompt",
        "trigger": "color_context or image",
        "instruction": "按既定美术风格与本镜注意力设计色彩主次，颜色对应材质与光照，跨镜保持可识别的风格。六三一配色仅是可选方法，允许单色、均衡配色或有动机的色彩变化，不强制面积比例。",
        "avoid": "无动机重新配色、用固定冷暖模板代替戏剧设计、色彩主次遮掉必要信息。",
    },
    {
        "rule_id": "craft.edit_on_action.v1",
        "stage": "shot_prompt",
        "trigger": "video or multi_shot",
        "instruction": "优先切在动作、视线或形状匹配的瞬间；交接镜的上一镜结束状态就是本镜切点，匹配剪辑要指出匹配的那一项。",
        "avoid": "动作中途无因果硬切、只靠淡入淡出拼接、相邻镜头没有可追踪的视觉连接。",
    },
    {
        "rule_id": "craft.beat_cadence.v1",
        "stage": "shot_prompt",
        "trigger": "video or action_context",
        "instruction": "按动作准备、接触、后果与人物反应组织可见节拍，时长服从物理过程、戏剧节奏与模型能力。允许停顿、倾听、持续动作和静止观察，不要求固定秒数的节拍或持续填满状态变化。",
        "avoid": "抽象地写快速或激烈、遗漏接触与受力后果、为了填满时长添加无依据动作。",
    },
    {
        "rule_id": "craft.sound_layers.v1",
        "stage": "shot_prompt",
        "trigger": "video or sound_context",
        "instruction": "声音至少区分环境底噪、画面内声源和与动作绑定的音效落点；对白保持逐字原文，不把它混进纯视觉描写。",
        "avoid": "只有台词没有空间声、音效与接触点不同步、加入画面里不存在的声源。",
    },
    {
        "rule_id": "craft.cross_episode_continuity.v1",
        "stage": "director_plan",
        "trigger": "series_context",
        "instruction": "跨集只引用已有 AssetPassport revision、剧集圣经 revision 和锁定字段；角色造型、场景基调、色彩光线和时间线不得按集重建。",
        "avoid": "复制一份跨集资产真相、每集重新描述角色、集间引用过期版本。",
    },
    {
        "rule_id": "craft.positive_constraint_rewrite.v1",
        "stage": "shot_prompt",
        "trigger": "always",
        "instruction": "把禁止项改写成画面里必须出现的正向可见事实；负向词只保留极少数模型确实需要避开的短约束。",
        "avoid": "不要出现文字 / 不要变形 / 不要脏乱等长负向清单代替构图、材质、身份和物理约束。",
    },
    {
        "rule_id": "craft.final_delivery_qc.v1",
        "stage": "delivery",
        "trigger": "review_context",
        "instruction": "把任务完成与成片合格分开：只有视频流、音频流、分辨率、时长、无缺镜/黑场、字幕、音画同步、可回读文件与哈希回执都有证据时，才能声明成片通过。",
        "avoid": "以任务 completed 代替成片 QC、只检查文件存在、用单测绿灯冒充真实成片。",
    },
    {
        "rule_id": "craft.story_promise.v1",
        "stage": "story",
        "trigger": "story_context",
        "instruction": "先写清戏剧承诺、持续冲突引擎、主角要付出的代价和失败后果；每一集都必须改变关系、目标、资源、认知或风险中的至少一项。",
        "avoid": "只有世界观介绍、每集都在重复同一种冲突、状态回到原点、结局没有兑现开场承诺。",
    },
    {
        "rule_id": "craft.episode_state_change.v1",
        "stage": "story",
        "trigger": "series_context",
        "instruction": "集与集之间沿用同一份进入状态、退出状态和信息权限；上一集退出状态就是下一集进入状态，变化必须能被观众看见。",
        "avoid": "跨集重置角色关系、遗忘已经发生的代价、用旁白补上观众没看见的变化。",
    },
    {
        "rule_id": "craft.real_hook.v1",
        "stage": "story",
        "trigger": "series_context",
        "instruction": "钩子必须先从已有欲望、秘密、资源或关系制造可见后果，再留下下一集必须回答的问题。",
        "avoid": "突然来人、突发反转、神秘物件无因果出现；这种假钩子只能制造噪音。",
    },
    {
        "rule_id": "craft.visual_bible_gate.v1",
        "stage": "assets",
        "trigger": "visual_context",
        "instruction": "先锁定视觉圣经、Look ID、色彩剧本、光线性格、角色身份、场景几何和道具状态，再写任何生成提示词。",
        "avoid": "资产未锁先抽卡、每镜重新描述风格、把一张好看的参考图当成可复用资产。",
    },
    {
        "rule_id": "craft.asset_view_by_exposure.v1",
        "stage": "assets",
        "trigger": "asset_context",
        "instruction": "资产视图数量按镜头会暴露的面推导：背面、侧面、俯视、全身、表情或道具状态只有在镜头真正出现时才要求。",
        "avoid": "机械生成所有视角、只给一张正面图却拍背面、用多余视图污染参考图配额。",
    },
    {
        "rule_id": "craft.prompt_array_order.v1",
        "stage": "prompt",
        "trigger": "prompt_context",
        "instruction": "分镜提示词固定为 8 段式，运动提示词固定为 6 段式，段间只用 ` + ` 连接；段序是合同，不是风格建议。",
        "avoid": "把十二槽、英文模板或散文直接混进产品格式，省略技术参数段或临时改变段序。",
    },
    {
        "rule_id": "craft.identity_motion_separation.v1",
        "stage": "prompt",
        "trigger": "prompt_context",
        "instruction": "角色卡逐字复制，身份块与运动块分开，第 7 段保持全片美术风格；第 8 段按本镜观看目的选择焦段、光圈、景深和快门感，允许有动机的变化，保持干净成像与资产身份。",
        "avoid": "压缩角色卡、把临时表情写成永久身份、无动机的风格漂移、照抄技术参数导致本镜动作关系看不清。",
    },
    {
        "rule_id": "craft.dialogue_as_action.v1",
        "stage": "dialogue",
        "trigger": "dialogue_context",
        "instruction": "明确台词在当下的作用：交流、试探、争取、掩饰、反应或自言自语等；潜台词和策略在有戏剧依据时使用，不为每句话强造双层动机。",
        "avoid": "用台词解释观众已经看见的信息、角色替编剧汇报设定、台词只负责推进时间。",
    },
    {
        "rule_id": "craft.speech_timing.v1",
        "stage": "dialogue",
        "trigger": "dialogue_context",
        "instruction": "按实际语言、字数、表演语速、呼吸、打断与倾听反应估计对白时长，再核对模型可生成时长。中文每秒三四字仅作初估，不强制半秒句间停顿或三到八秒窗口；必要时调整承载方式，保留逐字台词。",
        "avoid": "把长台词塞进短镜头、无意挤掉反应、为符合固定窗口删掉必要台词。",
    },
    {
        "rule_id": "craft.multi_speaker_lipsync.v1",
        "stage": "dialogue",
        "trigger": "dialogue_context",
        "instruction": "逐句标明台词、说话人、时间关系与口型要求。按模型已验证能力选择单人、轮流或打断对白；能力不足时保留故事意图并换模型或拆执行，不把多人词句混进无说话人归属的槽位。",
        "avoid": "把多个说话人的词拼进同一个音频槽、把镜头元指令当成台词、让模型自行补台词。",
    },
    {
        "rule_id": "craft.sound_mix_priority.v1",
        "stage": "sound",
        "trigger": "sound_context",
        "instruction": "对白优先，其次是画内关键声源和动作音效，再叠环境声、拟音和音乐；环境声必须让空间成立，不能被 BGM 淹没。",
        "avoid": "所有镜头只铺音乐、音效与接触点不同步、画外声源没有叙事动机。",
    },
    {
        "rule_id": "craft.performance_eyes_alive.v1",
        "stage": "performance",
        "trigger": "performance_context",
        "instruction": "选取能表达本镜人物意图的可见表演细节，如视线目标、倾听反应、呼吸、重心或手势变化，并放在正确动作位置。允许克制、静止和有意义的停顿，不要求每镜填齐所有细节或添加无依据的动作。",
        "avoid": "只有情绪形容词、眼睛空洞不聚焦、所有人都用同一套动作、把台词当口型表演。",
    },
    {
        "rule_id": "craft.composition_visual_weight.v1",
        "stage": "composition",
        "trigger": "composition_context",
        "instruction": "每镜先定视觉重音：谁或什么必须被看见、谁在前景、谁在背景、道具落在哪条视线或动作线上。",
        "avoid": "居中对称当默认、装饰填满画面、主体被环境吃掉、景别变化没有叙事理由。",
    },
    {
        "rule_id": "craft.transition_selection.v1",
        "stage": "editing",
        "trigger": "transition_context",
        "instruction": "按信息揭示、节奏与情感目的选择硬切、声音桥、动作匹配、视线、遮挡、形状匹配、省略或有动机的其他手法。允许静止反应切点与长镜不切；这里只规划生成素材和交接，专业剪辑交由外部软件。",
        "avoid": "机械重复一种转场、观众无法理解的空间跳变、用转场装饰掩盖关键动作缺失。",
    },
    {
        "rule_id": "craft.local_repair_before_regenerate.v1",
        "stage": "repair",
        "trigger": "repair_context",
        "instruction": "一处不对先修局部；能用切镜、反应镜、遮挡和声音桥遮住的问题，不重出整段。",
        "avoid": "为一个小缺陷重抽整镜、连续改动多个变量、未记录每次重试的差异。",
    },
    {
        "rule_id": "craft.stop_churn.v1",
        "stage": "repair",
        "trigger": "repair_context",
        "instruction": "按用户授权预算、失败证据和改善趋势决定是否继续返工；没有改善就停下复核输入、参考和模型能力，必要时换模型或调整执行设计。简化前保留核心叙事与表演，不默认重抽十几轮或削成单动作模板。",
        "avoid": "无限改措辞、用更高分辨率掩盖结构错误、把不收敛归因成模型不给力。",
    },
    {
        "rule_id": "craft.screening_earliest.v1",
        "stage": "screening",
        "trigger": "screening_context",
        "instruction": "粗剪最便宜，应该最早试映；记录弃片点、记得住的画面和观众问题，再映射到最小的剪短、改词、局部修复或单镜重出。",
        "avoid": "只看技术达标就宣布完成、用创作者自评替代观众反馈、收到意见后整片重做。",
    },
    {
        "rule_id": "craft.no_text_in_generation.v1",
        "stage": "title",
        "trigger": "title_context",
        "instruction": "生成阶段确保画面表面无文字、字幕、标题和水印；片名、字幕、演职员表和图形字在后期叠加。",
        "avoid": "让图像模型写字、依赖模型拼对中文、把字幕烧进生成画面。",
    },
    {
        "rule_id": "craft.cost_preflight.v1",
        "stage": "cost",
        "trigger": "cost_context",
        "instruction": "开工前按镜头数、单镜时长、接受率、单价和返工闭包估算生成次数与成本区间；缺接受率就明确未验证。",
        "avoid": "拿事后账单当预算、用别人产线的接受率冒充本仓实测、只报总额不拆镜头和重试。",
    },
    {
        "rule_id": "craft.delivery_measurable.v1",
        "stage": "delivery",
        "trigger": "delivery_context",
        "instruction": "交付必须检查容器、视频流、音频流、分辨率、帧率、时长、黑场、冻结帧、音画同步、响度、峰值、字幕、色彩空间、码率、文件回读和 SHA-256。",
        "avoid": "只检查文件存在、只看分辨率、把任务回执当 QC、缺少媒体证据就宣称通过。",
    },
    {
        # 2026-09-18 实战晋升：一部 2 分钟 28 镜的成片，台词镜占 90%（时长口径），
        # 观众看到的是"配了插图的广播剧"。合同层由 `script.viewability.*` 判定，
        # 这条规则负责在写剧本时就把预算讲清楚。
        "rule_id": "craft.dialogue_ratio_budget.v1",
        "stage": "story",
        "trigger": "story_context",
        "instruction": "按这场戏的意图决定对白与画面如何分工。对白、倾听和沉默都可承载行动与关系变化；对白比例仅作审看提醒，不按固定百分比删台词或添动作。",
        "avoid": "重复解释观众已看懂的信息、无表演意图地填满时长、为降低比例删掉必要对白。",
    },
    {
        # 同上：静态对峙堆到几十秒就只是把同一件事说很多遍。
        "rule_id": "craft.standoff_ceiling.v1",
        "stage": "story",
        "trigger": "story_context",
        "instruction": "对峙、倾听和停顿的时长由信息、关系与表演张力决定；用视线、反应、潜台词或有动机的行动推进，允许静止长镜，不因超过固定秒数强插走位或切镜。",
        "avoid": "没有新信息或关系变化的同义反复、无动机走位、为填节奏添加装饰动作。",
    },
    {
        "rule_id": "craft.framing_distance_arc.v1",
        "stage": "shot_prompt",
        "trigger": "video or multi_shot",
        "instruction": "按观众此刻需要看见或暂时不能看见的信息选择景别；用构图、视线、地标与动作建立必要空间关系。允许贴身开场、连续同景别、单一景别序列或长镜，不强制景别数量、比例或全景开场。",
        "avoid": "不顾戏剧目的轮换景别、关键空间关系无法理解、近景遮掉必要接触或反应。",
    },
    {
        # 这次最实用的方法发现：模型出不了连续套招，但能出单点冲击。
        "rule_id": "craft.action_fragment_shots.v1",
        "stage": "shot_prompt",
        "trigger": "action_context",
        "instruction": "根据动作关系、表演重点与模型能力选择连续镜、动作匹配、反应镜、细节插入或省略。先写清准备、接触、后果及落点，再安排可执行的生成时长；碎片快切是可选手法，不默认每片一两秒或限定每次生成镜数。",
        "avoid": "用碎片掩盖无法理解的因果、超出模型能力的动作堆叠、把打斗写成一句无接触关系的剑光相交。",
    },
    {
        "rule_id": "craft.text_visual_alignment.v1",
        "stage": "delivery",
        "trigger": "review_context",
        "instruction": "每处画面里声明的视觉事件（某个动作、某个道具状态）都要有对应验证帧：动作镜与道具镜出片后抽 3 帧确认画面真的含它，再决定是否接受。",
        "avoid": "只看画面好看、不核对剧本写的动作是否真的拍到了——实测出现过「剧本写剑断、画面是一柄完整的剑」，意思正好相反。",
    },
)


def _text(value: object, *, limit: int = 1_200) -> str:
    return str(value or "").strip()[:limit]


# ``inject_filmcraft_rules`` has always read the source through
# ``_text(..., limit=12_000)``.  The facade decision used to scan the raw
# string, so a cue past that limit could turn the decision on while the rule
# selection saw nothing (or the other way round).  Both paths now normalize
# through the one helper below.
ACTION_CONTEXT_TEXT_LIMIT = 12_000


def normalize_action_context_text(source_text: object) -> str:
    """Return the exact source text the action-context decision reads.

    Public on purpose: the rule selection in this module and the workflow
    facade decision must see identical characters, including the 12,000
    character ceiling this module has always applied.
    """

    return _text(source_text, limit=ACTION_CONTEXT_TEXT_LIMIT)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _has_director_vision(vision: Mapping[str, Any]) -> bool:
    return bool(vision.get("vision_revision") or vision.get("schema"))


def _multi_shot(params: Mapping[str, Any]) -> bool:
    try:
        return int(params.get("shot_count", 0) or 0) > 1
    except (TypeError, ValueError):
        return False


def _keyword_hit(text: str, *keywords: str) -> bool:
    lowered = text.casefold()
    return any(keyword.casefold() in lowered for keyword in keywords)


def _latest_negation(clause: str, clause_start: int, lowered: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` of the latest negation phrase inside ``clause``.

    ``start``/``end`` are relative to ``clause``; the absolute position is
    ``clause_start + start``.  The latest phrase wins because only the closest
    negation can be the one attached to the cue ("不要温情，没有台词的打斗"
    has two negations and only the second one is about the fight).
    """

    best: tuple[int, int] | None = None
    for negation in _ACTION_CONTEXT_NEGATIONS:
        if negation == "别":
            # ``别`` is also the second character of ordinary words
            # (特别/告别/分别/个别/辨别/区别/识别/性别/道别/级别/差别/离别),
            # so only its last *real* occurrence counts as a negation.
            start = clause.rfind("别")
            while start >= 0:
                absolute = clause_start + start
                if absolute == 0 or lowered[absolute - 1] not in _NON_NEGATING_PREFIXES:
                    break
                start = clause.rfind("别", 0, start)
            if start < 0:
                continue
        else:
            start = clause.rfind(negation)
            if start < 0:
                continue
        candidate = (start, start + len(negation))
        if best is None or candidate[0] >= best[0]:
            best = candidate
    return best


def _strip_tokens(text: str, tokens: tuple[str, ...]) -> str:
    """Remove every occurrence of ``tokens`` from ``text``, repeatedly.

    Repeated passes matter because removing one token can expose another
    ("出现任何打斗" loses 出现 then 任何).

    R9: every token is matched longest-first.  The tables are ordered for
    readability, and a short entry can sit in front of the longer one that
    starts with it ("特写" before "大特写", "武打" before "武打动作"), so a plain
    tuple-order scan ate the short one first and left the remainder ("大",
    "动作") behind as if it were content — the prohibition then looked like it
    was about a real word.  Equal lengths keep their table order (``sorted``
    is stable).
    """

    ordered = sorted(tokens, key=len, reverse=True)
    remaining = text
    changed = True
    while changed and remaining:
        changed = False
        for token in ordered:
            if token and token in remaining:
                remaining = remaining.replace(token, "")
                changed = True
    return remaining


def _inside_listed_cue(text: str, start: int, end: int) -> bool:
    """Return whether ``text[start:end]`` is only part of a longer cue phrase.

    A scene noun is also a word inside real cues (``动作场面``), so a frame match
    that exists only because a cue was cut in half must not be stripped — the
    cue has to stay readable for the enumeration check.
    """

    width = end - start
    for cue in _ACTION_CONTEXT_CUES:
        if len(cue) <= width:
            continue
        for offset in range(max(0, start - len(cue) + 1), start + 1):
            if offset + len(cue) >= end and text.startswith(cue, offset):
                return True
    return False


def _scene_frame_end(text: str, start: int) -> int:
    """Return the end offset of a bounded scene frame at ``start``, else ``start``.

    Shape: a short scene noun (``镜头``/``画面``/``场面``), one optional locative
    suffix, one optional bridge word (``出现``/``有``).  ``画面``/``镜头`` alone are
    still content nouns (R8: "不要``画面``" is about the picture, not about the
    cue); ``场面`` alone is the staging word and closes the frame.  The match is
    capped at ``_NEGATION_SCENE_FRAME_MAX`` characters and never crosses the
    enumeration separator (no separator character can match the shape), so it
    cannot swallow a clause.
    """

    for noun in _NEGATION_SCENE_NOUNS:
        if not text.startswith(noun, start):
            continue
        cursor = start + len(noun)
        if cursor < len(text) and text[cursor] in _NEGATION_FRAME_SUFFIXES:
            cursor += 1
        for bridge in _NEGATION_SCENE_BRIDGE_WORDS:
            if text.startswith(bridge, cursor):
                cursor += len(bridge)
                break
        if cursor == start + len(noun) and noun not in _NEGATION_SCENE_BARE_NOUNS:
            continue
        if cursor - start > _NEGATION_SCENE_FRAME_MAX:
            continue
        if _inside_listed_cue(text, start, cursor):
            continue
        return cursor
    return start


def _strip_scene_frames(text: str) -> str:
    """Remove every bounded no-preposition scene frame from ``text``."""

    cursor = 0
    while cursor < len(text):
        end = _scene_frame_end(text, cursor)
        if end > cursor:
            text = text[:cursor] + text[end:]
            # A new frame can now start at the same offset ("场面镜头里").
            continue
        cursor += 1
    return text


def _strip_locative_frames(text: str) -> str:
    """Remove bounded adjunct frames ("在画面里", "镜头里出现").

    R8: a preposition opens an adjunct of the cue, so the words inside the
    frame describe where (or in what) the cue happens and never become the
    object of the prohibition.  The frame is deliberately bounded — ``在`` plus
    at most ``_NEGATION_FRAME_MAX_ADJUNCT`` characters plus one optional
    locative suffix — so nothing long or clause-sized can be swallowed by it.

    R9: the same adjunct is often written without the preposition
    ("不要``镜头里``出现打斗", "不要``场面``打斗").  Those frames are recognised by
    ``_scene_frame_end`` under the same bounded, structural rules; the content
    nouns themselves are still not bridge tokens.
    """

    return _strip_scene_frames(_strip_prepositional_frames(text))


def _strip_prepositional_frames(text: str) -> str:
    """Remove ``在`` + bounded adjunct + optional locative suffix frames."""

    for preposition in _NEGATION_FRAME_PREPOSITIONS:
        while True:
            start = text.find(preposition)
            if start < 0:
                break
            cursor = start + len(preposition)
            limit = min(cursor + _NEGATION_FRAME_MAX_ADJUNCT, len(text))
            while cursor < limit and text[cursor] not in _ENUMERATION_SEPARATORS:
                cursor += 1
                # A locative suffix closes the frame wherever it appears, so
                # "在画面里出现打斗" keeps 出现 for the bridge-token pass instead
                # of eating it as part of the frame.
                if text[cursor - 1] in _NEGATION_FRAME_SUFFIXES:
                    break
            # The suffix still belongs to the frame when it lands on the bound.
            if cursor < len(text) and text[cursor] in _NEGATION_FRAME_SUFFIXES:
                cursor += 1
            text = text[:start] + text[cursor:]
    return text


def _strip_negation_scaffolding(text: str) -> str:
    """Remove everything a prohibition may carry in front of its own cue."""

    remaining = _strip_locative_frames(text)
    # R9: longest-match (see ``_strip_tokens``) keeps "一个人" whole instead of
    # letting the bridge token "一个" strip it down to a bare 人.
    return _strip_tokens(
        remaining, _NEGATION_CUE_ADJUNCT_TOKENS + _NEGATION_BRIDGE_TOKENS
    )


def _only_bridge_tokens(text: str) -> bool:
    """Return whether ``text`` is prohibition scaffolding instead of content.

    Scaffolding (R8, extended by R9) is: nothing, function words
    (``_NEGATION_BRIDGE_TOKENS``), a bounded locative frame — prepositional
    ("在画面里") or a no-preposition scene frame ("镜头里出现") — or a cue
    adjunct (``_NEGATION_CUE_ADJUNCT_TOKENS``: 景别词与数量词).  Any other
    character means the negation was describing that word, not the cue
    ("没有台词的打斗" is a fight without dialogue, not a ban on fights).
    Token removal is longest-match, so "大特写"/"中近景" stay whole.
    """

    if not text:
        return True
    return not _strip_negation_scaffolding(text)


def _only_bridge_tokens_or_cues(text: str) -> bool:
    """Like ``_only_bridge_tokens`` but also accepts already listed cues.

    Used for the segments that precede the last ``、`` of an enumeration:
    "不要打斗、爆炸、群殴" lists cues under one prohibition, so the cue names
    themselves — and their adjuncts — are allowed to sit between the negation
    and the last cue.
    """

    remaining = _strip_negation_scaffolding(text)
    if not remaining:
        return True
    return not _strip_tokens(remaining, _ACTION_CONTEXT_CUES)


def _has_affirmative_context_marker(text: str) -> bool:
    return any(marker in text for marker in _AFFIRMATIVE_CONTEXT_MARKERS)


def _negation_attached(lowered: str, cue_index: int, negation: tuple[int, int]) -> bool:
    """Return whether the negation phrase really governs the cue at ``cue_index``.

    ``negation`` is an absolute ``(start, end)`` pair inside ``lowered``.
    Attachment (R7, revised by R8) means the text between the end of the
    negation and the start of the cue is prohibition scaffolding: function
    words, a prepositional locative frame ("在画面里"), or a cue adjunct
    ("中景"/"特写"/"两人").  Any other content word in that gap means the
    negation was talking about that word instead ("没有``台词``的巷口决战" bans
    dialogue, not the fight), and an affirmative marker in the gap re-opens the
    cue ("不要``打斗``，要看追逐").
    """

    _, neg_end = negation
    between = lowered[neg_end:cue_index]
    if not between:
        return True
    if _has_affirmative_context_marker(between):
        return False
    delimiter = _ENUMERATION_SEPARATORS[0]
    if delimiter not in between:
        return _only_bridge_tokens(between)
    segments = between.split(delimiter)
    # R8: the segments *before* the last separator have to be scaffolding plus
    # already listed cues — that is what makes the text an enumeration of one
    # prohibition ("不要打斗、爆炸、群殴").  The last item is not inspected
    # again: the prohibition is already anchored on a listed cue, so
    # "两人群殴" / "三人追逐" / "近身肉搏" are covered by it, and only an
    # explicit negation or an affirmative marker re-opens an item
    # ("不要打斗、要看追逐" is True).  A list whose earlier items are not cues is
    # not a prohibition list at all, which is why "不要温情、两人打斗" keeps its
    # affirmative second item — 温情 is not a cue, so the negation stays with it.
    return all(_only_bridge_tokens_or_cues(segment) for segment in segments[:-1])


def _negated_action_cue(lowered: str, index: int) -> bool:
    """Return whether a negation governs the cue that starts at ``index``.

    Two scopes, both required (R6 + R7, revised by R8): the negation has to sit
    in the cue's own clause — the text between the cue and the closest clause
    boundary before it, so it never leaks across 「，。；！？,.;!?\n」 — and it has
    to be *attached* to the cue, so a negation that introduces another noun does
    not switch the cue off.  ``、`` is not a clause boundary: it separates items
    of one enumeration, and a prohibition anchored on a listed cue governs the
    later items unless an affirmative marker re-opens them.
    """

    boundary = max(
        (lowered.rfind(char, 0, index) for char in _CLAUSE_BOUNDARIES),
        default=-1,
    )
    clause_start = boundary + 1
    clause = lowered[clause_start:index]
    if not clause:
        return False
    negation = _latest_negation(clause, clause_start, lowered)
    if negation is None:
        return False
    return _negation_attached(
        lowered,
        index,
        (clause_start + negation[0], clause_start + negation[1]),
    )


def _has_filmcraft_action_context(source_text: object) -> bool:
    """Decide whether a request states an explicit combat/action scene.

    Legacy callers (canvas prompt optimization) never pass
    ``params["action_context"]``, so their rules are selected from this one
    authoritative phrase list.  A cue that is turned off by wording in its own
    clause ("全片不要打斗，也不要追逐") does not count, while an affirmative
    clause after a negated one ("不要温情，要打斗") does.
    """

    lowered = normalize_action_context_text(source_text).casefold()
    if not lowered:
        return False
    for cue in _ACTION_CONTEXT_CUES:
        needle = cue.casefold()
        start = 0
        while True:
            index = lowered.find(needle, start)
            if index < 0:
                break
            if not _negated_action_cue(lowered, index):
                return True
            start = index + len(needle)
    return False


def _style_anchor(vision: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(vision.get("style_anchor"))


def _cinematic(vision: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(vision.get("cinematic"))


def _rule_applies(
    rule: Mapping[str, Any],
    *,
    node_type: str,
    vision: Mapping[str, Any],
    params: Mapping[str, Any],
    project_dna: Mapping[str, Any],
    source_text: str,
    creation_stage: str,
) -> bool:
    trigger = _text(rule.get("trigger"))
    if trigger == "always":
        return True
    if trigger == "image":
        return node_type == "image"
    if trigger == "video":
        return node_type == "video"
    if trigger == "director_vision or multi_shot":
        return _has_director_vision(vision) or _multi_shot(params)
    if trigger == "video or multi_shot":
        return node_type == "video" or _multi_shot(params)
    if trigger == "video or multi_shot or action_or_dialogue":
        return (
            node_type == "video"
            or _multi_shot(params)
            or _keyword_hit(source_text, "对话", "对白", "争执", "对视", "动作", "追逐", "打斗")
        )
    if trigger == "action_context":
        # An explicit decision wins whenever the caller states one; the
        # storyboard compiler always does.  ``params`` can arrive from client
        # JSON as ``dict[str, Any]``, so only a real bool is a decision: a
        # string "false" (or "true", a number, anything else) is not honored,
        # it turns the rule off instead of being read through ``bool()``.
        # Callers that never pass the key (freezone prompt optimization) keep
        # the keyword path, narrowed to the curated phrase cues above.
        if "action_context" in params:
            value = params.get("action_context")
            return value if isinstance(value, bool) else False
        return _has_filmcraft_action_context(source_text)
    if trigger == "lighting_context":
        anchor = _style_anchor(vision)
        lighting = _mapping(_cinematic(vision).get("lighting"))
        return bool(
            anchor.get("lighting")
            or lighting
            or _keyword_hit(source_text, "光", "灯", "窗", "夜", "晨", "黄昏", "烛", "霓虹")
            or any(key in params for key in ("lighting", "light_source", "color_temperature"))
        )
    if trigger == "lighting_context or multi_shot":
        anchor = _style_anchor(vision)
        return bool(
            anchor.get("lighting")
            or _cinematic(vision).get("lighting")
            or _multi_shot(params)
            or _keyword_hit(source_text, "光", "灯", "窗", "夜", "晨", "黄昏")
        )
    if trigger == "color_context or image":
        anchor = _style_anchor(vision)
        return bool(
            node_type == "image"
            or anchor.get("color_palette")
            or _cinematic(vision).get("color_look")
            or _keyword_hit(source_text, "颜色", "色彩", "色调", "色板", "调色")
        )
    if trigger == "video or sound_context":
        return bool(
            node_type == "video"
            or _cinematic(vision).get("sound")
            or _keyword_hit(source_text, "声音", "音效", "环境声", "音乐", "对白")
            or params.get("sound_cues")
        )
    if trigger == "series_context":
        continuity = _mapping(_cinematic(vision).get("continuity"))
        return bool(
            continuity.get("series_id")
            or vision.get("series_id")
            or vision.get("episode_index") not in (None, "")
            or project_dna.get("series_id")
            or project_dna.get("visual_bible_revision")
        )
    if trigger == "review_context":
        return creation_stage.casefold() in {"review", "delivery", "publish", "compose", "assembly"}
    if trigger == "story_context":
        return creation_stage.casefold() in {"story", "script", "development", "planning"} or bool(
            params.get("episode_count") or params.get("series_id")
        )
    if trigger == "visual_context":
        return (
            creation_stage.casefold() in {"assets", "style", "visual", "preproduction"}
            or bool(vision.get("style_anchor"))
            or bool(_cinematic(vision).get("color_look"))
        )
    if trigger == "asset_context":
        return creation_stage.casefold() in {"assets", "preproduction"} or bool(
            params.get("asset_id") or params.get("asset_ids")
        )
    if trigger == "prompt_context":
        return creation_stage.casefold() in {
            "shot_prompt",
            "prompt",
            "storyboard",
            "video_prompt",
        } or bool(params.get("prompt_format") == "8+6")
    if trigger == "dialogue_context":
        return bool(
            params.get("dialogue")
            or params.get("dialogue_text")
            or _keyword_hit(source_text, "对白", "台词", "说话", "口型", "配音")
        )
    if trigger == "sound_context":
        return bool(
            params.get("sound_cues")
            or _cinematic(vision).get("sound")
            or _keyword_hit(source_text, "声音", "环境声", "音效", "音乐", "拟音", "混音")
        )
    if trigger == "performance_context":
        return bool(
            params.get("performance")
            or _keyword_hit(source_text, "表演", "表情", "眼神", "呼吸", "反应", "哭", "笑")
        )
    if trigger == "composition_context":
        return bool(
            params.get("composition")
            or _keyword_hit(source_text, "构图", "景别", "前景", "中景", "特写", "机位")
        )
    if trigger == "transition_context":
        return bool(
            params.get("transition")
            or _keyword_hit(source_text, "转场", "切口", "叠化", "硬切", "衔接", "J-Cut")
        )
    if trigger == "repair_context":
        return creation_stage.casefold() in {"repair", "review", "media_review"} or bool(
            params.get("retry_count") or params.get("attempt_count")
        )
    if trigger == "screening_context":
        return creation_stage.casefold() in {"screening", "review", "publish"} or bool(
            params.get("screening_feedback")
        )
    if trigger == "title_context":
        return bool(
            params.get("title_sequence")
            or _keyword_hit(source_text, "片头", "标题", "字幕", "片名", "logo")
        )
    if trigger == "cost_context":
        return bool(
            params.get("estimate_inputs")
            or params.get("budget")
            or params.get("acceptance_rate")
            or params.get("episode_count")
        )
    if trigger == "delivery_context":
        return creation_stage.casefold() in {"delivery", "publish", "compose", "assembly"}
    if trigger == "director_vision.continuity_locks.characters":
        return bool(_mapping(vision.get("continuity_locks")).get("characters"))
    if trigger == "director_vision.continuity_locks.locations or props":
        locks = _mapping(vision.get("continuity_locks"))
        return bool(locks.get("locations") or locks.get("props"))
    if trigger == "action or motion":
        return bool(params.get("camera_movement") or params.get("camera") or node_type == "video")
    if trigger == "director_vision.visual_motifs":
        return bool(vision.get("visual_motifs"))
    if trigger == "director_vision.rhythm":
        return bool(vision.get("rhythm"))
    return False


def inject_filmcraft_rules(
    *,
    node_type: str,
    params: Mapping[str, Any] | None = None,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    source_text: str = "",
    creation_stage: str = "",
) -> list[dict[str, str]]:
    """Return only rules triggered by the current compile context."""

    clean_params = _mapping(params)
    vision = _mapping(director_vision)
    dna = _mapping(project_dna)
    text = normalize_action_context_text(source_text)
    stage = _text(creation_stage, limit=80)
    selected: list[dict[str, str]] = []
    for raw in RULES:
        if not _rule_applies(
            raw,
            node_type=node_type,
            vision=vision,
            params=clean_params,
            project_dna=dna,
            source_text=text,
            creation_stage=stage,
        ):
            continue
        selected.append(
            {
                "rule_id": _text(raw.get("rule_id"), limit=120),
                "stage": _text(raw.get("stage"), limit=80),
                "trigger": _text(raw.get("trigger"), limit=200),
                "instruction": _text(raw.get("instruction")),
                "avoid": _text(raw.get("avoid")),
            }
        )
    return selected


__all__ = [
    "ACTION_CONTEXT_TEXT_LIMIT",
    "FILMCRAFT_KB_SCHEMA",
    "RULES",
    "inject_filmcraft_rules",
    "normalize_action_context_text",
]
