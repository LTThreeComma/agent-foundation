"""Memory mounts accepted at input boundaries, before canonical identity resolution."""

from typing import Annotated

from pydantic import Field

from a13n_service.resources.memories.schemas import MAX_MOUNTS, MemoryMount
from a13n_service.resources.memories.tables import MemoryRow
from a13n_service.resources.references import Reference, ReferenceBatch

MemoryMountInput = MemoryMount[Reference]


MemoryMountsInput = Annotated[tuple[MemoryMountInput, ...], Field(max_length=MAX_MOUNTS)]


def mount_ids(batch: ReferenceBatch, mount: MemoryMountInput | MemoryMount) -> MemoryMount:
    return MemoryMount.model_validate(
        {**mount.model_dump(exclude={"memory"}), "memory": {"id": batch.id(MemoryRow, mount.memory)}}
    )
