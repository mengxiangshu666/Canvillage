"""Fetch the effect-plaza preview media and generate the canvas template catalog.

This is a one-off asset ingestion tool, not part of the product runtime. It reads
the libtv effect-plaza snapshot (passed with ``--source``), downloads each
template's animated WebP demo, transcodes it to a small muted H.264 preview,
extracts a WebP poster from the first frame, and writes the TypeScript catalog
used by the canvas effect library.

Usage:
    .venv/Scripts/python.exe scripts/maintenance/fetch_effect_plaza_previews.py \
        --source E:/AI影视研究/libtv爬取/20_语料/effect_plaza/effect_plaza_slim.json

The source snapshot is a read-only reference. Output assets live under
``frontend/public`` and are served by the product; see the generated SOURCE.md
for attribution and the rights caveat.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

# target: camera writes the camera segment; effect writes the visual-effect clause.
# The prompt is a short, single-shot description derived from the template name
# and its preview, not the original LoRA trigger word (the platform exposes none).
PROMPTS: dict[int, tuple[str, str, list[str]]] = {
    23075240: ("camera", "镜头像蜜蜂绕飞一样快速环绕主体，忽近忽远、灵活变向，主体始终保持在画面中心", ["小蜜蜂", "环绕", "灵活运镜"]),
    23043916: ("effect", "一道锐利高光沿产品轮廓扫过，金属与玻璃表面依次亮起，背景保持干净暗场", ["产品", "扫光", "高光", "电商"]),
    23053563: ("camera", "镜头贴着地平线高速前冲，随后拉升俯瞰辽阔大地，主体始终位于画面前景", ["地平线", "前冲", "拉升", "航拍"]),
    23043915: ("camera", "主体保持不动，镜头从正面、侧面、背面等多个固定机位切换展示，每次切换干净利落", ["多角度", "定点", "展示", "产品"]),
    23043918: ("camera", "镜头高速穿过云层向下俯冲，云气从镜头两侧掠过，最后落到主体所在的场景", ["穿云", "俯冲", "云层"]),
    23043924: ("effect", "人物跟随节拍完成连贯舞蹈动作，肢体舒展、重心转换自然，衣摆随动作摆动", ["编舞", "舞蹈", "节拍"]),
    23053555: ("effect", "主体沿结构线拆解成悬浮零件，零件在空中缓慢旋转展示，再逐层组装回原样", ["3D解构", "拆解", "零件", "组装"]),
    23030899: ("camera", "镜头极速推进到人物眼睛，瞳孔逐渐占满画面，虹膜纹理清晰，瞳孔内映出下一幕画面", ["瞳孔", "推镜", "眼睛"]),
    23064304: ("camera", "镜头从高空向地面高速俯冲，城市与山川迅速放大，最后贴近主体", ["俯冲", "地球", "航拍"]),
    23077957: ("camera", "镜头围绕主体快速小半径环绕并不断变换高度，像蜜蜂一样灵活穿梭", ["小蜜蜂", "环绕", "变向"]),
    23030897: ("camera", "镜头微距贴近产品细节，沿表面缓慢推进，纹理与高光清晰可见", ["微距", "推镜", "细节"]),
    23053557: ("effect", "两人近身对打，攻防节奏紧凑，拳脚接触处迸发火花与冲击波，镜头跟随动作", ["双人", "对打", "格斗"]),
    23030896: ("camera", "主体在水下缓慢移动，气泡上升，光线在水体中折射成摇曳光斑", ["水下", "慢镜头", "折射"]),
    23064310: ("camera", "动作在命中瞬间定格，镜头绕主体缓慢环绕，飞溅的碎片与水滴悬停在空中", ["子弹时间", "定格", "环绕"]),
    23064303: ("camera", "镜头从主体快速拉远到城市、地球尺度，再迅速推回主体，空间跨度连续", ["环球", "缩放", "拉远"]),
    23064302: ("camera", "镜头从地球全景快速推进到地面主体，云层与地表细节连续放大", ["地球", "缩放", "推进"]),
    23030895: ("effect", "碎片、尘埃与人物衣摆违反重力向上漂浮，物体缓慢升空并围绕主体旋转", ["逆转引力", "失重", "漂浮"]),
    23064307: ("camera", "镜头跟随车辆在山路高速追逐，车身漂移，轮胎扬起尘土，背景快速后退", ["山路", "追击", "车辆", "漂移"]),
    23043922: ("camera", "低角度仰拍反派从阴影中缓步走来，逆光勾出轮廓，压迫感逐渐增强", ["反派", "登场", "仰拍"]),
    23030900: ("camera", "镜头围绕人物面部缓慢环绕，五官与光影随角度变化，背景虚化", ["面部", "环拍", "环绕"]),
    23053565: ("camera", "产品从画面深处缓慢悬浮推近，轻微旋转后落到画面中心，背景光晕随距离收拢", ["悬浮", "缓入", "产品"]),
    23053556: ("effect", "人物皮肤从面部开始逐层转化为金属机械结构，接缝亮起冷光，表情保持冷静", ["机械姬", "机械", "转化"]),
    23030906: ("effect", "背景星云旋转汇聚成漩涡，星光被拉成弧线，中心逐渐亮起", ["星云", "漩涡", "星光"]),
    23043926: ("effect", "成群飞鸟在空中突然解体为发光粒子，粒子随风散开又重新聚拢", ["飞鸟", "解体", "粒子"]),
    23053564: ("camera", "车辆在雪地高速漂移，雪花与冰屑被轮胎卷起，镜头贴身跟随", ["雪地", "赛车", "漂移"]),
    23064308: ("effect", "无数冷白星尘从天空缓缓降临，粒子带轻微闪烁，落点泛起柔光", ["星尘", "降临", "粒子"]),
    23053558: ("effect", "重击命中瞬间进入升格慢放，对手被击飞，汗珠与碎屑悬停，随后恢复正常速度", ["升格", "KO", "慢放", "击飞"]),
    23030903: ("camera", "镜头高速旋转并向前推进，画面像涡轮一样卷动，主体始终保持在旋转中心", ["涡轮", "旋转", "推进"]),
    23043925: ("effect", "爆炸火球在极慢动作中向外膨胀，碎片与火星悬浮飞散，冲击波推开空气", ["升格", "爆炸", "慢动作"]),
    23043917: ("effect", "人物被飞旋的机甲碎片包裹，装甲从四肢向躯干逐层咬合，最后面甲合拢、双眼亮起", ["机甲", "变身", "装甲"]),
    23030898: ("camera", "镜头特写人物面部，妆容从底妆到眼唇逐层生成变化，皮肤质感保持真实", ["试妆", "特写", "妆容"]),
    23064306: ("camera", "低角度仰拍巨大身影俯瞰镜头，人物在画面下方显得渺小，空气中有尘埃漂浮", ["巨人", "俯瞰", "仰拍"]),
    23053560: ("effect", "人物与巨龙近距离互动，龙翼展开带起气流，镜头围绕二者环绕", ["驯龙", "巨龙", "奇幻"]),
    23043920: ("camera", "镜头跟随车辆穿行城市街道，霓虹与路灯在车身表面流动，背景建筑快速后退", ["城市", "驾驶", "跟随"]),
    23043919: ("camera", "产品置于展厅灯光下缓慢旋转展示，地面反射清晰，背景干净", ["展厅", "产品", "旋转"]),
    23030902: ("camera", "聚光灯下主角登场，红毯两侧闪光灯连成一片，镜头低角度跟随", ["巨星", "登场", "聚光灯"]),
    23030907: ("effect", "红毯两侧闪光灯连续爆闪，强光在人物轮廓上炸开，背景人群虚化", ["红毯", "闪光灯", "爆闪"]),
    23030908: ("effect", "画面从火焰中亮起，火舌沿画面边缘蔓延，主体在火光中逐渐显现", ["燃烧", "开场", "火焰"]),
    23043923: ("effect", "雨滴在空中悬停，随手掌动作改变方向，随后重新加速落下，地面溅起水花", ["控雨", "雨滴", "悬停"]),
    23008756: ("camera", "直升机从画面外飞入并悬停，旋翼气流吹动地面尘土，镜头随直升机移动", ["直升机", "揭幕", "飞入"]),
    23053559: ("effect", "深海巨兽从黑暗水中缓缓浮现，轮廓被探照灯照亮，周围浮游物缓慢飘动", ["深海", "巨兽", "浮现"]),
    23053562: ("effect", "人物在转身瞬间完成换装，旧衣物化作光点散去，新装从肩部向下逐层贴合", ["换装", "变装", "服装"]),
    23030904: ("camera", "模特沿 T 台向镜头走来，步伐稳定，灯光随位置变化，背景观众虚化", ["T台", "模特", "走秀"]),
    23053561: ("camera", "镜头微距特写饰品，高光沿金属与宝石表面流动，背景保持暗场", ["饰品", "特写", "微距"]),
    23030905: ("effect", "人物瞳孔骤然收缩并变色，虹膜纹路亮起微光，眼神变得锐利", ["瞳孔", "异变", "虹膜"]),
    23064305: ("camera", "镜头以 360 度全景视角展示周围环境，空间关系完整，边缘轻微畸变", ["全景", "360", "环境"]),
    23030901: ("effect", "镜面中分裂出多个相同主体，分身动作略有延迟，镜面反射保持清晰", ["镜面", "分身", "反射"]),
    23043921: ("effect", "光翼从人物背后展开，流光沿羽翼纹理流动，光点向四周飘散", ["光翼", "展翼", "流光"]),
    23064309: ("effect", "面部被投影扫描，妆容沿扫描线逐步生成，最终形成高饱和赛博妆面", ["赛博", "化妆", "扫描"]),
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) village-canvas asset fetch"


@dataclass
class Template:
    id: int
    name: str
    demo: str
    author: str
    heat: int


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def render_assets(template: Template, repo: Path, staging: Path) -> tuple[Path, Path]:
    demo = staging / f"{template.id}.webp"
    if not demo.exists():
        download(template.demo, demo)
    video = repo / "frontend/public/video/effect-templates" / f"{template.id}.mp4"
    poster = repo / "frontend/public/effects/templates" / f"{template.id}.webp"
    video.parent.mkdir(parents=True, exist_ok=True)
    poster.parent.mkdir(parents=True, exist_ok=True)
    scale = "scale=360:360:force_original_aspect_ratio=decrease"
    run_ffmpeg([
        "-i", str(demo),
        "-vf", f"{scale},pad=360:360:(ow-iw)/2:(oh-ih)/2:color=black",
        "-r", "10", "-c:v", "libx264", "-preset", "veryfast", "-crf", "31",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(video),
    ])
    # 首帧可能是黑场（「燃烧开场」「3D解构」实测），取第 1 秒的帧做封面。
    run_ffmpeg([
        "-i", str(demo), "-ss", "1", "-frames:v", "1", "-vf", scale,
        "-c:v", "libwebp", "-quality", "78", str(poster),
    ])
    return video, poster


def render_typescript(templates: list[Template], repo: Path) -> Path:
    lines = [
        "// SPDX-License-Identifier: Elastic-2.0",
        "// Copyright (c) 2026 ClaymoreLab",
        "// Generated by scripts/maintenance/fetch_effect_plaza_previews.py.",
        "// Source: libtv effect-plaza snapshot (49 lens templates). Preview media is",
        "// third-party platform material; read the SOURCE.md next to the assets before",
        "// publishing it outside this local workspace.",
        "",
        "export type EffectTemplateTarget = 'camera' | 'effect';",
        "",
        "export interface EffectTemplate {",
        "  id: string;",
        "  label: string;",
        "  target: EffectTemplateTarget;",
        "  promptFragment: string;",
        "  keywords: readonly string[];",
        "  posterUrl: string;",
        "  videoUrl: string;",
        "  author: string;",
        "  heat: number;",
        "}",
        "",
        "export const EFFECT_TEMPLATES: ReadonlyArray<EffectTemplate> = [",
    ]
    for template in templates:
        target, prompt, keywords = PROMPTS[template.id]
        lines.extend([
            "  {",
            f"    id: {json.dumps(str(template.id))},",
            f"    label: {json.dumps(template.name, ensure_ascii=False)},",
            f"    target: {json.dumps(target)},",
            f"    promptFragment: {json.dumps(prompt, ensure_ascii=False)},",
            f"    keywords: {json.dumps(keywords, ensure_ascii=False)},",
            f"    posterUrl: {json.dumps(f'/effects/templates/{template.id}.webp')},",
            f"    videoUrl: {json.dumps(f'/video/effect-templates/{template.id}.mp4')},",
            f"    author: {json.dumps(template.author, ensure_ascii=False)},",
            f"    heat: {template.heat},",
            "  },",
        ])
    lines.extend([
        "];",
        "",
        "export function filterEffectTemplates(",
        "  templates: ReadonlyArray<EffectTemplate>,",
        "  query: string,",
        "): EffectTemplate[] {",
        "  const terms = query",
        "    .trim()",
        "    .toLowerCase()",
        "    .split(/\\s+/)",
        "    .filter(Boolean);",
        "  if (terms.length === 0) return [...templates];",
        "  return templates.filter((template) => {",
        "    const haystack = [",
        "      template.label,",
        "      template.promptFragment,",
        "      ...template.keywords,",
        "    ]",
        "      .join(' ')",
        "      .toLowerCase();",
        "    return terms.every((term) => haystack.includes(term));",
        "  });",
        "}",
        "",
    ])
    output = repo / "frontend/src/features/canvas/domain/effectTemplates.ts"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def render_source_note(templates: list[Template], repo: Path) -> Path:
    lines = [
        "# 特效模板预览素材来源",
        "",
        "这些文件由 `scripts/maintenance/fetch_effect_plaza_previews.py` 从 libtv",
        "特效广场快照（49 条镜头模板）转码生成：动态 WebP demo 转成 360×360、10fps、",
        "无音轨的 H.264 预览，封面取自动图首帧。它们不是本仓原创素材。",
        "",
        "**权利提示**：模板作者与平台以快照 `author` 字段为准，作者对预览画面保留权利。",
        "当前只用于本机工作区的效果预览；同步到公开源码仓或对外分发前必须重新确认授权。",
        "产品离线运行不依赖 libtv 或任何外部 CDN——这些文件已随包落盘。",
        "",
        "| id | 名称 | 作者 | 原始 demo |",
        "|---|---|---|---|",
    ]
    for template in sorted(templates, key=lambda item: -item.heat):
        lines.append(f"| {template.id} | {template.name} | {template.author} | {template.demo} |")
    output = repo / "frontend/public/effects/templates/SOURCE.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="path to effect_plaza_slim.json")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    items = source["items"]
    if args.limit:
        items = items[: args.limit]
    templates = [
        Template(
            id=int(item["id"]),
            name=str(item["name"]),
            demo=str(item["demo"]),
            author=str(item.get("author") or ""),
            heat=int(item.get("heat") or 0),
        )
        for item in items
    ]
    missing = [template.id for template in templates if template.id not in PROMPTS]
    if missing:
        raise SystemExit(f"missing prompt mapping for: {missing}")

    repo = Path(args.repo).resolve()
    staging = repo / "workspace/artifacts/effect-plaza-source"
    staging.mkdir(parents=True, exist_ok=True)

    failures: list[tuple[int, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(render_assets, template, repo, staging): template
            for template in templates
        }
        for future in as_completed(futures):
            template = futures[future]
            try:
                video, poster = future.result()
                print(f"ok {template.id} {template.name} {video.stat().st_size} {poster.stat().st_size}")
            except Exception as error:  # noqa: BLE001 - report and continue
                failures.append((template.id, str(error)))
                print(f"FAIL {template.id} {template.name}: {error}", file=sys.stderr)

    if failures:
        raise SystemExit(f"{len(failures)} template(s) failed: {failures}")

    catalog = render_typescript(templates, repo)
    note = render_source_note(templates, repo)
    video_bytes = sum(path.stat().st_size for path in (repo / "frontend/public/video/effect-templates").glob("*.mp4"))
    poster_bytes = sum(path.stat().st_size for path in (repo / "frontend/public/effects/templates").glob("*.webp"))
    print(f"wrote {catalog}")
    print(f"wrote {note}")
    print(f"templates={len(templates)} video_bytes={video_bytes} poster_bytes={poster_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
