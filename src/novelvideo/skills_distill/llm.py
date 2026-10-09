"""技能蒸馏 LLM 客户端 —— 复用 Village Infinite Canvas 生效网关配置（零额外配置）。

走 OpenAI 兼容 chat/completions；网关 base_url/api_key 来自
model_gateway_settings.get_effective_newapi_config()，模型名可用
SKILLS_DISTILL_MODEL 环境变量覆盖。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from novelvideo.model_gateway_settings import get_effective_newapi_config

logger = logging.getLogger("novelvideo.skills_distill.llm")

DEFAULT_MODEL = ""


def _direct_models_only() -> bool:
    return os.environ.get("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_direct_text_model(model: str | None):
    from novelvideo.generators.direct_models import resolve_direct_model

    direct = resolve_direct_model("text", model)
    if _direct_models_only() and direct is None:
        requested = str(model or "").strip() or "默认文字模型"
        raise RuntimeError(f"技能蒸馏文字模型不可用：{requested}；请在模型中心配置并检测。")
    if direct is not None and direct.protocol not in {"openai-compatible", "ollama-openai"}:
        raise RuntimeError(
            "技能蒸馏当前执行器需要 OpenAI-compatible chat/completions 协议；"
            f"当前模型协议为 {direct.protocol}。"
        )
    return direct


def _chat_completions_url(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _channels(*, direct_model=None) -> list[tuple[str, str, str, str]]:
    """返回 (base, key, name, model) 通道链，首选模型中心记录。"""
    chans: list[tuple[str, str, str, str]] = []

    def add(base: str, key: str, name: str, model: str = ""):
        base = base.rstrip("/")
        if base and key and not any(c[0] == base and c[1] == key for c in chans):
            chans.append((base, key, name, str(model or "").strip()))

    if direct_model is not None:
        add(direct_model.base_url, direct_model.api_key, "direct", direct_model.upstream_model)
        return chans

    if _direct_models_only():
        return chans

    base_env = os.environ.get("SKILLS_LLM_BASE", "").rstrip("/")
    key_env = os.environ.get("SKILLS_LLM_KEY", "")
    if base_env and key_env:
        add(base_env, key_env, "env", os.environ.get("SKILLS_DISTILL_MODEL", ""))
    try:
        cfg = get_effective_newapi_config()
        add(cfg.base_url, cfg.api_key, "gateway", os.environ.get("SKILLS_DISTILL_MODEL", ""))
    except Exception:  # noqa: BLE001
        pass
    return chans


def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    json_mode: bool = True,
    timeout: float = 120,
    retries: int = 3,
) -> str:
    """调用 LLM，返回文本输出。通道链逐个尝试：env → 网关 → deepseek。"""
    direct_model = _resolve_direct_text_model(model)
    selected_model = direct_model.upstream_model if direct_model is not None else str(model or DEFAULT_MODEL).strip()
    channels = _channels(direct_model=direct_model)
    if not channels:
        raise RuntimeError("no configured skills-distill LLM channel; add a text model in the model center")
    last_err: Exception | None = None
    for base, api_key, cname, channel_model in channels:
        request_model = selected_model or channel_model
        if not request_model:
            raise RuntimeError("技能蒸馏未配置模型 ID；请在模型中心填写上游模型 ID。")
        url = _chat_completions_url(base)
        body = {
            "model": request_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        }
        try:
            for attempt in range(retries):
                try:
                    data = _http_post(url, headers, body, timeout)
                    if isinstance(data, dict) and data.get("error"):
                        raise RuntimeError(f"LLM error: {data['error']}")
                    return data["choices"][0]["message"]["content"].strip()
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    logger.warning(
                        "LLM channel %s attempt %d/%d failed: %s",
                        cname, attempt + 1, retries, e,
                    )
                    time.sleep(min(2**attempt, 8))
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM channel %s exhausted: %s", cname, e)
    raise RuntimeError(f"LLM call failed after {len(channels)} channels: {last_err}")


def _escape_invalid_json_backslashes(text: str) -> str:
    valid_escapes = set('"\\/bfnrtu')
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt not in valid_escapes:
                out.append("\\\\")
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_json_from_model(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"```$", "", cleaned).strip()
    for s in (cleaned, _escape_invalid_json_backslashes(cleaned)):
        try:
            return json.loads(s, strict=False)
        except json.JSONDecodeError:
            pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        body = cleaned[start : end + 1]
        for s in (body, _escape_invalid_json_backslashes(body)):
            try:
                return json.loads(s, strict=False)
            except json.JSONDecodeError:
                pass
    raise ValueError(f"Could not parse model response as JSON: {text[:200]}...")
