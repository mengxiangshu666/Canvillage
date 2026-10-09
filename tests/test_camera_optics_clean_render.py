import pytest

from novelvideo.freezone.camera_optics import build_optical_camera_prompt


@pytest.mark.parametrize("body", ["arricam_lt", "arriflex_435", "imax_keighley", "imax_film_camera"])
def test_film_camera_preserves_optics_without_injecting_grain(body):
    prompt = build_optical_camera_prompt(
        camera_body=body, lens="cooke_s4", focal_length_mm=35, aperture="f/4"
    )
    assert "grain" not in prompt.casefold()
    assert "clean fine detail" in prompt
    assert "35mm" in prompt
    assert "f/4" in prompt
    assert "Cooke" in prompt
