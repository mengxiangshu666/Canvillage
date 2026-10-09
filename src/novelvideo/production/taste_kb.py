"""Taste knowledge base — what "good enough to ship" means, and which defaults are refused.

``filmcraft_kb`` says *how to write* a shot.  This module says *what counts as
good*, and — more importantly — **names the specific looks that are banned**.

Why naming matters: generative models converge on a small, predictable set of
solutions.  Ask for a night scene and you get a warm key light against cold
blue; ask for a fight and you get slow motion and rain.  "Avoid clichés" does
nothing, because the model has no notion of having used one.  The mechanism
that survives contact is:

* **name the cliché** (a stable id, not an adjective);
* **quarantine it per project** (a ledger, so a look spent once is not spent again);
* **put a number on it** (quotas, not guidance).

The aesthetic profile is the other half: taste recorded as explicit decisions
rather than left implicit in someone's head.  An implicit preference gets
re-derived from scratch every project, which means it defaults to the model's
preference.

Everything here is data.  The checks live in :mod:`taste_engines`.
"""

from __future__ import annotations

from typing import Any

TASTE_KB_SCHEMA = "taste_kb.v1"


# --------------------------------------------------------------------------- #
# Aesthetic profile — the shape taste has to be recorded in
# --------------------------------------------------------------------------- #

AESTHETIC_PROFILE_FIELDS: tuple[dict[str, str], ...] = (
    {
        "field": "north_star",
        "label": "基调一句话",
        "prompt": "这部片子让人记住的那一种感觉，一句话。不要形容词堆叠。",
    },
    {
        "field": "loves",
        "label": "深爱清单",
        "prompt": "你真正喜欢的画面 / 质感 / 处理，每条必须带证据：哪一部、哪一场、哪一眼。",
    },
    {
        "field": "antis",
        "label": "禁用清单",
        "prompt": "你不想再看到的东西。点名具体（例如「雨夜湿地面反光」），不写「俗套」「做作」。",
    },
    {
        "field": "palette_family",
        "label": "色彩所属族",
        "prompt": "冷奢 / 森林 / 黑棕 / 单色加单点 / 高饱和撞色 / 低饱和自然……只取一族。",
    },
    {
        "field": "light_family",
        "label": "光线所属族",
        "prompt": "单一实际光源高光比 / 大面积柔光低反差 / 混合色温 / 顶光压迫 / 无主光环境光。只取一族。",
    },
    {
        "field": "camera_family",
        "label": "摄影机所属族",
        "prompt": "静态长镜 / 手持跟随 / 轨道推进 / 广角贴脸 / 长焦压缩。只取一族。",
    },
    {
        "field": "texture_family",
        "label": "质感所属族",
        "prompt": "胶片颗粒 / 数码干净 / 高噪点 / 柔焦朦胧 / 锐利硬朗。只取一族。",
    },
    {
        "field": "editing_family",
        "label": "剪辑所属族",
        "prompt": "长镜少切 / 快切碎剪 / 匹配剪辑 / 跳切 / 平行交叉。只取一族。",
    },
)


# --------------------------------------------------------------------------- #
# Banned clichés — named, detectable, each with a replacement
# --------------------------------------------------------------------------- #

BANNED_CLICHES: tuple[dict[str, Any], ...] = (
    {
        "cliche_id": "slop.rain-night-wet-ground.v1",
        "label": "雨夜湿地面反光",
        "patterns": ("雨夜", "湿地面", "积水", "水洼", "湿漉", "水面反光", "雨水反光"),
        "why": "夜戏的默认答案：低照度加湿面反射加路灯。视觉廉价、情绪空洞，"
               "且会让所有夜戏长成同一部片子。",
        "instead": "换一个让地面本身有信息的设定：煤渣、纸屑、落叶、沙土、碎瓷砖、干裂的水泥。",
    },
    {
        "cliche_id": "slop.single-warm-key-high-ratio.v1",
        "label": "单一暖光源高光比",
        "patterns": ("唯一主光源", "唯一光源", "光比 8:1", "光比 9:1", "光比 7:1",
                     "暖黄", "暖白", "暗部沉入"),
        "why": "最容易写、也最像「电影感」的光。写一次是选择，写两次就是模板。",
        "instead": "换族：大面积柔光低反差 / 混合色温 / 顶光压迫 / 无主光环境光。",
    },
    {
        "cliche_id": "slop.dust-motes-in-beam.v1",
        "label": "光柱里的尘埃飞舞",
        "patterns": ("尘埃", "浮尘", "尘粒", "光柱", "光斑中", "漂浮"),
        "why": "用来证明空间是真的的默认手段，已经被用尽。",
        "instead": "用有来源的东西证明空间：气流带动的一张纸、蒸汽、烟、呼吸、涟漪。",
    },
    {
        "cliche_id": "slop.character-turns-back.v1",
        "label": "人物回头",
        "patterns": ("回头", "转过身看", "停下回头", "转身回望"),
        "why": "最省力的情绪标点。一用就把前面攒下的克制全泄掉。",
        "instead": "让身体做别的：手停住、呼吸断一拍、把东西放下、往反方向走半步。",
    },
    {
        "cliche_id": "slop.tear-closeup.v1",
        "label": "眼泪特写",
        "patterns": ("眼泪", "泪珠", "泪水滑落", "含泪", "落泪"),
        "why": "把悲伤直接写在脸上，取消了观众的解读空间。",
        "instead": "把情绪放到身体的别处：喉结、手指、下颌、背对时的肩线。",
    },
    {
        "cliche_id": "slop.backlit-silhouette.v1",
        "label": "逆光剪影",
        "patterns": ("逆光", "剪影", "轮廓光勾"),
        "why": "不需要交代脸，就不需要交代人物。是躲闪，不是设计。",
        "instead": "若本意是看不清这个人，用遮挡、失焦或背对来承担，而不是靠光偷懒。",
    },
    {
        "cliche_id": "slop.slowmo-raindrop.v1",
        "label": "慢镜与水花飞散",
        "patterns": ("慢镜", "慢动作", "水珠飞散", "水花炸开", "水花向四周"),
        "why": "动作戏的默认装饰。它不产生信息，只消耗时间。",
        "instead": "慢镜只留给一个真正需要看清的瞬间，且全片只用一次。",
    },
    {
        "cliche_id": "slop.cold-blue-antagonist.v1",
        "label": "冷蓝等于压迫",
        "patterns": ("冷蓝", "冷色", "蓝灰", "青蓝", "冷调"),
        "why": "把色彩当情绪标签，是最容易被看穿的符号化。",
        "instead": "让色彩从场景的实际光源里长出来，而不是从情绪里贴上去。",
    },
    {
        "cliche_id": "slop.ending-white-breath.v1",
        "label": "结尾白气 / 哈气",
        "patterns": ("白气", "哈气", "呼出一口", "呼气成雾", "热气升腾"),
        "why": "把「还有温度」这个意思外包给一个物理现象。用过一次就失效。",
        "instead": "用动作承担：拧开盖子、把手放在某处、把东西留在原地。",
    },
    {
        "cliche_id": "slop.creaking-old-fan.v1",
        "label": "吱呀的旧风扇",
        "patterns": ("风扇", "吱呀", "吊扇", "摇摇晃晃的灯"),
        "why": "老屋 / 拳馆 / 仓库场景的通用填充物，用来暗示时间在这里停住了。",
        "instead": "用与剧情有关的东西承担时间感：日历、封条、搬空的柜子、停走的东西。",
    },
)


# --------------------------------------------------------------------------- #
# Quotas — numbers, because "less of it" is not a limit
# --------------------------------------------------------------------------- #

QUOTAS: dict[str, Any] = {
    "staticShotRatioMax": 0.40,
    "singleCameraMoveRatioMax": 0.40,
    "adjacentSameFramingAllowed": False,
    "sameLightingFamilyRunMax": 3,
    "mustRewardShotRatioMax": 0.35,
    "dialogueWithoutSubtextAllowed": False,
    "everyShotNeedsSinglePurpose": True,
    "clicheHitsPerFilmMax": 1,
}

QUOTA_NOTES: dict[str, str] = {
    "staticShotRatioMax": "全片静止镜头占比。静态本身不是问题，「因为偷懒而静态」是；超过四成说明没在设计运动。",
    "singleCameraMoveRatioMax": "同一运镜的占比。只有一种运镜，等于没有运镜。",
    "adjacentSameFramingAllowed": "相邻同景别是最容易被看出的业余痕迹。",
    "sameLightingFamilyRunMax": "同一种光线性格连续镜数。连续太多说明光没有跟着戏走。",
    "mustRewardShotRatioMax": "「必须有理由才给」的镜头（特写、慢镜、大幅度运动）占比。",
    "dialogueWithoutSubtextAllowed": "没有潜台词的台词是广播，不是戏。",
    "everyShotNeedsSinglePurpose": "一镜一件事。答不出「观众这一刻必须先看到什么」的镜头是填充物。",
    "clicheHitsPerFilmMax": "全片命中的套路段落上限。超过说明审美正在滑向模型的默认值。",
}


# --------------------------------------------------------------------------- #
# Paired samples — teach by contrast, not by rule
# --------------------------------------------------------------------------- #

PAIRED_SAMPLES: tuple[dict[str, str], ...] = (
    {
        "slot": "action",
        "weak": "两人激烈打斗。",
        "strong": "他后退三步，每一步都踩在水边；第三步步幅明显变小，左小臂抬起横挡，"
                  "被击中时肩膀往下一沉，右脚跟抬起半寸卸力。",
        "why": "弱句给的是强度，强句给的是可拍的因果：步幅、接触、受力、卸力。",
    },
    {
        "slot": "performance",
        "weak": "她非常悲伤。",
        "strong": "她先把杯子放下，放得很正；然后手停在杯沿上不动，喉结动了一次；"
                  "眼睛始终没有抬起，呼吸比刚才浅了半拍。",
        "why": "情绪名词模型只能猜；身体的先后顺序它能执行。",
    },
    {
        "slot": "lighting",
        "weak": "氛围感很强的光线。",
        "strong": "唯一的光是头顶那盏 3000K 的旧吊灯，挂在画面右上；"
                  "人物眼窝以下全部落在暗部，只有锁骨以上受光；地面没有反射。",
        "why": "光必须写清来源、位置、色温、照亮到哪里、没照到哪里。",
    },
    {
        "slot": "dialogue",
        "weak": "「我们十年没见了，自从那场大火以后。」",
        "strong": "「你还在用那个牌子。」——潜台词：我一直在注意你。",
        "why": "弱句是信息交代；强句是行动。观众自己补出十年的距离。",
    },
    {
        "slot": "purpose",
        "weak": "这一镜用来交代环境和气氛。",
        "strong": "这一镜先让观众看见那扇门是关着的，这样下一镜他推门时观众会先紧张。",
        "why": "弱句是说明；强句说清它替后面的哪一下做了准备。",
    },
    {
        "slot": "camera",
        "weak": "镜头缓慢推进，营造压抑感。",
        "strong": "机器从 2.4 米匀速推到 1.2 米，推到她的眼睛上停住；"
                  "推进过程中背景的门框从画面两侧退出。",
        "why": "运镜要写起点、终点、速度、落在什么上，以及它让什么离开了画面。",
    },
)


# --------------------------------------------------------------------------- #
# Engines — declared here so the framework and its checks live in one place
# --------------------------------------------------------------------------- #

ENGINE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "engine_id": "engine.single_focus.v1",
        "label": "一镜一件事",
        "question": "观众在这一镜里必须先看到的是哪一样东西？为什么不能是别的？",
        "hard": True,
        "weight": 2,
    },
    {
        "engine_id": "engine.adjective_ban.v1",
        "label": "形容词死刑",
        "question": "这句话里有没有一个词，模型只能靠猜？",
        "hard": True,
        "weight": 2,
    },
    {
        "engine_id": "engine.replaceability.v1",
        "label": "可替换性检测",
        "question": "把这个动作或这句台词换掉，故事会损失什么？答不出就是废的。",
        "hard": True,
        "weight": 3,
    },
    {
        "engine_id": "engine.subtext.v1",
        "label": "潜台词强制",
        "question": "他没说出口的是什么？抽掉潜台词，这句话还成立吗？",
        "hard": True,
        "weight": 2,
    },
    {
        "engine_id": "engine.cliche_quarantine.v1",
        "label": "反套路隔离",
        "question": "这一部用了哪些被点名的套路？上一部用过的是不是又用了？",
        "hard": True,
        "weight": 2,
    },
    {
        "engine_id": "engine.diversity_quota.v1",
        "label": "多样性配额",
        "question": "运镜、光线、景别的分布有没有挤在一种上？",
        "hard": True,
        "weight": 2,
    },
    {
        "engine_id": "engine.unpredictability.v1",
        "label": "不可预测性",
        "question": "观众能不能猜到下一个动作或下一句话？能猜到就是废的。",
        "hard": False,
        "weight": 1,
    },
)


__all__ = [
    "TASTE_KB_SCHEMA",
    "AESTHETIC_PROFILE_FIELDS",
    "BANNED_CLICHES",
    "QUOTAS",
    "QUOTA_NOTES",
    "PAIRED_SAMPLES",
    "ENGINE_SPECS",
]
