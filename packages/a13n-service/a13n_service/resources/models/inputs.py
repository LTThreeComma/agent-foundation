"""Explicit model references for media understanding."""

from a13n_service.resources.models.schemas import MEDIA_KINDS, MediaUnderstandingSelection
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.references import Reference, ReferenceBatch

MediaSelectionInput = MediaUnderstandingSelection[Reference]


def collect_media(batch: ReferenceBatch, media: MediaSelectionInput | MediaUnderstandingSelection) -> None:
    for kind in MEDIA_KINDS:
        batch.add(ModelRow, getattr(media, kind))


def media_ids(
    batch: ReferenceBatch, media: MediaSelectionInput | MediaUnderstandingSelection
) -> MediaUnderstandingSelection:
    return MediaUnderstandingSelection.model_validate(
        {kind: {"id": batch.id(ModelRow, ref)} for kind in MEDIA_KINDS if (ref := getattr(media, kind)) is not None}
    )
