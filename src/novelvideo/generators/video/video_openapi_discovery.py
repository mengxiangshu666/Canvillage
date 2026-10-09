"""Zero-billing OpenAPI/Swagger discovery for video endpoints."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


_DOCUMENT_PATHS = (
    "/openapi.json",
    "/swagger.json",
    "/api-docs",
    "/v1/openapi.json",
    "/v1/swagger.json",
)


def discover_video_openapi(
    *, base_url: str, api_key: str, timeout: float = 2.0
) -> dict[str, Any]:
    """Read bounded API descriptions without invoking a media operation."""

    try:
        import httpx

        root = _origin(base_url)
        headers = {
            "Authorization": f"Bearer {str(api_key or '').strip()}",
            "Accept": "application/json",
        }
        with httpx.Client(timeout=max(0.5, min(float(timeout), 5.0)), follow_redirects=True) as client:
            for path in _candidate_paths(base_url):
                try:
                    response = client.get(f"{root}{path}", headers=headers)
                except httpx.HTTPError:
                    continue
                if response.status_code >= 400:
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    continue
                paths = payload.get("paths") if isinstance(payload, dict) else None
                workflow_inputs = extract_workflow_inputs(payload)
                if not isinstance(paths, dict) or not paths:
                    if not workflow_inputs:
                        continue
                    operations = [{
                        "path": path,
                        "method": "post",
                        "operationId": "workflow",
                        "requestContentTypes": ["application/json"],
                        "responseContentTypes": [],
                        "parameters": workflow_inputs[:96],
                        "source": "workflow_schema",
                    } for path in ("/prompt", "/workflow")]
                else:
                    operations = _safe_operations(paths, payload)
                video_paths = sorted(
                    str(item)
                    for item in paths
                    if _is_video_route(str(item))
                ) if isinstance(paths, dict) else []
                return {
                    "found": True,
                    "url": f"{root}{path}",
                    "paths": video_paths,
                    "operations": operations,
                    "workflowInputs": workflow_inputs[:96],
                    "version": str(
                        (payload.get("openapi") if isinstance(payload, dict) else "")
                        or (payload.get("swagger") if isinstance(payload, dict) else "")
                        or ""
                    ),
                }
    except Exception:
        pass
    return {"found": False, "url": "", "paths": [], "operations": [], "version": ""}


def select_video_task_routes(
    *, base_url: str, paths: object, operations: object
) -> tuple[str, str]:
    """Select a documented create/query pair, preferring non-alias routes."""
    root_path = urlsplit(str(base_url or "").strip()).path.rstrip("/")

    def relative_path(value: object) -> str:
        path = str(value or "").strip()
        if root_path and (path == root_path or path.startswith(root_path + "/")):
            path = path[len(root_path) :]
        return "/" + path.strip("/") if path.strip("/") else ""

    operation_items = operations if isinstance(operations, (list, tuple)) else ()
    route_operations = [
        item
        for item in operation_items
        if isinstance(item, Mapping)
        and str(item.get("method") or "").strip().lower() == "post"
    ]
    route_operations.sort(
        key=lambda item: (
            "alias" in str(item.get("operationId") or "").casefold(),
            0
            if relative_path(item.get("path")).casefold().endswith("/videos/generations")
            else 1,
            relative_path(item.get("path")).casefold(),
        )
    )
    submit_path = next(
        (
            relative_path(item.get("path"))
            for item in route_operations
            if relative_path(item.get("path"))
            and "alias" not in str(item.get("operationId") or "").casefold()
        ),
        "",
    )
    path_items = paths if isinstance(paths, (list, tuple, set)) else ()
    route_paths = [relative_path(item) for item in path_items]
    route_paths = [path for path in route_paths if path]
    task_path = ""
    if submit_path:
        prefix = submit_path.rstrip("/") + "/"
        task_path = next(
            (
                path
                for path in route_paths
                if path.startswith(prefix)
                and re.fullmatch(r"\{[^/{}]+\}", path[len(prefix) :])
            ),
            "",
        )
    if not task_path:
        task_path = next(
            (
                path
                for path in route_paths
                if re.search(r"/videos?/\{[^/{}]+\}$", path, re.I)
            ),
            "",
        )
    return submit_path, task_path


def extract_workflow_inputs(document: object) -> list[dict[str, object]]:
    """Extract deterministic ComfyUI graph inputs as capability evidence.

    ComfyUI workflows are often plain node maps rather than OpenAPI schemas.
    We inspect only primitive/default input values and never infer paid
    operations or hidden credentials. Connection tuples are graph wiring, not
    user-facing controls, and are skipped.
    """
    if not isinstance(document, Mapping):
        return []
    candidates: list[object] = []
    for key in ("workflow", "prompt", "nodes", "graph"):
        value = document.get(key)
        if isinstance(value, (Mapping, list, tuple)):
            candidates.append(value)
    if not candidates and all(isinstance(value, Mapping) for value in document.values()):
        candidates.append(document)
    result: list[dict[str, object]] = []
    seen: set[str] = set()

    def add(node_class: str, key: object, value: object) -> None:
        name = str(key or "").strip()
        if not name or _looks_sensitive_name(name) or isinstance(value, (Mapping, list, tuple)):
            return
        marker = name.casefold()
        if marker in seen:
            return
        seen.add(marker)
        if isinstance(value, bool):
            type_name = "boolean"
        elif isinstance(value, int) and not isinstance(value, bool):
            type_name = "integer"
        elif isinstance(value, float):
            type_name = "number"
        else:
            type_name = "string"
        item: dict[str, object] = {
            "key": name,
            "providerKey": name,
            "type": type_name,
            "default": value,
            "source": "workflow_schema",
        }
        if node_class:
            item["nodeClass"] = node_class[:120]
        result.append(item)

    def visit(value: object, node_class: str = "") -> None:
        if isinstance(value, Mapping):
            current_class = str(
                value.get("class_type") or value.get("classType") or value.get("node_type") or value.get("type") or node_class or ""
            ).strip()
            inputs = value.get("inputs")
            if isinstance(inputs, Mapping):
                for key, item in inputs.items():
                    add(current_class, key, item)
            for key, item in value.items():
                if key not in {"inputs", "_meta", "outputs"} and isinstance(item, (Mapping, list, tuple)):
                    visit(item, current_class)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item, node_class)

    for candidate in candidates:
        visit(candidate)
    return result[:128]


def _safe_operations(
    paths: dict[object, object], document: Mapping[str, Any] | None = None
) -> list[dict[str, object]]:
    """Keep video submission schemas only for capability extraction.

    The operation path alone is not enough to compile a provider request.  A
    request body's JSON schema is still zero-billing metadata, so retain its
    input properties in a small, sanitized form for the capability envelope.
    """

    definitions = (
        document.get("components", {}).get("schemas", {})
        if isinstance(document, Mapping)
        and isinstance(document.get("components"), Mapping)
        and isinstance(document.get("components", {}).get("schemas"), Mapping)
        else {}
    )
    if not definitions and isinstance(document, Mapping) and isinstance(document.get("definitions"), Mapping):
        # Swagger 2 uses a top-level ``definitions`` collection.
        definitions = document["definitions"]

    operations: list[dict[str, object]] = []
    for raw_path, raw_item in paths.items():
        path = str(raw_path or "").strip()
        if not path or not isinstance(raw_item, dict):
            continue
        for raw_method, raw_operation in raw_item.items():
            method = str(raw_method or "").strip().lower()
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation = raw_operation if isinstance(raw_operation, dict) else {}
            if not _is_video_operation(path, method, operation):
                continue
            if method not in {"post", "put", "patch"}:
                continue
            request_body = operation.get("requestBody")
            request_content = (
                sorted(str(item) for item in request_body.get("content", {}) if str(item).strip())
                if isinstance(request_body, dict) and isinstance(request_body.get("content"), dict)
                else []
            )
            request_parameters: list[dict[str, object]] = []
            raw_parameters = operation.get("parameters")
            if method in {"post", "put", "patch"} and isinstance(raw_parameters, list):
                for item in raw_parameters:
                    if not isinstance(item, Mapping):
                        continue
                    location = str(item.get("in") or "").strip().lower()
                    name = str(item.get("name") or "").strip()
                    if location not in {"query", "path"} or not name:
                        continue
                    if name.casefold() in {"model", "task_id", "request_id", "id"}:
                        continue
                    schema = item.get("schema") if isinstance(item.get("schema"), Mapping) else item
                    definition = _safe_schema_definition(schema, definitions)
                    definition["key"] = name
                    if item.get("required") is True:
                        definition["required"] = True
                    request_parameters.append(definition)
            if method in {"post", "put", "patch"} and isinstance(request_body, Mapping) and isinstance(request_body.get("content"), Mapping):
                for media_type, media_definition in request_body["content"].items():
                    if not isinstance(media_definition, Mapping):
                        continue
                    if request_parameters and str(media_type).lower() not in {
                        "application/json",
                        "application/*+json",
                    }:
                        continue
                    schema = media_definition.get("schema")
                    if isinstance(schema, Mapping):
                        request_parameters.extend(
                            _safe_schema_parameters(schema, definitions)
                        )
                    if request_parameters:
                        break
            responses = operation.get("responses")
            response_content: set[str] = set()
            if isinstance(responses, dict):
                for response in responses.values():
                    if not isinstance(response, dict) or not isinstance(response.get("content"), dict):
                        continue
                    response_content.update(str(item) for item in response["content"] if str(item).strip())
            operation_summary: dict[str, object] = {
                    "path": path,
                    "method": method,
                    "operationId": str(operation.get("operationId") or "").strip(),
                    "requestContentTypes": request_content[:8],
                    "responseContentTypes": sorted(response_content)[:8],
                    "tags": [
                        str(item)[:80]
                        for item in (operation.get("tags") or [])[:8]
                        if str(item).strip()
                    ],
            }
            if request_parameters:
                operation_summary["parameters"] = request_parameters[:96]
            operations.append(operation_summary)
            if len(operations) >= 128:
                return operations
    return operations


_NON_VIDEO_API_SEGMENTS = re.compile(
    r"(?:^|/)(?:admin|account|accounts|auth|billing|keys|settings|users?)(?:/|$)",
    re.IGNORECASE,
)
_VIDEO_OPERATION_HINTS = re.compile(
    r"video|videos|text.?to.?video|image.?to.?video|txt2vid|img2vid|i2v|t2v|prediction|queue|workflow|comfy",
    re.IGNORECASE,
)


def _is_video_operation(path: str, method: str, operation: Mapping[str, Any]) -> bool:
    normalized_path = "/" + path.strip().strip("/")
    if not _is_video_route(normalized_path):
        return False
    operation_text = " ".join(
        [
            normalized_path,
            str(operation.get("operationId") or ""),
            " ".join(str(item) for item in operation.get("tags", []) if str(item).strip())
            if isinstance(operation.get("tags"), list)
            else "",
        ]
    )
    return _VIDEO_OPERATION_HINTS.search(operation_text) is not None


def _is_video_route(path: str) -> bool:
    normalized_path = "/" + str(path or "").strip().strip("/")
    if not normalized_path.strip("/") or _NON_VIDEO_API_SEGMENTS.search(normalized_path):
        return False
    lowered = normalized_path.casefold()
    if lowered == "/prompt" or lowered.startswith("/history/"):
        return True
    if "/queue/" in lowered:
        return "/requests" in lowered
    return _VIDEO_OPERATION_HINTS.search(lowered) is not None


_SENSITIVE_SCHEMA_KEYS = {
    "example",
    "examples",
    "externalDocs",
    "xml",
}


def _resolve_schema(
    schema: Mapping[str, Any], definitions: Mapping[str, Any]
) -> Mapping[str, Any]:
    reference = str(schema.get("$ref") or "").strip()
    if reference.startswith("#/components/schemas/") or reference.startswith("#/definitions/"):
        resolved = definitions.get(reference.rsplit("/", 1)[-1])
        if isinstance(resolved, Mapping):
            return resolved
    return schema


def _safe_schema_definition(
    schema: Mapping[str, Any], definitions: Mapping[str, Any]
) -> dict[str, object]:
    """Project one OpenAPI schema without examples, refs, or credential data."""

    resolved = _resolve_schema(schema, definitions)
    result: dict[str, object] = {}
    for key in (
        "type",
        "format",
        "enum",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "title",
        "description",
        "nullable",
    ):
        if key not in resolved or key in _SENSITIVE_SCHEMA_KEYS:
            continue
        value = resolved[key]
        if key == "enum" and isinstance(value, list):
            result[key] = value[:100]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    nested = resolved.get("properties")
    if isinstance(nested, Mapping):
        result["properties"] = {
            str(name): _safe_schema_definition(value, definitions)
            for name, value in nested.items()
            if str(name).strip()
            and not _looks_sensitive_name(name)
            and isinstance(value, Mapping)
        }
    required = resolved.get("required")
    if isinstance(required, list):
        result["required"] = [str(item) for item in required[:100] if str(item).strip()]
    return result


def _safe_schema_parameters(
    schema: Mapping[str, Any], definitions: Mapping[str, Any]
) -> list[dict[str, object]]:
    resolved = _resolve_schema(schema, definitions)
    properties = resolved.get("properties")
    if not isinstance(properties, Mapping):
        return []
    required = {
        str(item).strip()
        for item in (resolved.get("required") or [])
        if str(item).strip()
    }
    result: list[dict[str, object]] = []
    for name, raw in properties.items():
        name_text = str(name).strip()
        if not name_text or _looks_sensitive_name(name_text) or not isinstance(raw, Mapping):
            continue
        definition = _safe_schema_definition(raw, definitions)
        definition["key"] = name_text
        if name_text in required:
            definition["required"] = True
        result.append(definition)
    return result


def _looks_sensitive_name(value: object) -> bool:
    lowered = str(value or "").replace("-", "_").casefold()
    return any(
        token in lowered
        for token in ("api_key", "access_token", "refresh_token", "password", "secret", "cookie", "authorization")
    )


def _candidate_paths(base_url: str) -> tuple[str, ...]:
    path = urlsplit(str(base_url or "")).path.rstrip("/")
    candidates: list[str] = []
    if path:
        candidates.extend((f"{path}/openapi.json", f"{path}/swagger.json"))
    candidates.extend(_DOCUMENT_PATHS)
    return tuple(dict.fromkeys(candidates))


def _origin(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


__all__ = ["discover_video_openapi", "extract_workflow_inputs"]
