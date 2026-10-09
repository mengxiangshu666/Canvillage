"""Structured twelve-shot fixture for the real one-minute production run."""

from __future__ import annotations

from typing import Any


_STYLE = (
    "写实电影感，低饱和暗红与深灰褐主色，旧相机金属冷银点缀，"
    "35mm 胶片颗粒，浅景深，动机光明确。"
)

_CHARACTER = "[阿木: 五十岁男性，短黑发夹少量灰发，深蓝旧外套，右手虎口有旧疤。]"

_TECHNIQUE = "35mm 胶片质感，浅景深，细颗粒，高光克制，暗部保留纹理。"


def _row(
    base: dict[str, Any],
    *,
    index: int,
    shot: str,
    visual: str,
    action: str,
    composition: str,
    lighting: str,
    technique: str,
    motion: str,
    ambience: str,
    scene: str,
    props: str,
) -> dict[str, Any]:
    row = dict(base)
    row.update(
        {
            "shot_no": index,
            "duration": 5,
            "visual_description": visual,
            "character_1": "阿木",
            "character_description_1": _CHARACTER,
            "scene_tags": scene,
            "prop_tags": props,
            "shot": shot,
            "character_action": action,
            "emotion": "克制、怀念、逐渐释然",
            "lighting_mood": lighting,
            "sound": ambience,
            "dialogue": "无",
            "shot_prompt": (
                f"[画面构图] {composition} + "
                f"[角色卡] {_CHARACTER} + "
                f"[主体/人物空间] {visual} + "
                f"[主体状态] {action} + "
                f"[场景环境] {scene} + "
                f"[光影几何] {lighting} + "
                f"[视觉风格] {_STYLE} + "
                f"[技术参数] {technique}"
            ),
            "video_motion_prompt": (
                f"[运镜轨迹] {motion} + "
                f"[主体动作] {action} + "
                f"[环境动态] {ambience} + "
                f"[音效氛围] {ambience} + "
                "[对话台词] 无对白，不使用旁白。 + "
                "[时长] [时长：5s]"
            ),
        }
    )
    return row


def build_hollywood_60_rows(base: dict[str, Any]) -> list[dict[str, Any]]:
    """Return twelve five-second shots with one continuous visual action line."""

    return [
        _row(
            base,
            index=1,
            shot="特写",
            visual="暗房顶部的红色安全灯轻微晃动，空气中的浮尘被红光切亮，灯罩边缘有旧漆剥落。",
            action="安全灯从静止转为极轻的摆动，尘埃缓慢飘过光束。",
            composition="安全灯位于画面右上，暗部占据大部分画面，用极窄高光勾出灯罩。",
            lighting="唯一动机光是暗红安全灯，光束边缘迅速衰减，背景保留胶片黑位。",
            technique=_TECHNIQUE,
            motion="固定机位，极缓慢推近，保持轴线稳定。",
            ambience="暗房通风机低鸣、远处雨声。",
            scene="旧照相馆暗房",
            props="红色安全灯、浮尘",
        ),
        _row(
            base,
            index=2,
            shot="近景",
            visual="阿木推门进入暗房，肩背形成深色剪影，左手压下门把，右手护着胸前的旧相机。",
            action="阿木放轻脚步进入，回身关门，雨声随门缝收窄而减弱。",
            composition="门框形成内框构图，人物位于左侧三分线，右侧留给暗房纵深。",
            lighting="门缝冷白余光从背后掠过肩线，暗红安全灯从左前方压低面部细节。",
            technique=_TECHNIQUE,
            motion="镜头从门把轻摇到胸口旧相机，再停住。",
            ambience="门轴低响、雨声、脚步与布料摩擦。",
            scene="旧照相馆暗房、木门",
            props="木门、旧相机、深色外套",
        ),
        _row(
            base,
            index=3,
            shot="全景",
            visual="狭长暗房完整展开，木桌、药水槽、晾片绳和金属柜沿纵深排列，阿木站在入口处。",
            action="阿木环视房间，最后把目光停在最深处的木桌上。",
            composition="对称单点透视，门框在后方压出低矮入口，人物作为尺度参照。",
            lighting="顶灯熄灭，仅一盏红灯提供环境底光，地面形成长而窄的反射。",
            technique=_TECHNIQUE,
            motion="缓慢横移半步，随后沿人物视线推向木桌。",
            ambience="通风机、雨点敲窗、空旷房间轻微回声。",
            scene="旧照相馆暗房、木桌、药水槽、晾片绳",
            props="木桌、药水槽、晾片绳、金属柜",
        ),
        _row(
            base,
            index=4,
            shot="中景",
            visual="阿木坐到木桌旁，把旧相机放在桌面中央，指尖在机背停了一下。",
            action="阿木解开外套，把相机稳稳放下，拇指擦过磨损的卷片旋钮。",
            composition="人物右侧三分之一，相机居中偏左，桌面留出操作空间。",
            lighting="红灯从左前侧切过手背与机身，后颈沉入阴影。",
            technique=_TECHNIQUE,
            motion="固定机位，焦点从阿木手背缓慢转移到相机卷片旋钮。",
            ambience="衣物摩擦、相机金属轻碰木桌、雨声。",
            scene="旧照相馆暗房、木桌",
            props="旧相机、木桌、深色外套",
        ),
        _row(
            base,
            index=5,
            shot="特写",
            visual="阿木打开相机后盖，看见里面还卡着最后一段未冲洗的黑白胶片。",
            action="后盖弹开一条细缝，阿木停住呼吸，用指腹托住胶片边缘。",
            composition="机背占据画面中心，手指从下方进入，胶片形成一条窄亮线。",
            lighting="红灯侧照金属接缝，胶片表面只有一道极细反光。",
            technique=_TECHNIQUE,
            motion="微距固定机位，焦点从金属后盖移到胶片边缘。",
            ambience="机背卡扣轻响、极细胶片摩擦声、远处雨声。",
            scene="旧照相馆暗房、木桌",
            props="旧相机、未冲洗胶片",
        ),
        _row(
            base,
            index=6,
            shot="中近景",
            visual="阿木在红灯下把胶片卷进显影罐，动作克制、熟练，额头渗出一层薄汗。",
            action="双手交替旋转罐盖，胶片发出连续而轻的滑动声。",
            composition="双手与显影罐占画面下半，阿木面部位于上方虚焦区。",
            lighting="红灯从右侧勾出指节，面部仅保留轮廓，药水槽反出一道暗高光。",
            technique=_TECHNIQUE,
            motion="镜头随双手动作轻微下沉，不做无动机摇晃。",
            ambience="胶片滑动、罐盖摩擦、药水轻晃、通风机。",
            scene="旧照相馆暗房、药水槽",
            props="显影罐、胶片、药水槽",
        ),
        _row(
            base,
            index=7,
            shot="特写",
            visual="显影液表面出现细小波纹，黑白影像在相纸上从乳白逐渐显出人像轮廓。",
            action="阿木用夹子轻推相纸，影像从灰雾里慢慢浮现。",
            composition="俯拍相纸中心，夹子从画面右下进入，避免遮挡影像。",
            lighting="红灯从水面反射，高光被压成暗红色，液面有细碎银点。",
            technique=_TECHNIQUE,
            motion="固定俯拍，极慢推近至影像轮廓出现。",
            ambience="药液轻晃、夹子碰盘、水滴回落。",
            scene="旧照相馆暗房、显影盘",
            props="显影盘、相纸、夹子、药水",
        ),
        _row(
            base,
            index=8,
            shot="全景",
            visual="阿木俯身盯着显影盘，红色灯光映在眼睛下方，他的手指在盘沿停住。",
            action="阿木屏住呼吸，身体前倾，手指逐渐收紧。",
            composition="人物脸部位于左侧三分线，显影盘在右下前景作为失焦色块。",
            lighting="顶侧红灯形成窄轮廓光，眼部以下保留一点反射。",
            technique=_TECHNIQUE,
            motion="镜头缓慢靠近，最后停在眼睫与盘沿之间。",
            ambience="药液轻响、呼吸克制、通风机低频。",
            scene="旧照相馆暗房、显影盘",
            props="显影盘、相纸、木桌",
        ),
        _row(
            base,
            index=9,
            shot="特写",
            visual="相纸上完全显出母亲年轻时的笑脸，肩上披着一条浅色围巾，背景是虚化的旧街。",
            action="药液轻轻晃动，影像的灰阶继续加深，母亲的笑容变得清晰。",
            composition="相纸填满画面，母亲脸部位于中心偏上，边缘保留湿纸纤维。",
            lighting="红灯均匀扫过相纸，高光柔和，阴影保留丰富层次。",
            technique=_TECHNIQUE,
            motion="固定俯拍，焦点从相纸边缘缓慢拉到母亲的眼睛。",
            ambience="药液轻响、水滴、远处雨声像记忆般拉远。",
            scene="旧照相馆暗房、显影盘",
            props="相纸、显影盘、母亲旧照",
        ),
        _row(
            base,
            index=10,
            shot="近景",
            visual="阿木抬起头，眼眶泛红但没有落泪，红灯把他的脸分成明暗两半。",
            action="阿木呼出一口气，嘴角先绷紧，随后极轻地松动。",
            composition="面部贴近画幅中心，左侧留出暗房空气，右侧被红灯边缘切过。",
            lighting="硬质红灯从右侧切分面部，左脸接近剪影，眼中保留一点高光。",
            technique=_TECHNIQUE,
            motion="固定机位，极慢微推，不切碎表演。",
            ambience="通风机、雨声、远处滴水。",
            scene="旧照相馆暗房",
            props="红色安全灯、木桌",
        ),
        _row(
            base,
            index=11,
            shot="全景",
            visual="阿木把湿相纸举到红灯前，狭长暗房被相纸挡住一小块光，周围家具沉进暗部。",
            action="阿木站直身体，双手举起相纸，让红光从纸张背后穿过。",
            composition="人物居中，相纸形成明亮的视觉焦点，暗房纵深在他身后延展。",
            lighting="后背光与红光共同勾出身形，相纸成为画面最亮区域。",
            technique=_TECHNIQUE,
            motion="镜头从相纸缓慢后拉，露出完整的暗房空间。",
            ambience="雨声、纸张滴水、通风机、远处城市低频。",
            scene="旧照相馆暗房、木桌、晾片绳",
            props="湿相纸、红色安全灯、木桌",
        ),
        _row(
            base,
            index=12,
            shot="远景",
            visual="阿木把相纸挂上晾片绳，退回暗部，只留下照片在红光里轻轻滴水。",
            action="阿木松开夹子，后退一步，轻轻按下旧相机的快门。",
            composition="相纸位于前景右侧，阿木在后方左三分之一处成为低亮度剪影。",
            lighting="相纸承接红光，人物只剩肩线和手部，最后随快门声压暗。",
            technique=_TECHNIQUE,
            motion="固定机位，快门声落下后极慢压暗，不添加字幕。",
            ambience="夹子轻响、相纸滴水、机械快门、通风机余音。",
            scene="旧照相馆暗房、晾片绳",
            props="湿相纸、晾片绳、旧相机、夹子",
        ),
    ]
