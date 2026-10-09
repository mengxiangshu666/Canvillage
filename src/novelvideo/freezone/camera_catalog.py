"""Shared source of truth for Freezone image camera options."""

from __future__ import annotations

from copy import deepcopy


FREEZONE_IMAGE_CAMERA_OPTIONS = {
    "camera_bodies": [
        {"id": "imax_keighley", "label": "IMAX Keighley"},
        {"id": "panavision_dxl2", "label": "Panavision DXL2"},
        {"id": "arri_alexa_65", "label": "ARRI ALEXA 65"},
        {"id": "arri_alexa_35", "label": "ARRI ALEXA 35"},
        {"id": "arricam_lt", "label": "Arricam LT"},
        {"id": "red_vraptor_xl", "label": "RED V-Raptor XL"},
        {"id": "sony_venice_2", "label": "Sony Venice 2"},
        {"id": "imax_film_camera", "label": "IMAX Film Camera"},
    ],
    "lenses": [
        {"id": "arri_signature_prime", "label": "Arri Signature Prime"},
        {"id": "cooke_s4i", "label": "Cooke S4/i"},
        {"id": "cooke_sf_18x", "label": "Cooke SF 1.8x Anamorphic"},
        {"id": "zeiss_supreme_prime", "label": "Zeiss Supreme Prime"},
        {"id": "zeiss_ultra_prime", "label": "Zeiss Ultra Prime"},
        {"id": "panavision_primo_70", "label": "Panavision Primo 70"},
        {"id": "panavision_c_series", "label": "Panavision C-series"},
        {"id": "canon_k35", "label": "Canon K-35"},
        {"id": "hawk_class_x", "label": "Hawk Class X"},
    ],
    "focal_lengths_mm": [8, 14, 24, 35, 50, 75, 125],
    "apertures": ["f/1.4", "f/2", "f/2.8", "f/4", "f/5.6", "f/8", "f/11"],
}


def get_image_camera_options() -> dict:
    return deepcopy(FREEZONE_IMAGE_CAMERA_OPTIONS)


__all__ = ["FREEZONE_IMAGE_CAMERA_OPTIONS", "get_image_camera_options"]
