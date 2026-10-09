from novelvideo.chat.memory_compiler import MEMORY_SCHEMA_VERSION, compile_memory


def test_compiles_conversational_feedback_into_agent_ready_preference():
    result = compile_memory(
        "用户长期要求：觉得每次你跟我说话废话特多，你能说人话吗？永久记住这点"
    )

    assert result.decision == "add"
    assert result.kind == "preference"
    assert result.memory_key == "user.communication_style"
    assert "结论先行" in result.content
    assert "废话特多" not in result.content
    assert result.metadata()["memory_schema"] == MEMORY_SCHEMA_VERSION

    detailed = compile_memory("你每次说得特别特别详细，其实只需要说主要的东西就行")
    assert detailed.decision == "add"
    assert detailed.memory_key == "user.communication_style"


def test_rejects_feedback_without_a_resolvable_subject():
    result = compile_memory("以后不能再犯这种低级错误")

    assert result.decision == "noop"
    assert result.reason == "unresolved_reference"
    assert not result.content

    malformed = compile_memory("不能再这个犯这种低级错误了")
    assert malformed.decision == "noop"
    assert malformed.reason == "unresolved_reference"
    prohibited = compile_memory("禁止再犯这种低级错误了")
    assert prohibited.decision == "noop"


def test_merges_premature_execution_feedback_under_a_stable_key():
    first = compile_memory(
        "每次对话都为了单子的快速生成被冲动绑架，什么都没有问就开始搭节点"
    )
    second = compile_memory(
        "每次跟你交流时你特别急躁，什么都没问就直接开始搭建节点"
    )

    assert first.decision == second.decision == "add"
    assert first.memory_key == second.memory_key == "canvas.execution_readiness"
    assert first.content == second.content
    assert "信息充分时直接执行" in first.content
    assert "只询问一个" in first.content


def test_compiles_verified_experience_as_professional_memory():
    result = compile_memory(
        "这个动作方案更稳定，建议先固定角色身份再拆镜头",
        kind_hint="candidate_experience",
        scope_kind="professional",
        task_stage="creative_planning",
    )

    assert result.decision == "add"
    assert result.kind == "candidate_experience"
    assert result.scope_kind == "professional"
    assert result.applies_when == {"task_stage": "creative_planning"}
    assert result.content == "建议先固定角色身份再拆镜头。"


def test_keeps_clear_cross_project_rule_without_chat_wrapper():
    result = compile_memory("以后所有项目都要优先保证角色连续性")

    assert result.decision == "add"
    assert result.kind == "learned_rule"
    assert result.content == "所有项目都要优先保证角色连续性。"
    assert "以后" not in result.content


def test_small_talk_never_becomes_memory():
    result = compile_memory("嗨")

    assert result.decision == "noop"
    assert result.reason == "small_talk"


def test_task_request_and_vague_learning_are_not_promoted():
    task = compile_memory(
        "不要提交任何图片或视频生成任务，创建 1 个项目目标文字节点"
    )
    vague = compile_memory("再教给你一个知识")

    assert task.decision == "noop"
    assert task.reason == "task_request"
    assert vague.decision == "noop"
    assert vague.reason == "vague_learning"


def test_unresolved_pronoun_and_old_execution_rule_are_handled():
    pronoun = compile_memory("他就应该是9:16")
    execution = compile_memory(
        "先判断创作需求是否具备可执行条件：信息充分时直接执行；"
        "缺失信息会改变作品方向时；只询问一个影响最大的条件。"
    )

    assert pronoun.decision == "noop"
    assert pronoun.reason == "unresolved_reference"
    assert execution.memory_key == "canvas.execution_readiness"
    assert "信息充分时直接执行" in execution.content
    assert "只询问一个影响最大的条件" in execution.content


def test_compiles_character_sheet_layout():
    result = compile_memory(
        "保持图中角色外观、比例、颜色和细节一致，角色设定表布局左侧三分之一放"
        "头顶到胸口的角色正面大头照，使用浅灰背景，避免元素拥挤"
    )

    assert result.decision == "add"
    assert result.memory_key == "creative.character_sheet_layout"
    assert result.content == (
        "制作角色设定表时，保持角色外观、比例、颜色和细节一致；"
        "左侧三分之一放置头顶至胸口的角色正面大头照；使用浅灰背景并避免元素拥挤。"
    )
    assert result.action == ("保持角色视觉身份一致", "使用清晰的角色设定表布局")
    assert result.avoid == ("角色元素拥挤", "外观、比例、颜色或细节漂移")


def test_compiles_node_parameter_contract():
    result = compile_memory("执行画布任务时必须考虑好节点的参数")

    assert result.decision == "add"
    assert result.memory_key == "canvas.model_parameter_contract"
    assert result.content == (
        "执行节点前核对所选模型的能力合同与节点参数；"
        "只提交该模型支持的画幅、分辨率、时长、声音和参考素材数量。"
    )
    assert result.action == ("先读取所选模型能力", "按能力合同填写节点参数")
    assert result.avoid == ("提交模型不支持的参数", "用隐藏默认值覆盖用户配置")
    metadata = result.metadata()
    assert metadata["memory_schema"] == "xiaoshu.memory.v3"
    assert metadata["rule_type"] == "prompt_convention"
    assert metadata["executable"] is False


def test_compiles_duration_and_reference_feedback_as_preview_candidates():
    duration = compile_memory("你每次视频提示词不要写时长秒数")
    assert duration.decision == "add"
    assert duration.kind == "candidate_experience"
    assert duration.rule_type == "prompt_convention"
    assert duration.hook_id == "video_prompt.remove_redundant_duration"
    assert duration.hook_mode == "preview_only"
    assert duration.confidence == 0.35

    reference = compile_memory("你每次都把姥爷跟姥姥的图片引用反了")
    assert reference.decision == "add"
    assert reference.kind == "candidate_experience"
    assert reference.rule_type == "reference_consistency"
    assert reference.hook_id == "reference.require_explicit_mapping"


def test_unresolved_low_level_error_is_not_a_rule():
    result = compile_memory("禁止再犯这种低级错误了哟；你都应该注意这一点")
    assert result.decision == "noop"
    assert result.reason == "unresolved_reference"


def test_cross_project_teaching_preserves_conditional_reference_branches():
    marker = "ZC_MEM_EVAL_20260827"
    result = compile_memory(
        f"记住这条跨项目规则：当任务包含 {marker} 并创建咖啡包装概念时，"
        "必须先检查当前画布；如果存在名为‘咖啡品牌主Logo’的节点，"
        "必须复用并连接到新包装节点；"
        f"新节点提示词必须包含 [MEMORY_APPLIED:{marker}]；"
        "如果只有无关资产，禁止强行连线；禁止启动任何媒体任务。",
        kind_hint="candidate_experience",
        scope_kind="professional",
        task_stage="simple_canvas",
    )

    assert result.decision == "add"
    assert "如果存在名为‘咖啡品牌主Logo’的节点，必须复用并连接" in result.content
    assert "如果只有无关资产，禁止强行连线" in result.content
    assert "禁止启动任何媒体任务" in result.content
    assert result.avoid == (
        "如果只有无关资产，禁止强行连线",
        "禁止启动任何媒体任务",
    )
