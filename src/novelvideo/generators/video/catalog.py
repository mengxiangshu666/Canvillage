"""Validated registry for video model capability declarations."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .capabilities import ModelCapability, validate_catalog_key


SCHEMA_VERSION = "video-model-catalog.v1"


class VideoModelCatalogError(ValueError):
    """Base error for invalid catalogs and unsafe model selection."""


class DuplicateModelKeyError(VideoModelCatalogError):
    """A model id or alias is claimed by more than one model."""


class UnknownVideoModelError(VideoModelCatalogError):
    """No model or alias has the requested exact key."""


class VideoModelUnavailableError(VideoModelCatalogError):
    """The requested model exists but is not enabled."""


class VideoModelRegistry:
    """Read-only exact-match index over declared model capabilities."""

    __slots__ = ("_capabilities", "_catalog_revision", "_lookup")

    def __init__(
        self,
        capabilities: Iterable[ModelCapability],
        *,
        catalog_revision: str | None = None,
    ) -> None:
        models = tuple(capabilities)
        for capability in models:
            if not isinstance(capability, ModelCapability):
                raise VideoModelCatalogError("registry entries must be ModelCapability instances")

        revisions = {item.catalog_revision for item in models}
        if len(revisions) > 1:
            raise VideoModelCatalogError("registry contains mixed catalog revisions")
        inferred_revision = next(iter(revisions), None)
        if catalog_revision is not None and (
            not isinstance(catalog_revision, str) or not catalog_revision.strip()
        ):
            raise VideoModelCatalogError("catalog_revision must be a non-empty string")
        if not models and catalog_revision is None:
            raise VideoModelCatalogError("empty registry requires an explicit catalog_revision")
        if catalog_revision is not None and inferred_revision not in (None, catalog_revision):
            raise VideoModelCatalogError("registry catalog_revision does not match its models")

        lookup: dict[str, ModelCapability] = {}
        for capability in models:
            for key in (capability.model_id, *capability.aliases):
                if key in lookup:
                    owner = lookup[key].model_id
                    raise DuplicateModelKeyError(
                        f"video model key {key!r} is claimed by both "
                        f"{owner!r} and {capability.model_id!r}"
                    )
                lookup[key] = capability
        self._capabilities = models
        self._catalog_revision = catalog_revision or inferred_revision
        self._lookup = MappingProxyType(lookup)

    def resolve(self, model_id_or_alias: str) -> ModelCapability:
        """Resolve one enabled model by exact id or alias, without substitution."""

        try:
            validate_catalog_key(model_id_or_alias, "resolve key")
        except ValueError as exc:
            raise UnknownVideoModelError(str(exc)) from None
        capability = self._lookup.get(model_id_or_alias)
        if capability is None:
            raise UnknownVideoModelError(f"unknown video model: {model_id_or_alias!r}")
        if not capability.is_enabled:
            raise VideoModelUnavailableError(
                f"video model {capability.model_id!r} is not enabled "
                f"(lifecycle={capability.lifecycle.value})"
            )
        return capability

    def list_enabled(self) -> tuple[ModelCapability, ...]:
        """Return enabled declarations in stable manifest order."""

        return tuple(capability for capability in self._capabilities if capability.is_enabled)

    @classmethod
    def from_dict(cls, manifest: Mapping[str, Any]) -> VideoModelRegistry:
        """Load a strict JSON-compatible v1 manifest mapping."""

        if not isinstance(manifest, Mapping):
            raise VideoModelCatalogError("video model manifest must be a mapping")
        fields = {"schema_version", "catalog_revision", "models"}
        unknown = set(manifest) - fields
        if unknown:
            raise VideoModelCatalogError(
                f"video model manifest has unknown fields: {sorted(unknown)}"
            )
        missing = fields - set(manifest)
        if missing:
            raise VideoModelCatalogError(
                f"video model manifest missing fields: {sorted(missing)}"
            )
        if manifest["schema_version"] != SCHEMA_VERSION:
            raise VideoModelCatalogError(
                f"unsupported schema_version: {manifest['schema_version']!r}"
            )

        revision = manifest["catalog_revision"]
        if not isinstance(revision, str) or not revision.strip():
            raise VideoModelCatalogError("catalog_revision must be a non-empty string")
        raw_models = manifest["models"]
        if not isinstance(raw_models, list):
            raise VideoModelCatalogError("models must be a list")

        models: list[ModelCapability] = []
        for index, item in enumerate(raw_models):
            try:
                models.append(ModelCapability.from_dict(item))
            except ValueError as exc:
                raise VideoModelCatalogError(f"models[{index}]: {exc}") from exc
        mismatched = [item.model_id for item in models if item.catalog_revision != revision]
        if mismatched:
            raise VideoModelCatalogError(
                f"models do not match catalog_revision {revision!r}: {mismatched}"
            )
        return cls(models, catalog_revision=revision)

    from_manifest = from_dict

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "catalog_revision": self._catalog_revision,
            "models": [item.to_dict() for item in self._capabilities],
        }

    to_manifest = to_dict
