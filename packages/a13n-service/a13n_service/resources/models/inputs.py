"""Explicit model references for media understanding."""

from pydantic import BaseModel, ConfigDict

from a13n_service.resources.models.schemas import MEDIA_KINDS, MediaUnderstandingSelection
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.references import Reference, ReferenceBatch


class MediaSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    image: Reference | None = None
    video: Reference | None = None
    audio: Reference | None = None


def collect_media(batch: ReferenceBatch, media: MediaSelectionInput) -> None:
    for kind in MEDIA_KINDS:
        batch.add(ModelRow, getattr(media, kind))


def media_ids(batch: ReferenceBatch, media: MediaSelectionInput) -> MediaUnderstandingSelection:
    return MediaUnderstandingSelection.model_validate(
        {kind: batch.id(ModelRow, ref) for kind in MEDIA_KINDS if (ref := getattr(media, kind)) is not None}
    )
