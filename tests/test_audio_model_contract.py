from novelvideo.audio.model_contract import compile_audio_speech_contract
from novelvideo.generators.direct_model_capabilities import (
    direct_model_capability_summary,
    infer_direct_model_protocol,
    normalize_direct_model_base_url,
)


def test_autodl_audio_protocol_and_workflow_capability_are_explicit():
    assert infer_direct_model_protocol(
        "audio", "indextts2-v1", base_url="https://autodl.art"
    ) == "autodl-comfyui"
    assert normalize_direct_model_base_url("https://autodl.art") == "https://autodl.art"
    metadata = {
        "supportedModes": ["text_to_speech"],
        "inputSlots": ["text", "voice_reference"],
        "referenceLimits": {"text_to_speech": {"audio": 1}},
        "providerMapping": {
            "voice_reference": {"field": "prompt_simple", "transport": "url"}
        },
        "parameterDefaults": {"emo_calm": 0.3},
    }
    capability = direct_model_capability_summary(
        "audio",
        "indextts2-v1",
        protocol="autodl-comfyui",
        base_url="https://autodl.art",
        metadata=metadata,
    )
    contract = compile_audio_speech_contract(capability)
    assert contract.accepts_voice_reference
    assert contract.reference_audio_limit == 1
    assert contract.voice_reference_field == "prompt_simple"
    assert contract.voice_reference_transport == "url"
    assert contract.parameter_defaults["emo_calm"] == 0.3
