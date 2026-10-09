"""产品身份单一来源。

第三方 provider（OpenRouter 等）会把 `HTTP-Referer` / `X-Title` 记进它们的
请求归因与排行榜，所以这两个值**就是**产品对外的署名。此前它们散落在五个
模块里各自硬编码，值是上游遗留名，既改不动也防不住回填。

集中到这里之后：改一次名字 = 改一个常量；再有新调用点就是引用本模块，
而不是再抄一份字符串。

与前端 `frontend/src/lib/product-identity.ts` 同名同义，两边改要一起改。
"""

from __future__ import annotations

# 对外显示名。中文界面用 `PRODUCT_NAME_ZH`，provider 归因用英文名。
PRODUCT_NAME = "Village Infinite Canvas"
PRODUCT_NAME_ZH = "村长无限画布"

# 归因用站点地址。OpenRouter 只拿它做来源标注，不要求可访问。
PRODUCT_SITE_URL = "https://github.com/mengxiangshu666/infinite-canvas"


def attribution_headers(subject: str = "") -> dict[str, str]:
    """构造第三方 provider 的归因头。

    `subject` 是子功能名（如 "Scene Overlap"），拼进 `X-Title` 以便在
    OpenRouter 的活动面板里区分是产品的哪条链路在调用。空则只报产品名。
    """

    title = f"{PRODUCT_NAME} {subject}".strip() if subject else PRODUCT_NAME
    return {
        "HTTP-Referer": PRODUCT_SITE_URL,
        "X-Title": title,
    }
