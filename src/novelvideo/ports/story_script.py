"""Transport-neutral structured story-script contracts.

These models are shared by the Freezone generator and the HTTP schema layer;
the generator must not import API modules just to configure an Agent output.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

ScriptContentIntent = Literal[
    "action", "narrative", "product", "dialogue", "lyrical", "documentary", "tutorial", "other"
]


class FreezoneStoryKeyframePlan(BaseModel):
    """一个生成段内有独立参考职责的单幅画面，不是额外视频节点。"""

    role: Literal["action_state", "contact_state", "ending_state", "spatial_reveal", "detail_view", "other"] = Field(
        default="action_state", description="画面职责：动作、接触、结束状态、空间揭示或局部细节"
    )
    generation_strategy: Literal["", "independent", "state_edit"] = Field(
        default="", description="independent用资产组织独立构图；state_edit参考首图改变动作状态；历史缺省保持state_edit"
    )
    framing: str = Field(default="", max_length=1000, description="本张景别、视点、取景与主体关系；independent必填，不复制首图机位")
    state: str = Field(max_length=1000, description="这一张画面要冻结的可见状态")
    purpose: str = Field(default="", max_length=600, description="相比首图与其他计划图新增的可见信息，不能只写锁定状态")
    required: bool = Field(default=False, description="缺少本图会损害动作、空间或关键信息的表达")


class FreezoneStoryScriptRow(BaseModel):
    # Identity fields are assigned after the model returns. They are deliberately
    # omitted from the structured-output schema so the LLM cannot invent or reuse
    # production identities.
    shot_id: Annotated[str, SkipJsonSchema()] = Field(
        default="", description="项目内稳定镜头身份，由服务端分配"
    )
    shot_order: Annotated[int, SkipJsonSchema()] = Field(
        default=0, ge=0, description="当前片序，由服务端分配"
    )
    display_shot_no: Annotated[str, SkipJsonSchema()] = Field(
        default="", description="用户看到的镜号，允许独立修改"
    )
    shot_no: int = Field(description="镜号")
    sequence_ids: list[str] = Field(default_factory=list, max_length=100, description="本镜所属导演段落编号；与对应sequence.shot_nos双向一致，允许交叉归属")
    duration: float = Field(gt=0, allow_inf_nan=False, description="时长，单位秒，可为小数")
    duration_policy: Literal["", "single_action_line", "multi_beat", "fixed_timing"] = Field(default="", description="内容类型：单动作线、多节拍或用户/叙事固定时长；均按完整表演所需时间选择，不默认最短或模型上限")
    duration_reason: str = Field(default="", max_length=1000, description="时长依据：动作、对白、观察或停留的时间安排，何时完成及为何此时切镜；不是模型时长档位")
    visual_description: str = Field(description="画面描述")
    shot_purpose: str = Field(default="", max_length=600, description="本镜让观众知道或感受到什么，以及选择景别和视点的理由")
    film_language: str = Field(default="", max_length=1000, description="按表达目的组合的叙事、摄影、调度、剪辑、美术或声音手法；开放文本，不限于预设类别")
    cut_reason: str = Field(default="", max_length=600, description="本镜结束为何切向下一镜，下一镜带来什么信息；有意省略、换视点或跳切须说明")
    start_state: str = Field(default="", max_length=1000, description="本镜首帧可见状态：主体位置、姿态、运动方向、动作阶段及关键道具状态")
    end_state: str = Field(default="", max_length=1000, description="本镜切点可见状态；只描述结束瞬间，不重复整镜剧情")
    content_intent: ScriptContentIntent = Field(
        default="other",
        description="结合用户意图识别本场内容：动作、叙事、产品、对白、抒情、纪实、教程或其它；不按动作词猜类型",
    )
    character_1: str = Field(default="", description="角色1")
    character_description_1: str = Field(default="", description="角色描述1")
    character_image_1: str = Field(default="", description="角色图1，由后端回填")
    character_2: str = Field(default="", description="角色2")
    character_description_2: str = Field(default="", description="角色描述2")
    character_image_2: str = Field(default="", description="角色图2，由后端回填")
    character_state_start: dict[str, Annotated[str, Field(max_length=1000)]] = Field(default_factory=dict, max_length=2, description="本镜角色名到起始服装、装备及持久外观状态的映射；无人物为空。同场未变化时逐字继承前镜结束状态，不含姿态或情绪")
    character_state_end: dict[str, Annotated[str, Field(max_length=1000)]] = Field(default_factory=dict, max_length=2, description="角色名到结束服装、装备及持久外观状态的映射；不变逐字照抄起始，有变化须在实际动作中交代")
    reference: str = Field(default="", description="关键帧参考图，由后端回填")
    keyframe_index: int = Field(default=0, ge=0, description="对应视频关键帧的 1-based 序号")
    shot: str = Field(default="", description="景别")
    character_action: str = Field(default="", description="角色动作")
    emotion: str = Field(default="", description="情绪")
    scene_tags: str = Field(default="", description="场景标签")
    scene_descriptions: dict[str, Annotated[str, Field(max_length=1600)]] = Field(
        default_factory=dict, max_length=30,
        description="场景名到可复用空间设定的映射；明确布局、出入口、地标、尺度、固定陈设与材质，不写人物或单镜动作",
    )
    prop_descriptions: dict[str, Annotated[str, Field(max_length=1600)]] = Field(
        default_factory=dict, max_length=30,
        description="道具名到基准外观设定的映射；明确形状、尺度、材质、配色、连接结构和操作部位，不混入本镜暂态",
    )
    prop_tags: str = Field(
        default="",
        description="道具标签，顿号分隔；跨镜反复出现的关键道具写在这里，没有写「无」",
    )
    prop_state_start: str = Field(default="", description="本镜开始时关键道具状态")
    prop_state_end: str = Field(default="", description="本镜结束时关键道具状态")
    prop_state_change: str = Field(default="", description="本镜关键道具发生的状态变化")
    lighting_mood: str = Field(default="", description="光影氛围")
    sound: str = Field(default="", description="音效")
    dialogue: str = Field(default="", description="对白")
    shot_prompt: str = Field(default="", description="分镜提示词")
    video_motion_prompt: str = Field(default="", description="视频运动提示词")
    transition_plan: str = Field(
        default="", description="通向下一镜的衔接计划，开放文本；continuous_action 表示明确动作接续。首尾帧生成能力另外用 generation_mode 声明"
    )
    generation_mode: str = Field(
        default="", description="推荐视频生成方式：all_reference优先；特殊需要按模型能力选择image_to_video / first_last_frame / text_to_video，推荐不证明模型支持"
    )
    reference_requirements: str = Field(
        default="", description="本镜生成前需要的角色、场景、尾帧等参考"
    )
    keyframe_plan: list[FreezoneStoryKeyframePlan] = Field(
        default_factory=list,
        max_length=4,
        description="本生成段内可选的关键画面计划，按独立构图或动作改图分工；已有参考足够时为空，不增加视频节点",
    )


class FreezoneStorySequencePlan(BaseModel):
    sequence_id: str = Field(default="", max_length=100, description="序列编号")
    title: str = Field(default="", max_length=300, description="序列标题")
    dramatic_goal: str = Field(default="", max_length=1200, description="这段戏让观众期待什么")
    resistance: str = Field(default="", max_length=1200, description="阻力或未解决的问题")
    escalation: str = Field(default="", max_length=1200, description="冲突如何升级；无冲突作品写表达如何发展")
    turn: str = Field(default="", max_length=1200, description="信息、关系或行动的转折")
    release: str = Field(default="", max_length=1200, description="段尾释放、悬念或余波")
    staging_plan: str = Field(default="", max_length=1200, description="整段空间调度：地标、人物相对位置、移动路线、视线及视点变化；有意空间跳跃说明理由")
    performance_plan: str = Field(default="", max_length=1200, description="表演推进：角色注意、判断、选择与行动，以及受影响者可见的反应；若策略改变写前后行为差异，不强制人物成长；无角色作品写主体展示过程")
    shot_nos: list[int] = Field(default_factory=list, max_length=300, description="覆盖的镜号")


class FreezoneStoryVisualPlan(BaseModel):
    visual_style: str = Field(default="", max_length=1200, description="全片可见美术基准：画风媒介、轮廓、细节处理及稳定色彩语言，不只写电影感")
    texture: str = Field(default="", max_length=1200, description="材质响应、纹理与笔触规则；去噪保留真实或风格化材料细节")
    color_progression: str = Field(default="", max_length=1200, description="色彩随故事的变化")
    lighting: str = Field(default="", max_length=1200, description="主光方向、光源及变化理由")
    camera_language: str = Field(default="", max_length=1200, description="服务叙事的视点、构图与运动习惯及适用理由；实际起点、路径、速度、揭示与结束构图逐镜展开")


class FreezoneStoryDirectorPlan(BaseModel):
    """Shared creative intent, not a claim of verified film quality."""

    target_duration_seconds: Annotated[float | None, SkipJsonSchema()] = Field(default=None, gt=0, allow_inf_nan=False, description="由用户明确整片时长要求提取，不由模型创作假设填写")

    story_promise: str = Field(default="", max_length=1200, description="向观众承诺的体验")
    protagonist_goal: str = Field(default="", max_length=1200, description="主角目标；无主角作品说明表达主体")
    core_conflict: str = Field(default="", max_length=1200, description="核心冲突；无冲突作品说明表达关系")
    ending_change: str = Field(default="", max_length=1200, description="结尾相对开头的可见变化；可以是人物自身、他人、关系或环境变化，人物不变或开放结局说明其结果，不硬加成长或胜利")
    visual_bible: FreezoneStoryVisualPlan = Field(default_factory=FreezoneStoryVisualPlan)
    rhythm_curve: str = Field(default="", max_length=2000, description="节奏和情绪的发展，不套固定公式")
    sound_plan: str = Field(default="", max_length=2000, description="声音视点、环境声、音乐、静默与声音桥的全片规划")
    assumptions: list[str] = Field(default_factory=list, max_length=30, description="用户未指定时采用的创作假设，不能伪装成用户要求")
    sequences: list[FreezoneStorySequencePlan] = Field(default_factory=list, max_length=100)


class FreezoneStoryScriptGenerateData(BaseModel):
    title: str = Field(default="", description="故事脚本标题")
    director_plan: FreezoneStoryDirectorPlan = Field(default_factory=FreezoneStoryDirectorPlan)
    rows: list[FreezoneStoryScriptRow] = Field(
        min_length=1, description="结构化故事脚本行"
    )


__all__ = ["FreezoneStoryKeyframePlan", "FreezoneStoryScriptGenerateData", "FreezoneStoryScriptRow"]
