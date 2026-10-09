from novelvideo.director_rhythm import apply_director_rhythm_plan, plan_director_rhythm


def test_director_rhythm_assigns_diverse_editorial_durations():
    beats = [
        {"beat_number": 1, "visual_description": "雨夜城市的全景，街道空无一人。", "narration_segment": "雨一直下。"},
        {"beat_number": 2, "visual_description": "特写：他握紧带血的钥匙。", "narration_segment": "钥匙还带着余温。"},
        {"beat_number": 3, "visual_description": "她愣住，眼神震惊。", "narration_segment": "她终于明白了。"},
        {"beat_number": 4, "audio_type": "dialogue", "visual_description": "两人隔着门对话。", "narration_segment": "你现在还要进去吗？"},
    ]

    plans = apply_director_rhythm_plan(beats)

    assert [plan.narrative_function for plan in plans if plan] == [
        "establishing",
        "insert",
        "reaction",
        "dialogue",
    ]
    targets = [beat["target_duration_seconds"] for beat in beats]
    assert len(set(targets)) >= 3
    assert targets[0] > targets[1]
    assert all(beat["generation_duration_seconds"] >= beat["target_duration_seconds"] for beat in beats)
    assert all(beat["trim_out_seconds"] - beat["trim_in_seconds"] <= beat["target_duration_seconds"] + 0.11 for beat in beats)


def test_director_rhythm_keeps_manual_shot_duration_untouched():
    beat = {
        "beat_number": 7,
        "is_manual_shot": True,
        "duration_seconds": 3.0,
        "visual_description": "手工插入的镜头",
        "narration_segment": "",
    }

    assert apply_director_rhythm_plan([beat]) == [None]
    assert beat["duration_seconds"] == 3.0
    assert "target_duration_seconds" not in beat


def test_director_rhythm_long_dialogue_has_room_for_spoken_text():
    beat = {
        "beat_number": 11,
        "audio_type": "dialogue",
        "visual_description": "两人面对面对话。",
        "narration_segment": "我知道这件事会改变我们所有人的命运，但现在已经没有回头路了。",
    }

    plan = plan_director_rhythm(beat)

    assert plan.narrative_function == "dialogue"
    assert plan.target_duration_seconds >= 6.0
    assert plan.generation_duration_seconds >= plan.target_duration_seconds


def test_director_rhythm_uses_the_selected_model_ceiling_for_long_takes():
    beat = {
        "beat_number": 12,
        "audio_type": "dialogue",
        "visual_description": "一镜到底，人物穿过站台、上车并完成最后的告别。",
        "narration_segment": "这段台词需要完整保留。" * 12,
    }

    plan = plan_director_rhythm(beat, generation_bounds=(4, 30))

    assert plan.target_duration_seconds > 15.0
    assert plan.target_duration_seconds <= 30.0
    assert plan.generation_duration_seconds <= 30.0
    assert plan.generation_duration_seconds >= plan.target_duration_seconds


def test_director_rhythm_keeps_four_second_source_floor_for_short_cut():
    plan = plan_director_rhythm(
        {
            "beat_number": 13,
            "visual_description": "特写：钥匙落在桌面上。",
            "narration_segment": "咔哒。",
        },
        generation_bounds=(4, 30),
    )

    assert plan.target_duration_seconds < 4.0
    assert plan.generation_duration_seconds == 4.0


def test_director_rhythm_classifies_fight_progression_as_action():
    beats = [
        {"beat_number": 1, "visual_description": "反派持续猛攻，男主不断后退。"},
        {"beat_number": 2, "visual_description": "男主闪避重拳，反手肘击反派。"},
        {"beat_number": 3, "visual_description": "男主飞踢，反派向后倒飞撞上墙面。"},
    ]

    plans = apply_director_rhythm_plan(beats)

    assert [plan.narrative_function for plan in plans if plan] == [
        "action",
        "action",
        "action",
    ]


def test_silent_visual_beat_never_defaults_to_dialogue():
    action = plan_director_rhythm(
        {"beat_number": 1, "audio_type": "silence", "visual_description": "人物走过积水。"}
    )
    hold = plan_director_rhythm(
        {
            "beat_number": 2,
            "audio_type": "silence",
            "visual_description": "男主喘着粗气，居高临下俯视倒地的反派。",
        }
    )

    assert action.narrative_function == "action"
    assert hold.narrative_function == "emotional_hold"


def test_director_rhythm_fits_automatic_cuts_to_episode_target() -> None:
    beats = [
        {
            "beat_number": index,
            "visual_description": "人物完成一个明确动作。",
            "narration_segment": "",
        }
        for index in range(1, 19)
    ]

    apply_director_rhythm_plan(beats, target_duration=90)

    assert abs(sum(beat["target_duration_seconds"] for beat in beats) - 90) <= 1.0
    assert all(
        beat["generation_duration_seconds"] >= beat["target_duration_seconds"]
        for beat in beats
    )


def test_final_and_generation_duration_are_separate_for_automatic_beat():
    from novelvideo.manual_shots import (
        resolve_composition_window,
        resolve_final_video_duration,
        resolve_generation_video_duration,
    )

    beat = {
        "target_duration_seconds": 1.8,
        "generation_duration_seconds": 4.0,
        "trim_in_seconds": 0.3,
        "trim_out_seconds": 2.1,
    }

    assert resolve_final_video_duration(beat) == 1.8
    assert resolve_generation_video_duration(beat) == 4.0
    # A real narration duration supersedes the optimistic planning estimate,
    # while the generation request remains a source-material duration.
    assert resolve_final_video_duration(beat, audio_duration=3.2) == 3.2
    assert resolve_generation_video_duration(beat, audio_duration=3.2) == 4.0
    assert resolve_composition_window(beat) == (0.3, 1.8)
    assert resolve_composition_window(beat, audio_duration=3.2) == (0.3, 3.2)


async def test_director_rhythm_fields_round_trip_through_sqlite(tmp_path):
    from novelvideo.models import NovelEpisode, NovelVisualBeat
    from novelvideo.sqlite_store import SQLiteStore

    project_dir = tmp_path / "user" / "project"
    project_dir.mkdir(parents=True)
    store = SQLiteStore("user/project", output_dir=str(project_dir), state_dir=str(project_dir))
    await store._ensure_db()
    await store.add_episodes([NovelEpisode(number=1, title="第一集")])
    await store.add_visual_beats(
        [
            NovelVisualBeat(
                beat_number=1,
                episode_number=1,
                narration="她停在门前。",
                visual_description="特写：她握紧钥匙。",
                narrative_function="insert",
                pace="fast",
                cut_reason="以细节承载关键信息。",
                target_duration_seconds=1.8,
                generation_duration_seconds=4.0,
                trim_in_seconds=0.4,
                trim_out_seconds=2.2,
            )
        ]
    )

    beat = (await store.get_beats_as_dicts(1))[0]

    assert beat["narrative_function"] == "insert"
    assert beat["pace"] == "fast"
    assert beat["target_duration_seconds"] == 1.8
    assert beat["generation_duration_seconds"] == 4.0
    assert beat["trim_in_seconds"] == 0.4
    assert beat["trim_out_seconds"] == 2.2


async def test_legacy_beat_receives_director_rhythm_when_read(tmp_path):
    from novelvideo.models import NovelEpisode, NovelVisualBeat
    from novelvideo.sqlite_store import SQLiteStore

    project_dir = tmp_path / "legacy-user" / "legacy-project"
    project_dir.mkdir(parents=True)
    store = SQLiteStore(
        "legacy-user/legacy-project",
        output_dir=str(project_dir),
        state_dir=str(project_dir),
    )
    await store._ensure_db()
    await store.add_episodes([NovelEpisode(number=1, title="旧项目")])
    await store.add_visual_beats(
        [
            NovelVisualBeat(
                beat_number=1,
                episode_number=1,
                narration="她停在门前，迟迟没有推开。",
                visual_description="特写：她握紧钥匙，眼神犹豫。",
            )
        ]
    )

    beat = (await store.get_beats_as_dicts(1))[0]

    assert beat["narrative_function"] == "insert"
    assert beat["target_duration_seconds"] != 5.0
    assert beat["generation_duration_seconds"] >= beat["target_duration_seconds"]
