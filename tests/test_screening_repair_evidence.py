from novelvideo.production.screening_repair import build_screening_feedback, plan_screening_repairs


def test_repair_preserves_observed_problem_and_exact_timestamp():
    feedback = build_screening_feedback(screening_id='test', issues=[{'category': 'continuity', 'timestamp_seconds': 0, 'description': '开场道具不符', 'audience_effect': '身份无法辨认', 'localized': True}])
    action = plan_screening_repairs(feedback)['actions'][0]
    assert action['timestamp_seconds'] == 0
    assert action['description'] == '开场道具不符'
    assert action['audience_effect'] == '身份无法辨认'
    assert action['requires_shot_mapping'] is True
    assert action['shot_id'] == ''


def test_identified_shot_can_be_targeted_without_losing_evidence():
    feedback = build_screening_feedback(screening_id='test', issues=[{'category': 'prop_state', 'timestamp': 31.3, 'shot_id': 'shot_stable', 'description': '滑板外观改变', 'severity': 'high'}])
    action = plan_screening_repairs(feedback)['actions'][0]
    assert action['shot_id'] == 'shot_stable'
    assert action['requires_shot_mapping'] is False
    assert action['timestamp_seconds'] == 31.3
    assert action['description'] == '滑板外观改变'
