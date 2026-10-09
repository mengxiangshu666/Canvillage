"""LibTV-grade camera / lens / focal / aperture optics for Freezone image prompts.

Source of truth for optical language: reverse-engineered LibTV cameraControl
suffixes (desktop study pack 09-source-extreme). Freezone UI ids are mapped
through aliases so picker labels stay stable while uplink prompts carry real
cinematography language.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Optical suffix libraries (prompt fragments, no leading comma)
# ---------------------------------------------------------------------------

CAMERA_BODY_OPTICS: dict[str, str] = {
    "sony_venice": "clean digital look, 8k uhd",
    "arri_alexa_35": "cinematic color grading, soft highlights",
    "arri_alexa_65": (
        "Shot on ARRI Alexa 65, large format cinematography, hyper-realistic, "
        "cinematic lighting, 8k resolution, movie still"
    ),
    "red_v_raptor": "high contrast, sharp focus",
    "panavision_dxl2": "Hollywood movie style",
    "arricam_lt": (
        "Shot on Arricam LT, Kodak Vision3 500T color response, pin sharp focus, "
        "clean fine detail, soft highlight roll-off, rich color density, faint red halo "
        "at the edge of the light source, highly detailed texture"
    ),
    "arriflex_435": (
        "Shot on Arriflex 435, Kodak Vision3 250D 5207 style, pin sharp focus, "
        "clean fine detail, soft highlight roll-off, high dynamic range, rich color density"
    ),
    "imax_keighley": (
        "shot on IMAX Keighley, Kodak Vision3 500T style, extremely sharp focus, "
        "high dynamic range, Extremely shallow depth of field, 18k resolution, "
        "hyper-realistic skin texture, clean fine detail, 3D separation, color graded, "
        "volumetric lighting, epic scale"
    ),
    "imax_film_camera": "high resolution, clean fine detail",
}

LENS_OPTICS: dict[str, str] = {
    "zeiss_ultra_prime": "using Zeiss Ultra Prime, clinically sharp, high contrast",
    "cooke_sf_18x": (
        "using Cooke Anamorphic SF, Identify the light source, golden lens flare, "
        "oval bokeh, Full Frame Anamorphic, warm cinematic look"
    ),
    "canon_k35": "using Canon K-35 lens, soft contrast, golden lens flare",
    "cooke_s4": "using Cooke S4 lens, The Cooke Look, creamy bokeh",
    "cooke_speed_panchro": (
        "using Cooke Speed Panchro Series III, buttery background blur, The Cooke Look, "
        "warm organic tones, soft sharpness, atmospheric depth"
    ),
    "arri_signature_prime": "using Arri Signature Prime, clean image, perfect optics",
    "helios": "using Helios 44-2 lens, swirly bokeh, twisted background",
    "panavision_c_series": (
        "anamorphic cinematic look, distinct blue horizontal lens flare, oval bokeh"
    ),
    "panavision_primo": (
        "using Panavision Primo Anamorphic, oval bokeh, Anamorphic format, "
        "horizontal blue lens flare, 2x squeeze, anamorphic look"
    ),
    "hawk_class_x": (
        "using Hawk Class X Anamorphic, identify the light source, subtle creamy "
        "vertical oval bokeh, organic sharpness, vintage modern aesthetic, soft horizontal flare"
    ),
    # Freezone UI aliases that are close cousins of LibTV keys
    "zeiss_supreme_prime": "using Zeiss Supreme Prime, clinically sharp, high contrast, modern cinema optics",
    "cooke_s4i": "using Cooke S4/i lens, The Cooke Look, creamy bokeh, warm organic rendering",
    "panavision_primo_70": (
        "using Panavision Primo 70, large-format anamorphic character, oval bokeh, "
        "horizontal flare, premium cinema compression"
    ),
}

FOCAL_OPTICS: dict[str, str] = {
    "8mm": (
        "8mm prime lens, exaggerated perspective, Rectilinear, immense sense of scale, "
        "stretching at edges"
    ),
    "14mm": (
        "14mm prime lens, rectilinear distortion, moderate linear perspective, subtle edge "
        "stretch, spatial depth, soft vignetting"
    ),
    "24mm": (
        "24mm prime lens, minimal rectilinear distortion, natural linear perspective, "
        "sharp corner details, cinematic natural framing, mild vignetting"
    ),
    "35mm": (
        "35mm prime lens, near-zero rectilinear distortion, human-eye natural perspective, "
        "sharp full-frame details, subtle vignetting, candid cinematic framing"
    ),
    "50mm": (
        "50mm prime lens, zero distortion, eye-like perspective, close-up, subject focus, "
        "ultra shallow DOF, sharp center, faint vignetting"
    ),
    "75mm": (
        "75mm prime lens, zero distortion, strong compression, extreme tight close, "
        "subject full dominance, extreme shallow DOF, sharp center, faint vignetting"
    ),
    "125mm": (
        "125mm macro lens, 1:1 magnification, extreme compression, microscopic view, "
        "razor-sharp details, total background isolation, creamy bokeh, texture focused"
    ),
}

APERTURE_OPTICS: dict[str, str] = {
    "f/1.4": "shallow depth of field, creamy bokeh, out-of-focus background",
    "f/2": "shallow depth of field, soft background separation, gentle bokeh",
    "f/2.8": "moderate shallow depth of field, subject separation with readable background",
    "f/4": "f/4 aperture, deep enough focus for subject and near environment, controlled bokeh",
    "f/5.6": "f/5.6 aperture, extended depth of field, crisp subject and midground",
    "f/8": "f/8 aperture, deep depth of field, sharp environmental detail",
    "f/11": "f/11 aperture, deep depth of field, crystal clear background, everything in focus",
}

# Freezone picker ids / display labels → library keys
_BODY_ALIASES: dict[str, str] = {
    "sony_venice_2": "sony_venice",
    "sony venice 2": "sony_venice",
    "sony venice": "sony_venice",
    "red_vraptor_xl": "red_v_raptor",
    "red v-raptor xl": "red_v_raptor",
    "red v-raptor": "red_v_raptor",
    "red v raptor": "red_v_raptor",
    "arri alexa 65": "arri_alexa_65",
    "arri alexa 35": "arri_alexa_35",
    "panavision dxl2": "panavision_dxl2",
    "imax keighley": "imax_keighley",
    "imax film camera": "imax_film_camera",
    "arricam lt": "arricam_lt",
    "arriflex 435": "arriflex_435",
}

_LENS_ALIASES: dict[str, str] = {
    "cooke s4/i": "cooke_s4i",
    "cooke s4i": "cooke_s4i",
    "cooke s4": "cooke_s4",
    "arri signature prime": "arri_signature_prime",
    "zeiss supreme prime": "zeiss_supreme_prime",
    "zeiss ultra prime": "zeiss_ultra_prime",
    "panavision primo 70": "panavision_primo_70",
    "panavision primo": "panavision_primo",
    "panavision c-series": "panavision_c_series",
    "panavision c series": "panavision_c_series",
    "canon k-35": "canon_k35",
    "canon k35": "canon_k35",
    "hawk class x": "hawk_class_x",
}


def _norm_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    folded = text.casefold().replace("–", "-").replace("—", "-")
    folded = re.sub(r"[\s/]+", " ", folded).strip()
    underscored = re.sub(r"[\s\-]+", "_", folded)
    underscored = re.sub(r"_+", "_", underscored)
    return underscored


def _resolve_body_key(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    candidates = [
        text,
        _norm_key(text),
        text.casefold(),
        re.sub(r"[\s_\-]+", " ", text.casefold()).strip(),
    ]
    for cand in candidates:
        if cand in CAMERA_BODY_OPTICS:
            return cand
        alias = _BODY_ALIASES.get(cand)
        if alias:
            return alias
        alias = _BODY_ALIASES.get(_norm_key(cand))
        if alias:
            return alias
    # fuzzy: first token families
    nk = _norm_key(text)
    for key in CAMERA_BODY_OPTICS:
        if nk == key or nk.startswith(key) or key.startswith(nk):
            return key
    if "venice" in nk:
        return "sony_venice"
    if "alexa_65" in nk or "alexa65" in nk:
        return "arri_alexa_65"
    if "alexa_35" in nk or "alexa35" in nk:
        return "arri_alexa_35"
    if "vraptor" in nk or "v_raptor" in nk:
        return "red_v_raptor"
    if "dxl" in nk:
        return "panavision_dxl2"
    if "keighley" in nk:
        return "imax_keighley"
    if "imax" in nk:
        return "imax_film_camera"
    return None


def _resolve_lens_key(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    candidates = [
        text,
        _norm_key(text),
        text.casefold(),
        re.sub(r"[\s_\-]+", " ", text.casefold()).strip(),
    ]
    for cand in candidates:
        if cand in LENS_OPTICS:
            return cand
        alias = _LENS_ALIASES.get(cand)
        if alias:
            return alias
        alias = _LENS_ALIASES.get(_norm_key(cand).replace("_", " "))
        if alias:
            return alias
    nk = _norm_key(text)
    for key in LENS_OPTICS:
        if nk == key or nk.startswith(key) or key.startswith(nk):
            return key
    if "signature" in nk:
        return "arri_signature_prime"
    if "supreme" in nk:
        return "zeiss_supreme_prime"
    if "ultra_prime" in nk or "ultraprime" in nk:
        return "zeiss_ultra_prime"
    if "primo" in nk:
        return "panavision_primo_70" if "70" in nk else "panavision_primo"
    if "cooke" in nk and "s4" in nk:
        return "cooke_s4i" if "i" in nk else "cooke_s4"
    if "helios" in nk:
        return "helios"
    if "hawk" in nk:
        return "hawk_class_x"
    if "k35" in nk or "k_35" in nk:
        return "canon_k35"
    return None


def _resolve_focal_key(focal_mm: int | float | str | None) -> str | None:
    if focal_mm is None or focal_mm == "":
        return None
    try:
        value = int(float(focal_mm))
    except (TypeError, ValueError):
        text = str(focal_mm).strip().lower().replace(" ", "")
        m = re.search(r"(\d+)\s*mm", text)
        if not m:
            return None
        value = int(m.group(1))
    key = f"{value}mm"
    if key in FOCAL_OPTICS:
        return key
    # nearest supported focal
    supported = sorted(int(k.replace("mm", "")) for k in FOCAL_OPTICS)
    nearest = min(supported, key=lambda x: abs(x - value))
    return f"{nearest}mm"


def _resolve_aperture_key(raw: str) -> str | None:
    text = str(raw or "").strip().casefold().replace(" ", "")
    if not text:
        return None
    text = text.replace("ƒ", "f").replace("ｆ", "f")
    if not text.startswith("f"):
        text = f"f/{text.lstrip('/')}"
    text = text.replace("f", "f/", 1) if text.startswith("f") and not text.startswith("f/") else text
    text = re.sub(r"f/+", "f/", text)
    if text in APERTURE_OPTICS:
        return text
    # map common stops onto nearest library entry
    m = re.search(r"f/([\d.]+)", text)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    supported = []
    for key in APERTURE_OPTICS:
        mm = re.search(r"f/([\d.]+)", key)
        if mm:
            supported.append((float(mm.group(1)), key))
    if not supported:
        return None
    nearest = min(supported, key=lambda item: abs(item[0] - val))
    return nearest[1]


def resolve_camera_optics(
    *,
    camera_body: str = "",
    lens: str = "",
    focal_length_mm: int | float | str | None = None,
    aperture: str = "",
) -> dict[str, Any]:
    """Resolve UI camera fields into optics keys + fragments."""
    body_key = _resolve_body_key(camera_body)
    lens_key = _resolve_lens_key(lens)
    focal_key = _resolve_focal_key(focal_length_mm)
    aperture_key = _resolve_aperture_key(aperture)
    return {
        "body_key": body_key,
        "lens_key": lens_key,
        "focal_key": focal_key,
        "aperture_key": aperture_key,
        "body_optics": CAMERA_BODY_OPTICS.get(body_key or "", ""),
        "lens_optics": LENS_OPTICS.get(lens_key or "", ""),
        "focal_optics": FOCAL_OPTICS.get(focal_key or "", ""),
        "aperture_optics": APERTURE_OPTICS.get(aperture_key or "", ""),
        "display_body": str(camera_body or "").strip(),
        "display_lens": str(lens or "").strip(),
        "display_focal": (
            f"{int(float(focal_length_mm))}mm"
            if focal_length_mm not in (None, "")
            else ""
        ),
        "display_aperture": str(aperture or "").strip(),
    }


def build_optical_camera_prompt(
    *,
    camera_body: str = "",
    lens: str = "",
    focal_length_mm: int | float | str | None = None,
    aperture: str = "",
) -> str:
    """Build a production-grade camera block for image generation uplink."""
    resolved = resolve_camera_optics(
        camera_body=camera_body,
        lens=lens,
        focal_length_mm=focal_length_mm,
        aperture=aperture,
    )
    setup_bits: list[str] = []
    if resolved["display_body"]:
        setup_bits.append(resolved["display_body"])
    elif resolved["body_key"]:
        setup_bits.append(resolved["body_key"].replace("_", " "))
    if resolved["display_lens"]:
        setup_bits.append(resolved["display_lens"])
    elif resolved["lens_key"]:
        setup_bits.append(resolved["lens_key"].replace("_", " "))
    if resolved["display_focal"]:
        setup_bits.append(resolved["display_focal"])
    elif resolved["focal_key"]:
        setup_bits.append(resolved["focal_key"])
    if resolved["display_aperture"]:
        setup_bits.append(resolved["display_aperture"])
    elif resolved["aperture_key"]:
        setup_bits.append(resolved["aperture_key"])

    optics_bits = [
        fragment
        for fragment in (
            resolved["body_optics"],
            resolved["lens_optics"],
            resolved["focal_optics"],
            resolved["aperture_optics"],
        )
        if fragment
    ]
    if not setup_bits and not optics_bits:
        return ""

    lines = ["Camera setup:"]
    if setup_bits:
        lines.append(f"- {' | '.join(setup_bits)}")
    if optics_bits:
        # Single dense optical line — models respond better than id-only tags.
        lines.append(f"- Optical character: {'; '.join(optics_bits)}")
    lines.append(
        "- Preserve this camera language in framing, lens feel, depth rendition, "
        "highlight roll-off, and overall optical character."
    )
    return "\n".join(lines)
