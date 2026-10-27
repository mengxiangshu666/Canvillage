"""Import the libtv image-style library (three lists, 20 unique styles).

The three endpoints ``image1/styleList``, ``image1/doubao/styleList`` and
``image1/kontext/styleList`` mostly repeat the same 20 styles. Each record has
an official ``textPrompt`` (with ``%s`` as the user-prompt injection point) and
a preview image. This tool dedupes by name, downloads the preview, and writes a
normal StyleConfig preset plus a bundled WebP preview.

Usage:
    .venv/Scripts/python.exe scripts/maintenance/import_libtv_image_styles.py \
        --source E:/AI影视研究/libtv爬取/30_证据/api_probe/scatter/_styles_opening_full.json

The source snapshot is read-only. Preview images are third-party platform
material; see docs/ai/acceptance/libtv-image-styles-20261027.md for the rights
caveat before publishing outside this local workspace.
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) village-canvas style import"

# name -> (slug, family, style_family, animation_subtype, chinese tag)
STYLE_META: dict[str, tuple[str, str, str, str, str]] = {
    "irasutoya 小人风格": ("libtv_irasutoya", "illustration", "animation", "2d", "插画漫画"),
    "卡通玩具": ("libtv_toy_package", "three_d", "animation", "3d", "3D潮玩"),
    "街机像素风": ("libtv_arcade_pixel", "pixel", "animation", "2d", "像素复古"),
    "emoji风格图标": ("libtv_emoji_icon", "three_d", "animation", "3d", "3D潮玩"),
    "riso噪点插画": ("libtv_riso_print", "illustration", "animation", "2d", "插画漫画"),
    "噪点胶片": ("libtv_grain_film", "photography", "live_action", "", "摄影胶片"),
    "搞怪emoji油画": ("libtv_emoji_oil_painting", "three_d", "animation", "3d", "3D潮玩"),
    "3D科幻场景": ("libtv_scifi_3d", "three_d", "animation", "3d", "3D潮玩"),
    "粘土动画画风": ("libtv_clay_animation", "three_d", "animation", "3d", "3D潮玩"),
    "小人国世界": ("libtv_tiny_world", "three_d", "animation", "3d", "3D潮玩"),
    "lego积木风": ("libtv_lego", "three_d", "animation", "3d", "3D潮玩"),
    "超真实感照片": ("libtv_candid_photo", "photography", "live_action", "", "摄影胶片"),
    "jellycat 风格": ("libtv_jellycat", "three_d", "animation", "3d", "3D潮玩"),
    "复古美漫插图": ("libtv_retro_american_comic", "illustration", "animation", "2d", "插画漫画"),
    "古典欧式绘本": ("libtv_european_storybook", "illustration", "animation", "2d", "插画漫画"),
    "矢量线条图标": ("libtv_vector_line_icon", "illustration", "animation", "2d", "插画漫画"),
    "复古日漫风": ("libtv_retro_anime", "illustration", "animation", "2d", "插画漫画"),
    "撕拉片胶卷": ("libtv_polaroid", "photography", "live_action", "", "摄影胶片"),
    "布偶风格": ("libtv_puppet", "three_d", "animation", "3d", "3D潮玩"),
    "kitty拟人化": ("libtv_sanrio_girl", "three_d", "animation", "3d", "3D潮玩"),
}

NEGATIVE = (
    "No watermark, no signature, no garbled text, no extra fingers, "
    "no distorted anatomy, no low-resolution blur."
)

# The preset catalog contract requires every entry to fill the rich fields and
# ship a 768x432 preview. These category-level notes carry the studio grammar;
# the per-style textPrompt stays in style_instructions.
RICH_BY_FAMILY: dict[str, dict[str, str]] = {
    "illustration": {
        "palette": "flat high-saturation colors, limited palette, clean separation between shapes",
        "lighting": "even illustration lighting, minimal cast shadows, readable silhouettes",
        "optics": "orthographic 2D drawing, no photographic lens distortion",
        "composition": "single readable subject with clean negative space",
        "camera_motion": "static frame; if animated, use gentle 2D parallax",
    },
    "three_d": {
        "palette": "vivid toy-like colors, glossy highlights, simple material separation",
        "lighting": "soft studio HDRI, gentle rim light, clean contact shadows",
        "optics": "50mm product lens, moderate depth of field, no wide-angle distortion",
        "composition": "centered product-like subject on a clean background",
        "camera_motion": "slow turntable orbit or slight push-in, stable horizon",
    },
    "photography": {
        "palette": "natural film colors, low saturation, warm highlights and deep shadows",
        "lighting": "available light with soft shadows, slight underexposure, visible grain",
        "optics": "35mm snapshot lens, shallow depth of field, mild lens falloff",
        "composition": "off-center candid framing with believable background detail",
        "camera_motion": "handheld drift or static frame, no smooth commercial dolly",
    },
    "pixel": {
        "palette": "limited 16-bit palette, strong square color blocks",
        "lighting": "flat pixel shading with hard-edged highlights",
        "optics": "orthographic pixel grid, no anti-aliased photographic detail",
        "composition": "side-scroller framing with readable sprite silhouette",
        "camera_motion": "static frame; keep the pixel grid locked",
    },
}


def clean_prompt(value: str) -> str:
    text = (value or "").replace("%s", " ").strip()
    return text.strip(" ,，。")


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def save_preview(content: bytes, destination: Path) -> None:
    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image = ImageOps.fit(image, (768, 432), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "WEBP", quality=82, method=6)


def collect_styles(source: dict) -> list[dict]:
    seen: dict[str, dict] = {}
    for key in ("styleList_main", "styleList_doubao", "styleList_kontext"):
        for item in source.get(key) or []:
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            if name not in STYLE_META:
                raise SystemExit(f"missing style mapping for: {name}")
            seen[name] = item
    return list(seen.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="path to _styles_opening_full.json")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    styles = collect_styles(source)
    repo = Path(args.repo).resolve()
    presets_dir = repo / "src/novelvideo/styles/presets"
    presets_dir.mkdir(parents=True, exist_ok=True)

    total_bytes = 0
    for item in styles:
        name = str(item["name"]).strip()
        slug, family, style_family, animation_subtype, chinese_tag = STYLE_META[name]
        instructions = clean_prompt(str(item.get("textPrompt") or ""))
        if not instructions:
            raise SystemExit(f"empty textPrompt for: {name}")
        preview = presets_dir / f"{slug}.webp"
        content = download(str(item["imageUrl"]))
        save_preview(content, preview)
        total_bytes += preview.stat().st_size
        rich = RICH_BY_FAMILY[family]
        payload = {
            "id": slug,
            "name": name,
            "label": name,
            "family": family,
            "era": "",
            "palette": rich["palette"],
            "lighting": rich["lighting"],
            "optics": rich["optics"],
            "composition": rich["composition"],
            "camera_motion": rich["camera_motion"],
            "style_instructions": instructions,
            "avoid_instructions": NEGATIVE,
            "style_tag": name,
            "tags": [name, "libtv", chinese_tag],
            "model_overrides": {},
            "style_family": style_family,
            "animation_subtype": animation_subtype,
            "image_prompt": instructions,
            "video_prompt": (
                f"{instructions}. Keep the visual style consistent during motion; "
                "avoid morphing, flicker, or style drift."
            ),
            "negative_prompt": NEGATIVE,
        }
        (presets_dir / f"{slug}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"ok {slug} {name} preview={preview.stat().st_size}B")

    print(f"styles={len(styles)} preview_bytes={total_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
