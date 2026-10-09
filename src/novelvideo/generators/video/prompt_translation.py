"""Prompt translation helper used by video adapters."""

from __future__ import annotations

__all__ = ["translate_prompt_to_english"]


async def translate_prompt_to_english(prompt: str) -> str:
    """将中文视频提示词翻译并优化为 WAN I2V 最佳格式。

    使用模型中心的直连文字模型，按照 WAN 模型最佳实践结构化输出。

    Args:
        prompt: 中文提示词

    Returns:
        优化后的英文提示词
    """
    if not prompt:
        return prompt

    # 检查是否已经是英文（简单判断：不含中文字符）
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in prompt)
    if not has_chinese:
        return prompt

    try:
        from novelvideo.generators.direct_models import get_direct_pydantic_model

        runtime_model = get_direct_pydantic_model("text", None, timeout_seconds=120.0)
        if runtime_model is None:
            print("[VIDEO] 未配置可用的直连文字模型，保留原提示词")
            return prompt

        translation_prompt = f"""You are an expert at writing prompts for WAN 2.1 Image-to-Video AI model.

Convert this Chinese video motion description into an optimized English prompt following WAN I2V best practices.

## WAN I2V Prompt Rules:
1. Focus on MOTION and CAMERA MOVEMENT (the image already defines the subject/scene)
2. Use speed adverbs: "slowly", "gently", "dramatically", "subtly"
3. Use effective camera keywords: "push in", "pull back", "tracking shot", "static shot"
4. AVOID: "whip pan", "crash zoom", "dolly out" (these don't work well)
5. Add lighting if relevant: "soft lighting", "rim lighting", "backlit"
6. Keep it concise (under 80 words)

## Output Format:
[Lighting if relevant], [Shot type if relevant]. [Subject motion description]. [Camera movement].

## Chinese Input:
{prompt}

## English Output (ONLY the optimized prompt, nothing else):"""

        from pydantic_ai import Agent

        response = await Agent(
            runtime_model,
            system_prompt="Return only the optimized English WAN I2V prompt.",
            output_type=str,
            name="Video Prompt Translator",
        ).run(translation_prompt)

        english_prompt = str(response.output or "").strip()
        if not english_prompt:
            return prompt
        # 移除可能的引号包裹
        if english_prompt.startswith('"') and english_prompt.endswith('"'):
            english_prompt = english_prompt[1:-1]
        print(f"[VIDEO] 优化提示词: {prompt} -> {english_prompt}")
        return english_prompt

    except Exception as e:
        print(f"[VIDEO] 翻译失败，使用原文: {e}")
        return prompt
