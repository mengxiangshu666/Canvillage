"""Resolve Cognee's text route from the same model registry as the canvas."""

from __future__ import annotations

from dataclasses import dataclass
import os

from novelvideo.model_gateway_settings import get_effective_newapi_config
from novelvideo.official_defaults import DEFAULT_COGNEE_LLM_MODEL


@dataclass(frozen=True, slots=True)
class CogneeTextModelBinding:
    api_key: str
    base_url: str
    upstream_model: str
    source: str
    runtime_ready: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.upstream_model)

    def litellm_kwargs(self) -> dict[str, str]:
        """Return one explicit in-memory LiteLLM route for this binding."""

        if not self.configured:
            raise ValueError("尚未配置可用的直连文字模型，请先在模型中心配置并检测。")
        if not self.runtime_ready:
            raise ValueError(
                "直连文字模型尚未通过完整 Chat + Stream 运行合同，请先在模型中心重新检测连接。"
            )
        model = self.upstream_model
        if not model.startswith(("openai/", "custom/")):
            model = f"openai/{model}"
        return {
            "model": model,
            "api_key": self.api_key,
            "api_base": self.base_url,
        }


def _direct_models_only() -> bool:
    return os.environ.get("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_cognee_text_model_binding() -> CogneeTextModelBinding:
    """Prefer the operator-configured direct text model without hidden aliases."""

    from novelvideo.generators.direct_models import (
        ensure_direct_model_runtime_ready,
        resolve_direct_model,
    )

    direct = resolve_direct_model("text")
    if direct is not None:
        try:
            ensure_direct_model_runtime_ready(direct)
            runtime_ready = True
        except (TypeError, ValueError):
            # Importing the application must remain possible while the model
            # center shows an incomplete probe.  The first actual Cognee call
            # still fails through litellm_kwargs with a precise re-detect hint.
            runtime_ready = False
        return CogneeTextModelBinding(
            api_key=direct.api_key,
            base_url=direct.base_url,
            upstream_model=direct.upstream_model,
            source="direct" if runtime_ready else "direct-unverified",
            runtime_ready=runtime_ready,
        )
    if _direct_models_only():
        return CogneeTextModelBinding("", "", "", "direct-unconfigured")

    gateway = get_effective_newapi_config()
    model = os.environ.get("COGNEE_LLM_MODEL", "").strip() or DEFAULT_COGNEE_LLM_MODEL
    return CogneeTextModelBinding(
        api_key=str(gateway.api_key or "").strip(),
        base_url=str(gateway.base_url or "").strip(),
        upstream_model=model,
        source="gateway",
    )


def get_cognee_litellm_kwargs() -> dict[str, str]:
    """Resolve LiteLLM calls from the same selected text model as Cognee."""

    return resolve_cognee_text_model_binding().litellm_kwargs()


__all__ = [
    "CogneeTextModelBinding",
    "get_cognee_litellm_kwargs",
    "resolve_cognee_text_model_binding",
]
