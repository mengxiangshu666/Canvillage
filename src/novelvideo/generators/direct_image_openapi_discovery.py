"""Non-billing OpenAPI discovery for direct image model parameters."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_DOCUMENT_PATHS = ("/openapi.json", "/swagger.json", "/api-docs", "/v1/openapi.json", "/v1/swagger.json")
_IMAGE_PATH = re.compile(r"image|images", re.IGNORECASE)
_RATIO_KEY = re.compile(r"aspect.?ratio|image.?ratio|supported.?ratios", re.IGNORECASE)
_RATIO_VALUE = re.compile(r"^\s*\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?\s*$")


def discover_image_openapi(
    *, base_url: str, api_key: str, protocol: str, timeout: float = 3.0
) -> dict[str, Any]:
    """Read public API schemas and return only explicit image ratio enums."""
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"status": "invalid-base-url", "aspectRatioOptions": []}
    root = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    prefix = parsed.path.rstrip("/")
    paths = tuple(dict.fromkeys(
        [f"{prefix}{suffix}" for suffix in ("/openapi.json", "/swagger.json") if prefix]
        + list(_DOCUMENT_PATHS)
    ))
    try:
        import httpx

        from novelvideo.generators.model_contracts import (
            get_model_contract,
        )

        headers = get_model_contract(protocol).auth.headers(api_key)
        headers["Accept"] = "application/json"
        with httpx.Client(timeout=max(0.5, min(float(timeout), 5.0)), follow_redirects=True) as client:
            for path in paths:
                try:
                    response = client.get(f"{root}{path}", headers=headers)
                except httpx.HTTPError:
                    continue
                if response.status_code >= 400 or len(response.content) > 2_000_000:
                    continue
                try:
                    document = response.json()
                except ValueError:
                    continue
                ratios = extract_image_aspect_ratios(document)
                if isinstance(document, Mapping) and isinstance(document.get("paths"), Mapping):
                    return {
                        "status": "schema-found",
                        "documentPath": path,
                        "aspectRatioOptions": ratios,
                    }
    except Exception:
        pass
    return {"status": "not-found", "aspectRatioOptions": []}


def extract_image_aspect_ratios(document: object) -> list[str]:
    """Extract ratio enums only from image operations in an OpenAPI document."""
    if not isinstance(document, Mapping):
        return []
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        return []
    definitions: Mapping[str, Any] = {}
    components = document.get("components")
    if isinstance(components, Mapping) and isinstance(components.get("schemas"), Mapping):
        definitions = components["schemas"]
    elif isinstance(document.get("definitions"), Mapping):
        definitions = document["definitions"]

    result: list[str] = []
    visited: set[str] = set()

    def visit(value: object, key: str = "", depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(value, Mapping):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith(("#/components/schemas/", "#/definitions/")):
                name = reference.rsplit("/", 1)[-1]
                if name not in visited and isinstance(definitions.get(name), Mapping):
                    visited.add(name)
                    visit(definitions[name], name, depth + 1)
            if _RATIO_KEY.search(key):
                for candidate in _enum_candidates(value):
                    if isinstance(candidate, str) and _RATIO_VALUE.fullmatch(candidate):
                        normalized = re.sub(r"\s+", "", candidate)
                        if normalized not in result:
                            result.append(normalized)
            for child_key, child in value.items():
                if child_key in {"example", "examples", "externalDocs"}:
                    continue
                visit(child, str(child_key), depth + 1)
        elif isinstance(value, list):
            for child in value[:256]:
                visit(child, key, depth + 1)

    for path, path_item in paths.items():
        if not _IMAGE_PATH.search(str(path)) or not isinstance(path_item, Mapping):
            continue
        for method, operation in path_item.items():
            if str(method).casefold() not in {"post", "put", "patch"} or not isinstance(operation, Mapping):
                continue
            visit(operation.get("parameters", []), depth=0)
            visit(operation.get("requestBody", {}), depth=0)
    return result[:64]


def _enum_candidates(value: Mapping[str, Any]) -> list[object]:
    candidates: list[object] = []
    enum = value.get("enum")
    if isinstance(enum, list):
        candidates.extend(enum)
    items = value.get("items")
    if isinstance(items, Mapping) and isinstance(items.get("enum"), list):
        candidates.extend(items["enum"])
    for key in ("default", "x-enum", "x-enum-values", "supportedValues"):
        raw = value.get(key)
        if isinstance(raw, list):
            candidates.extend(raw)
    return candidates


__all__ = ["discover_image_openapi", "extract_image_aspect_ratios"]
