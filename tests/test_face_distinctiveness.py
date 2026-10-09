"""同龄同性别的角色必须有各自独有的可见辨识标记。

孟叔和老兰都是「四十多岁、黑色短发、圆脸」，基准图被画成同一个人。
提取后要能把这种撞脸查出来，逼着重写面部描述。
"""

from novelvideo.cognee.pipeline import face_distinctiveness_collisions
from novelvideo.models import NovelCharacter


def _char(name, gender, age, face):
    return NovelCharacter(name=name, gender=gender, age_group=age, face_prompt=face)


def test_same_age_gender_without_unique_markers_collides():
    characters = [
        _char("孟叔", "男", "middle", "男性，四十岁左右，黑色短发，眼神精明，微圆的脸型"),
        _char("老兰", "男", "middle", "男性，四十多岁，黑色整齐短发，面容饱满，微胖的方圆脸"),
    ]
    assert face_distinctiveness_collisions(characters) == [("孟叔", "老兰")]


def test_distinct_markers_pass():
    characters = [
        _char("孟叔", "男", "middle", "男性，四十五岁，瘦削长脸，下颌有短硬胡茬，黑色短发"),
        _char("老兰", "男", "middle", "男性，五十岁，富态宽脸，双下巴，花白头发，整齐八字胡"),
    ]
    assert face_distinctiveness_collisions(characters) == []


def test_different_age_or_gender_is_not_compared():
    characters = [
        _char("父亲", "男", "middle", "男性，中年，黑色短发，方脸"),
        _char("大哥", "男", "youth", "男性，青年，黑色短发，方脸"),
        _char("母亲", "女", "middle", "女性，中年，黑色长发，圆脸"),
    ]
    assert face_distinctiveness_collisions(characters) == []
