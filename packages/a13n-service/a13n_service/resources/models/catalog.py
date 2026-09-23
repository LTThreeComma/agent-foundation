"""The models a model provider type is known to serve: the Harness's official model and pricing catalogues.

Both functions block (the pricing catalogue may rebuild its snapshot); call them from a worker thread.
"""

from a13n_harness.model_catalog import OfficialModelEntry, get_official_model_catalog
from a13n_harness.pricing import PricingCatalog, get_current_pricing_catalog
from a13n_harness.providers.model.definition import ModelProviderDefinition

from a13n_service.resources.models.schemas import CatalogModel


def known_models(definition: ModelProviderDefinition) -> list[CatalogModel]:
    pricing = get_current_pricing_catalog()
    candidates = (_catalog_model(entry, pricing) for entry in get_official_model_catalog().entries)
    return [model for model in candidates if serves(definition, model)]


def known_model(key: str) -> CatalogModel | None:
    entry = get_official_model_catalog().get(key)
    return None if entry is None else _catalog_model(entry, get_current_pricing_catalog())


def serves(definition: ModelProviderDefinition, model: CatalogModel) -> bool:
    # Official keys name Pydantic AI channels ("google-gla"); definitions, like pricing, name vendors ("google").
    channels = {model.key.partition(":")[0], *([model.pricing.provider] if model.pricing else [])}
    return not channels.isdisjoint(definition.catalog_providers)


def _catalog_model(entry: OfficialModelEntry, pricing: PricingCatalog) -> CatalogModel:
    channel, _, name = entry.key.partition(":")
    price = pricing.resolve(name, provider=channel)
    return CatalogModel(
        key=entry.key,
        model_name=name,
        characteristics=entry.characteristics,
        # Execution looks a price up by the model's own name; the pricing catalogue may have matched it to a
        # related entry, such as the base model of an experimental variant.
        pricing=None if price is None else price.model_copy(update={"model": name}),
        source_url=str(entry.source_url),
    )
