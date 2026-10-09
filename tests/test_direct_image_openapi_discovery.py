from novelvideo.generators.direct_image_openapi_discovery import (
    extract_image_aspect_ratios,
)


def test_extracts_declared_ratios_from_image_schema_refs_only() -> None:
    document = {
        "openapi": "3.0.0",
        "paths": {
            "/v1/images/generations": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ImageRequest"}
                            }
                        }
                    }
                }
            },
            "/v1/videos/generations": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "aspect_ratio": {"enum": ["2:1", "1:2"]}
                                    }
                                }
                            }
                        }
                    }
                }
            },
        },
        "components": {
            "schemas": {
                "ImageRequest": {
                    "properties": {
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "4:5", "5:4", "21:9", "2.39:1", "1:2", "2:1", "5:3"],
                        }
                    }
                }
            }
        },
    }

    assert extract_image_aspect_ratios(document) == [
        "1:1",
        "16:9",
        "9:16",
        "4:3",
        "3:4",
        "3:2",
        "2:3",
        "4:5",
        "5:4",
        "21:9",
        "2.39:1",
        "1:2",
        "2:1",
        "5:3",
    ]


def test_openapi_without_image_ratio_enum_does_not_invent_options() -> None:
    assert extract_image_aspect_ratios(
        {
            "paths": {
                "/v1/images/generations": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "properties": {
                                            "size": {"enum": ["1024x1024", "1536x1024"]}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    ) == []


def test_schema_auth_uses_the_selected_protocol(monkeypatch) -> None:
    from novelvideo.generators import direct_image_openapi_discovery as discovery

    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 404
        content = b""

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, url: str, *, headers: dict[str, str]):
            captured.setdefault("first_url", url)
            captured.setdefault("headers", headers)
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = discovery.discover_image_openapi(
        base_url="https://image.example/v1",
        api_key="test-key",
        protocol="gemini-image",
    )

    assert result["status"] == "not-found"
    assert captured["headers"] == {
        "Authorization": "Bearer test-key",
        "Accept": "application/json",
    }
