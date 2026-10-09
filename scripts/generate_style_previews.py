"""为内置风格批量生成预览图。

默认调用正在运行的本地 Village Infinite Canvas API，使用应用自身已配置的图片模型出口，
不读取、复制或落盘任何密钥。生成采用可恢复、原子写入策略。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path

import httpx
from PIL import Image

from novelvideo.styles.preview_image import normalize_native_16x9_preview


PREVIEW_PROMPTS = {
    "anime": "黄昏城市天桥上，两位成年动画角色在列车驶过时回头交谈，完整上半身、手势、城市环境和天空层次同时可见，日式二维动画电影关键帧",
    "anime_cel_premium": "黄昏屋顶上，一位成年女剑士迎风转身，远处城市与云层形成清晰层次，中景动画关键帧，完整角色动作和环境同时可见",
    "arthouse_naturalism": "阴天的旧公寓厨房里，两位成年人隔着餐桌安静对视，窗光落在真实生活痕迹上，中景电影剧照，克制而细腻",
    "brutalist_future": "巨型混凝土未来大厅内，一名成年旅行者站在狭长天窗光束下，人物作为宏大建筑的尺度锚点，深景广角",
    "clay_stop_motion": "手工微缩火车站里，一名成年黏土角色提着旧皮箱回头，木质站台与小灯具层次丰富，定格动画电影画面",
    "cosmic_sci_fi": "宇宙飞船观景舱内，一名成年宇航员望向窗外巨大的蓝色行星，人物与工程结构都清晰，宏大电影感",
    "cyberpunk_neon": "雨夜未来街区，一名成年快递员站在透明雨棚下查看机械包裹，霓虹反射穿过多层城市空间，中景电影剧照",
    "dark_fantasy": "风暴将至的古老山门前，一名成年守卫举起火把面对远处巨影，磨损石材、披风与深重阴影清晰可见",
    "documentary_16mm": "清晨菜市场里，一位成年摊主整理水果，顾客从前景经过，真实自然光与生活动作，观察式16毫米纪实画面",
    "fashion_editorial": "极简深色摄影棚中，一位成年模特穿结构感长外套侧身迈步，服装轮廓、织物运动和雕塑光线清晰",
    "graphic_novel": "雨中车站，一名成年记者举起相机捕捉远处奔跑的人影，强剪影、墨线与选择性色彩，单帧冲突清晰",
    "neo_noir": "雨夜城市公寓内，一位成年女侦探站在百叶窗旁回头，桌上钨丝台灯照亮展开的案卷，中景电影剧照",
    "nordic_lifestyle": "明亮北欧住宅里，两位成年人一起准备早餐，浅木、织物与窗外柔光形成自然生活层次，中景纪实构图",
    "paper_cut_folk": "山村节庆夜晚，一名成年舞者举起灯笼穿过层叠屋檐与花纹云朵，精致中国民俗剪纸，轮廓清晰",
    "product_cinematic": "深色干净摄影棚中，一只无品牌的磨砂玻璃香水瓶立在黑色石台上，水珠、玻璃边缘与精确倒影构成高级商业主视觉",
    "retro_hong_kong": "九十年代雨夜街巷，一对成年男女站在茶餐厅门口短暂对视，钨丝灯、霓虹倒影和拥挤城市纵深清晰",
    "romantic_period": "十九世纪宅邸窗边，两位成年人隔着一张小桌交换目光，烛光、窗纱和衣料表现克制情绪，中景年代电影画面",
    "stylized_3d_family": "温暖奇幻车站里，一名成年旅人抱着行李与一只小动物告别，风格化三维角色、布景和电影布光完整呈现",
    "tang_epic": "盛大宫门前，一名成年使者走过仪仗与飘动旌旗，夕阳勾勒建筑、服饰和群像层次，宏大历史电影画面",
    "ukiyo_e_modern": "海风中的现代港口，一名成年女子撑伞站在浪纹与帆影之间，靛蓝朱红色块与优雅轮廓构成现代浮世绘",
    "watercolor_storybook": "春日河畔，一名成年旅行者牵着小马走向远处村庄，透明水彩、纸张纹理与清晰故事焦点",
    "wuxia_ink": "云雾山巅，两名成年剑客隔着石桥对峙，衣摆和竹叶沿同一方向飞扬，水墨留白与动作姿态形成笔锋",
    "song_poetic_landscape": "晨雾中的宋式山水与楼阁沿江展开，一名成年行旅者走过长桥，矿物青绿、绢本肌理、三远法空间与大面积留白清晰可辨",
    "dunhuang_mural": "风化洞窟壁面上的飞天与商旅叙事横向展开，石青石绿朱砂矿物色、灰泥裂纹、飘带动势与壁画边饰完整呈现",
    "chinese_shadow_puppet": "暖白幕布前，两名彩色皮影人物持兵器交锋，半透明染色皮革、镂刻纹样、铆钉关节与灯火背光清晰可见",
    "chinese_opera_stage": "深色戏台上一位成年武生与一位成年花旦隔桌对峙，脸谱、水袖、靠旗、正红孔雀蓝服饰与象征性舞台调度准确",
    "french_new_wave": "六十年代巴黎街角，两位成年人边走边争论，轻便手持摄影、自然街光、高反差银盐颗粒与偏轴构图形成真实电影剧照",
    "expressionist_silent": "歪斜城市布景中一名成年男子穿过尖锐投影，纯黑粉白、高硬侧光、扭曲透视与默片式夸张剪影明确",
    "spaghetti_western": "烈日荒原的决斗前夕，两名成年枪手隔着尘土街道相望，晒褪赭土、硬光、极端远景与面部特写张力同框",
    "analog_retro_future": "七十年代想象的太空控制室里，一名成年工程师检查实体开关与CRT屏幕，钨丝灯、粗线缆、磨损金属和模拟辉光可信",
    "solarpunk_cinema": "阳光下的生态社区横跨温室、住宅和水循环设施，成年人骑车穿过绿植建筑，清洁技术与日常生活真实可读",
    "pixel_art_cinema": "雨后像素城市车站，一名成年旅人等待列车，受控色板、清晰像素簇、块状光影和前中后景视差构成宽银幕场景",
    "low_poly_diorama": "微缩山谷车站中，一名成年旅人走向低多边形列车，三角折面、哑光材质、桌面模型尺度和柔和接触阴影明确",
    "porcelain_fantasy": "青花瓷构成的河谷城市中，一名成年旅者跨过拱桥，钴蓝釉下线条、温润釉白、山水纹样和瓷面高光连续统一",
    "surreal_dreamscape": "自然晨光中的普通公寓延伸成无尽海岸，一名成年人站在门口观察唯一一处不可能空间折叠，写实而克制",
    "technicolor_musical": "华丽棚拍火车站内，成年舞者群沿对称阶梯完成队形变化，宝石红、钴蓝、祖母绿服装与奶油白布景形成经典三色染印歌舞电影画面",
    "giallo_psychological": "巴洛克公寓的狭长走廊里，一名成年女子透过镜面发现远处人影，猩红、孔雀绿和钴蓝凝胶光切开深黑阴影，窥视构图与长焦压缩明确",
    "afrofuturist_epic": "宏大未来城市的仪式广场上，成年工程师与人群穿过太阳能塔和织物穹顶，赤陶、深靛、黄金与先进材料形成完整可信的非洲未来文化系统",
    "art_deco_metropolis": "黑金装饰艺术酒店大厅内，两位成年人沿阶梯相向而行，黄铜、磨砂玻璃、放射纹、扇形光影和严格对称几何完整呈现",
    "ligne_claire_european": "海滨城市车站里，一名成年记者追随驶离的蓝色列车，等粗清线、平涂原色、精确建筑与交通工具透视、清晰前中后景",
    "oil_painted_animation": "风暴前的海边村庄中，一名成年旅人迎风走向灯塔，可见鬃毛笔触、厚涂亮部、破色、画布肌理与稳定人物剪影贯穿角色和环境",
    "upa_midcentury_modern": "中世纪现代风格的城市咖啡馆里，两名成年动画角色交谈，奶油白、芥末黄、砖红、青绿色块，细线人物、不对称负空间、几何家具与高度简化背景准确",
    "felt_puppet_stopmotion": "手工微缩森林车站中，一名成年毛毡木偶提着缝制皮箱等待小火车，针毡纤维、缝线、纽扣、布料接缝和真实桌面阴影清楚可见",
    "pinscreen_engraving": "纯黑银灰的针幕浮雕梦境中，一名成年旅人穿过风吹草原，低角度掠射光在密集金属针阵列上形成连续灰阶、铜版画颗粒和浅浮雕阴影",
    "gongbi_heavy_color": "古典园林宴集横向展开，成年人物按礼仪关系围坐，游丝描细线、石青石绿朱砂、层层罩染、泥金与精密服饰器物纹样准确",
    "yamato_e_emaki": "季节云霞分隔的宫廷绘卷中，多组成年人物在吹拔屋台式室内连续叙事，纸本矿物色、高视点和横向阅读节奏完整",
    "korean_minhwa": "朝鲜民画式书架与花园场景中，成年主人整理器物，石青石绿朱红、纸纤维、朴拙比例、逆透视和装饰平衡清晰可辨",
    "indian_miniature": "宫廷花园中，多位成年人物在水池、亭阁与花树之间展开叙事，珠宝般矿物色、金饰、高视点、精密织物和装饰边框完整呈现",
    "persian_miniature": "层叠园林与亭阁构成的诗意画页中，成年旅者沿曲折山径会面，群青青绿朱红、金色、多重视点、云气和精密纹样明确",
    "cyanotype_photography": "海边温室中，一名成年人穿过植物与玻璃投影，画面以普鲁士蓝和纸白呈现，接触印相轮廓、手工刷涂边界、纸纤维和化学晕染可信",
    "realistic": "清晨真实城市街区中，两位成年人在路边咖啡店前自然交谈，完整人物关系、街道纵深、真实材质、自然窗光和克制景深共同构成现代电影中景",
}

COMMON_SUFFIX = (
    "。严格16:9横屏电影构图；预览图必须完整展示这种视觉风格的色彩、布光、材质、构图和空间层次；"
    "主体不得只是证件照或纯头像；无文字、无字幕、无标志、无水印。"
)


async def generate_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    *,
    api_base: str,
    model: str,
    style_id: str,
    prompt: str,
    output_path: Path,
    output_format: str,
    max_width: int,
    attempts: int,
) -> dict[str, object]:
    url = f"{api_base.rstrip('/')}/styles/{style_id}/preview"
    async with semaphore:
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(
                    url,
                    json={"prompt": prompt + COMMON_SUFFIX, "model": model},
                )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    raise RuntimeError(response.text[:500])
                with Image.open(io.BytesIO(response.content)) as source:
                    source.load()
                    image = normalize_native_16x9_preview(source, max_width)
                    encoded = io.BytesIO()
                    if output_format == "webp":
                        image.save(encoded, format="WEBP", quality=88, method=6)
                    else:
                        image.save(encoded, format="PNG", optimize=True)
                    image_bytes = encoded.getvalue()
                temp_path = output_path.with_suffix(output_path.suffix + ".partial")
                temp_path.write_bytes(image_bytes)
                temp_path.replace(output_path)
                return {
                    "style_id": style_id,
                    "ok": True,
                    "bytes": len(image_bytes),
                    "attempt": attempt,
                }
            except Exception as exc:  # noqa: BLE001 - batch must continue per style
                last_error = str(exc)
                if attempt < attempts:
                    await asyncio.sleep(2**attempt)
        return {"style_id": style_id, "ok": False, "error": last_error}


async def main_async(args: argparse.Namespace) -> int:
    preset_dir = Path(args.preset_dir).resolve()
    selected_ids = {
        item.strip()
        for value in args.ids
        for item in value.split(",")
        if item.strip()
    }
    jobs: list[tuple[str, str, Path]] = []
    for path in sorted(preset_dir.glob("*.json")):
        style_id = path.stem
        if selected_ids and style_id not in selected_ids:
            continue
        prompt = PREVIEW_PROMPTS.get(style_id)
        output_path = preset_dir / f"{style_id}.{args.format}"
        existing_preview = any(
            (preset_dir / f"{style_id}{suffix}").exists()
            for suffix in (".webp", ".png", ".jpg", ".jpeg")
        )
        if not prompt or (existing_preview and not args.force):
            continue
        jobs.append((style_id, prompt, output_path))

    semaphore = asyncio.Semaphore(max(1, args.workers))
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *(
                generate_one(
                    client,
                    semaphore,
                    api_base=args.api_base,
                    model=args.model,
                    style_id=style_id,
                    prompt=prompt,
                    output_path=output_path,
                    output_format=args.format,
                    max_width=args.max_width,
                    attempts=args.attempts,
                )
                for style_id, prompt, output_path in jobs
            )
        )

    report_dir = Path(__file__).parents[1] / "output" / "style-previews"
    report_dir.mkdir(parents=True, exist_ok=True)
    manifest = report_dir / "preview_generation_report.json"
    manifest.write_text(
        json.dumps({"requested": len(jobs), "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ok_count = sum(bool(item["ok"]) for item in results)
    print(f"PREVIEWS={ok_count}/{len(results)} REPORT={manifest}")
    for item in results:
        print(json.dumps(item, ensure_ascii=False))
    return 0 if ok_count == len(results) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://127.0.0.1:8781/api/v1")
    parser.add_argument("--model", default="nanobanana")
    parser.add_argument(
        "--preset-dir",
        default=str(Path(__file__).parents[1] / "src" / "novelvideo" / "styles" / "presets"),
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=360.0)
    parser.add_argument("--format", choices=("webp", "png"), default="webp")
    parser.add_argument("--max-width", type=int, default=768)
    parser.add_argument(
        "--ids",
        action="append",
        default=[],
        help="Only generate these style ids; accepts repeated or comma-separated values.",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
