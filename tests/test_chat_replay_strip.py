import time

from novelvideo.chat import service as chat_service


def test_suppresses_partial_labeled_transcript_replay_before_current_prompt():
    replay = "User: 之前的问题\nAssistant: 之前的回答\nUser: 另一条旧问题"

    assert (
        chat_service._strip_replayed_chat_response(
            replay,
            previous_assistant=[],
            current_prompt="现在的问题",
            suppress_partial_replay=True,
        )
        == ""
    )


def test_keeps_reply_after_current_prompt_in_replayed_transcript():
    replay = (
        "User: 之前的问题\n"
        "Assistant: 之前的回答\n"
        "User: 现在的问题\n"
        "Assistant: 这是新的回复"
    )

    assert (
        chat_service._strip_replayed_chat_response(
            replay,
            previous_assistant=[],
            current_prompt="现在的问题",
            suppress_partial_replay=True,
        )
        == "这是新的回复"
    )


def test_final_replay_strip_still_returns_unlabeled_content():
    assert (
        chat_service._strip_replayed_chat_response(
            "正常的新回复",
            previous_assistant=[],
            current_prompt="现在的问题",
        )
        == "正常的新回复"
    )


def test_strips_unlabeled_assistant_history_sequence_before_new_reply():
    previous = [
        "你好！有什么我可以帮你的吗？",
        "你好！我是 Hermes Agent，你的 AI 助手。",
    ]
    replay = "".join(previous) + "当前任务失败了，我建议先重试脚本生成。"

    assert (
        chat_service._strip_replayed_chat_response(
            replay,
            previous_assistant=previous,
            current_prompt="继续",
            suppress_partial_replay=True,
        )
        == "当前任务失败了，我建议先重试脚本生成。"
    )


def test_keeps_complete_repeated_short_reply_on_final_strip():
    assert (
        chat_service._strip_replayed_chat_response(
            "你好！有什么可以帮你？",
            previous_assistant=["你好！有什么可以帮你？"],
            current_prompt="你好",
            suppress_partial_replay=False,
        )
        == "你好！有什么可以帮你？"
    )


def test_suppresses_complete_repeated_short_reply_during_streaming():
    assert (
        chat_service._strip_replayed_chat_response(
            "你好！有什么可以帮你？",
            previous_assistant=["你好！有什么可以帮你？"],
            current_prompt="你好",
            suppress_partial_replay=True,
        )
        == ""
    )


def test_final_strip_drops_fully_replayed_long_response():
    previous = "已经处理完成。" * 30
    assert (
        chat_service._strip_replayed_chat_response(
            previous,
            previous_assistant=[previous],
            current_prompt="进度怎么样？",
            suppress_partial_replay=False,
        )
        == ""
    )


def test_hides_internal_skill_tool_events_from_chat_cards():
    assert chat_service._is_hidden_chat_tool_event(
        "skill",
        "→ skill view (dramaclaw)\n内容: Loading skill 'dramaclaw'",
    )
    assert not chat_service._is_hidden_chat_tool_event(
        "dramaclaw_pipeline_status",
        "→ dramaclaw_pipeline_status\ncompleted",
    )


def test_strips_real_replay_with_markdown_table_shape_drift_and_infrastructure_tail():
    previous = [
        """目标明确：新建可生成图节点，写入韩国九头身美女提示词。先落结构，不启动付费生图。

节点已搭好（**L1 结构落地**）。

### 结果
| 项 | 值 |
|---|
| 节点 | `agent-cb021f8e737d-1` |
| 名称 | 韩国九头身美女 |
| 类型 | 生图提示词节点 |
| revision | 2 |
| 耗时 | 37ms |
| 状态 | 已落库，**尚未生图** |

### 已写入提示词要点
- 韩系九头身模特比例、瓷白肤色、双眼皮与自然皮肤纹理
- 全身体、竖构图 9:16、Vogue Korea 编辑风
- 奶油色丝质上衣 + 高腰炭灰色长裤、柔光棚拍

要我直接提案生图（模型/参数会先给你确认），还是先改妆造、景别或服装？""",
        """记下这条偏好。

已记住。

以后你说「搭节点 / 建节点」这类结构请求，我会直接创建并写好可用提示词，不再追问要不要改妆造、景别或是否提案生图。

付费媒体生成仍会走系统确认门，其余结构操作默认直接落地。""",
        """明白。

流程固定成：先直接搭好 → 搭完再问你下一步；中间有需求你主动说就行。""",
    ]
    replay = (
        previous[0].replace("|---|", "|---|---|").replace("。\n\n节点", "。节点")
        + previous[1].replace("。\n\n已记住", "。已记住")
        + previous[2]
        + "API call failed after 3 retries: HTTP 503: Service temporarily unavailable"
    )

    stripped = chat_service._strip_replayed_chat_response(
        replay,
        previous_assistant=previous,
        current_prompt="节点搭好了吗？",
    )

    assert stripped == "API call failed after 3 retries: HTTP 503: Service temporarily unavailable"
    assert chat_service._is_infrastructure_error_message(stripped)


def test_completion_does_not_replace_valid_delta_with_infrastructure_error():
    assert (
        chat_service._completion_text_or_existing(
            "API call failed after 3 retries: HTTP 503: Service temporarily unavailable",
            "节点已经搭好。",
        )
        == "节点已经搭好。"
    )


def test_empty_village_completion_explains_upstream_overload_without_fake_success():
    message = chat_service._village_empty_completion_message(
        "API call failed after 3 retries: HTTP 503: system memory overloaded",
        had_effect=False,
    )

    assert "上游负载过高" in message
    assert "本轮尚未开始画布执行" in message
    assert "可直接重试" in message


def test_empty_village_completion_preserves_completed_tool_receipts():
    message = chat_service._village_empty_completion_message(
        "HTTP 500: openai_error",
        had_effect=True,
    )

    assert "HTTP 500" in message
    assert "已经落盘的工具回执已保留" in message
    assert "避免重复执行" in message


def test_replay_matching_preserves_semantic_operators():
    assert chat_service._strip_replayed_assistant_prefix(
        "x > y，因此条件成立。",
        ["x < y"],
    ) == "x > y，因此条件成立。"
    assert chat_service._strip_replayed_assistant_prefix(
        "a - b 是本轮的新结论。",
        ["a + b"],
    ) == "a - b 是本轮的新结论。"


def test_infrastructure_error_filter_keeps_normal_explanation():
    explanation = (
        "上游返回 Service temporarily unavailable，"
        "含义是当前服务无可用容量，请稍后重试。"
    )
    assert chat_service._strip_infrastructure_error_tail(explanation) == explanation
    assert not chat_service._is_infrastructure_error_message(explanation)
    assert (
        chat_service._strip_infrastructure_error_tail(
            "节点已完成。\nAPI call failed after 3 retries: HTTP 503"
        )
        == "节点已完成。"
    )


def test_prepared_replay_candidates_keep_stream_delta_filter_bounded():
    previous = [
        f"历史回答{index:02d}-" + ("剧情节点和镜头语言" * 50)
        for index in range(20)
    ]
    content = "这是一条全新的回复-" + ("新的内容" * 100)
    prepared = chat_service._prepare_replay_prefix_candidates(previous)

    started = time.perf_counter()
    for _ in range(100):
        assert chat_service._strip_replayed_chat_response(
            content,
            previous,
            "继续",
            suppress_partial_replay=True,
            prepared_candidates=prepared,
        ) == content
    elapsed = time.perf_counter() - started

    assert len(prepared) == len(previous)
    assert elapsed < 1.5
