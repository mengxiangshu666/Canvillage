import pytest

from novelvideo.freezone.script_batching import plan_source_batches


def _source(count: int = 20) -> str:
    return "\n\n".join(f"镜{i}：第{i}镜的事件。" for i in range(1, count + 1))


def test_short_or_unnumbered_source_stays_single_batch():
    assert len(plan_source_batches("一个想法", [])) == 1
    assert len(plan_source_batches(_source(6), list(range(1, 7)))) == 1
    assert len(plan_source_batches("没有镜号的正文", [1, 2, 3])) == 1


def test_long_numbered_source_is_split_without_overlap_or_loss():
    batches = plan_source_batches(_source(20), list(range(1, 21)), max_shots=8)
    assert [batch["shot_numbers"] for batch in batches] == [
        [1, 2, 3, 4, 5, 6, 7, 8],
        [9, 10, 11, 12, 13, 14, 15, 16],
        [17, 18, 19, 20],
    ]
    flattened = [number for batch in batches for number in batch["shot_numbers"]]
    assert flattened == list(range(1, 21))
    assert all(
        batch["source_text"].startswith(f"镜{batch['shot_numbers'][0]}")
        for batch in batches
    )
    assert all(batch["global_context"] for batch in batches)


def test_batching_never_crosses_a_missing_shot_or_splits_one_shot():
    source = "镜1：一。\n\n镜3：三。\n\n" + "\n\n".join(
        f"镜{i}：第{i}镜。" for i in range(4, 22)
    )
    with pytest.raises(ValueError, match="缺少明确镜号"):
        plan_source_batches(source, [1, 2, 3, 4], threshold=4)
    batches = plan_source_batches(
        source, [1, 3] + list(range(4, 22)), threshold=4, max_shots=4
    )
    assert batches[0]["shot_numbers"] == [1]
    assert batches[1]["shot_numbers"] == [3, 4, 5, 6]
    assert "镜3：三。" in batches[1]["source_text"]
