from novelvideo.chat.intent_contract import classify_agent_intent


def test_direct_media_request_is_actionable_even_with_later_prompt_discussion():
    intent = classify_agent_intent("生成一个 30 秒视频镜头并优化提示词")

    assert intent.kind == "media_submission"
    assert intent.action_requested is True
    assert intent.media_submission_requested is True
    assert intent.negated_action is False


def test_discussion_before_action_is_not_a_submission():
    intent = classify_agent_intent("讨论怎么生成视频，不要提交")

    assert intent.kind == "discussion"
    assert intent.action_requested is False
    assert intent.media_submission_requested is False


def test_canvas_creation_is_actionable_but_not_media_submission():
    intent = classify_agent_intent("创建一个角色节点")

    assert intent.kind == "canvas_mutation"
    assert intent.action_requested is True
    assert intent.explicit_creation_requested is True
    assert intent.media_submission_requested is False


def test_negated_creation_discussion_stays_observational():
    intent = classify_agent_intent("不要生成，聊聊怎么创建角色")

    assert intent.kind == "discussion"
    assert intent.action_requested is False
    assert intent.negated_action is True


def test_negative_constraints_do_not_hide_selected_node_optimization():
    intent = classify_agent_intent(
        "优化我眼前选中的镜头，让雨夜氛围更压抑、人物欲言又止。"
        "只修改选中节点，不要创建新节点，不改变连线，不启动媒体任务。"
    )

    assert intent.kind == "canvas_mutation"
    assert intent.action_requested is True
    assert intent.target_mutation_requested is True
    assert intent.explicit_creation_requested is False
    assert intent.negated_action is True


def test_bare_approval_is_authorization_not_observation():
    """用户批准上一轮提议时，整句没有任何动作动词，但必须算授权。

    这类回复原先被判成 observation，回合合同随之冻结为只读，agent 刚要落地的
    写操作被自己人拦下，用户只看到一句「已保留恢复点」——实际什么都没发生。
    """

    for phrase in (
        "可以",
        "行",
        "好的",
        "你看着来就行了",
        "就这么办",
        "按你说的做",
        "照这个来",
        "嗯，你决定",
        "剩下的你决定",
        "没问题",
        "同意",
        "go ahead",
        "OK",
    ):
        intent = classify_agent_intent(phrase)

        assert intent.kind == "user_authorization", phrase
        assert intent.action_requested is False, phrase
        assert "explicit_approval_language" in intent.reason_codes, phrase


def test_approval_detection_does_not_swallow_questions_or_courtesy():
    """授权判定只能在讨论/动作/媒体/续跑都不成立时兜底，不能抢走问句。"""

    for phrase in (
        "介绍一下这个功能",
        "这个方案怎么样",
        "为什么角色会漂移",
        "可以加个节点吗",
        "能改一下吗",
        "这个节点是干什么的",
        "画布上有几个节点",
    ):
        assert classify_agent_intent(phrase).kind != "user_authorization", phrase


for phrase in (
    "谢谢",
    "辛苦了",
    "多谢",
    "辛苦了谢谢",
    "thanks",
    # 2026-09-30 自造缺陷的回归：曾有一条「短句 + 非疑问 + 不提具体对象就当授权」
    # 的兜底，它把这些**新任务请求**全判成了批准。判错的方向是把新请求当批准，
    # 和当天修掉的 agent_turn_contract_read_only 是同一个方向上的错。
    "做一个20秒的企业宣传片",
    "帮我做一集短剧",
    "按刚才说的改",
    "再来一版",
    "看个电影",
):
    assert classify_agent_intent(phrase).kind != "user_authorization", phrase


def test_connect_wording_counts_as_an_action():
    """连线类请求必须算动作，否则整条写侧路线降级成只读再把自己拒掉。

    回归 2026-09-30 真机：用户说「将角色和场景基准资产连线接入分镜1-4」，
    `_ACTION_RE` 里没有「连线/连接/断开/移除」（它们只在 `_MUTATION_RE` 里），
    `action_requested` 判成 False → `expert_arbitration.write_route` 为 False →
    capability 降级成 `canvas.snapshot`、`policy` 变成 "read" →
    写检查点 `require_write_fields=True` 要求 policy=="write" →
    `execution_context_write_policy_missing` → `dynamic_checkpoint_blocked`。
    用户看到的现象是「改已有节点总是失败」，且计划/清单版本全部对得上，
    完全看不出是分类器的问题。
    """

    for phrase in (
        "将角色和场景基准资产连线接入分镜1-4",
        "把这两个节点连起来",
        "断开这两条连线",
        "移除这个节点",
        "把这些图接到分镜上",
    ):
        intent = classify_agent_intent(phrase)

        assert intent.action_requested is True, phrase
        assert intent.target_mutation_requested is True, phrase


def test_connect_wording_change_does_not_swallow_reports_or_questions():
    """收祈使形态是为了覆盖「连起来」，但不能把报错陈述和问句也当成变更请求。"""

    for phrase in (
        "这个方案怎么样",
        "为什么角色会漂移",
        "你能做什么",
        "介绍一下这个功能",
    ):
        assert classify_agent_intent(phrase).action_requested is False, phrase

    # 「网络连接失败」里的「连接」是名词化的故障陈述，不是祈使动作。
    report = classify_agent_intent("网络连接失败帮我看看")

    assert report.action_requested is False


def test_action_verb_families_stay_recognized():
    """守卫：中文动词表会漂，必须盯住这几族。

    2026-09-30 一天之内在同一个文件上踩了三次：动作表漏了「连线/连接/断开/移除」
    （它们只在变更表里），漏了「帮我X/替我X」这个前缀族，漏了「出片」这一族动词形态。
    每次的后果都一样——`action_requested` 为 False，整条写侧路线降级成只读，
    然后在写检查点把自己拒掉，而计划版本与能力清单版本全都对得上，
    从错误信息里完全看不出是分类器的问题。

    中文动宾之间可以插量词与修饰语，靠枚举补总是补不全；这条测试是那道防线。
    新增动词形态时加到这里，而不是等它在真机上再炸一次。
    """

    must_be_actions = (
        # 连线族
        "将角色和场景基准资产连线接入分镜1-4",
        "把这两个节点连起来",
        "断开这两条连线",
        "移除这个节点",
        # 帮我/替我前缀族
        "帮我做一集短剧",
        "帮我写个脚本",
        "帮我建三个节点",
        "替我改一下",
        # 出片族
        "出片",
        "出个成片",
        "出一条短片",
        "把片子出出来",
        # 挂载/装配族（2026-09-30 真机：用户说「可以 挂载一下吧」，
        # 「挂载」不在任何动词表里 → 判成只读 → 执行上下文冻结成 policy='read'
        # → 真到写的时候要求必须是 write → dynamic_checkpoint_blocked。
        # 这是同一个病的第四次发作，每次都只换一个词。）
        "可以 挂载一下吧",
        "挂载这三个资产",
        "把这个挂载上",
        "把资产绑定到分镜",
        "把这些接入分镜",
        # 常规族（回归保护）
        "生成这五张图",
        "删除选中节点",
    )
    for phrase in must_be_actions:
        assert classify_agent_intent(phrase).action_requested is True, phrase

    must_not_be_actions = (
        "出现一个问题",
        "出发前准备好",
        "这个方案怎么样",
        "你能做什么",
        "介绍一下这个功能",
        "为什么角色会漂移",
        "可以加个节点吗",
        "今天天气不错",
        "网络连接失败帮我看看",
    )
    for phrase in must_not_be_actions:
        assert classify_agent_intent(phrase).action_requested is False, phrase
