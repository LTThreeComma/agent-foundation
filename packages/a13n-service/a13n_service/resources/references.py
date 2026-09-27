"""Explicit workspace resource addresses and bounded, deduplicated input resolution.

Only transport/input code carries references. Persisted relationships and execution use canonical IDs.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, TypeAdapter, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError, invalid, not_found
from a13n_service.infra.ids import OBJECT_ID_PATTERN, ObjectId

# Resource kinds retain their own key rules; this is the union of their address alphabets.
ReferenceKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$")]
PathReference = Annotated[
    str, StringConstraints(pattern=rf"(?:{OBJECT_ID_PATTERN})|(?:^@[A-Za-z0-9_][A-Za-z0-9_-]{{0,127}}$)")
]


class IdReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: ObjectId


class KeyReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: ReferenceKey


type Reference = IdReference | KeyReference
_REFERENCE = TypeAdapter(Reference)


class KeyedRow(Protocol):
    KIND: ClassVar[str]
    id: Mapped[str]
    workspace_id: Mapped[str]
    key: Mapped[str]


def parse_reference(value: str) -> Reference:
    """A scalar address: plain means ID, @ means key. There is no fallback."""
    try:
        return _REFERENCE.validate_python({"key": value[1:]} if value.startswith("@") else {"id": value})
    except ValidationError:
        raise invalid("reference", "expected an ID or @key") from None


async def resolve_many[R: KeyedRow](
    session: AsyncSession, table: type[R], workspace_id: str, references: Iterable[Reference]
) -> dict[Reference, R]:
    """One set read per resource kind, scoped for both address forms. Callers authorize the workspace first."""
    wanted = set(references)
    if not wanted:
        return {}
    ids = {ref.id for ref in wanted if isinstance(ref, IdReference)}
    keys = {ref.key for ref in wanted if isinstance(ref, KeyReference)}
    rows = (
        await session.scalars(
            select(table).where(table.workspace_id == workspace_id, or_(table.id.in_(ids), table.key.in_(keys)))
        )
    ).all()
    by_id = {row.id: row for row in rows}
    by_key = {row.key: row for row in rows}
    resolved = {}
    for ref in wanted:
        row = by_id.get(ref.id) if isinstance(ref, IdReference) else by_key.get(ref.key)
        if row is None:
            raise not_found(table.KIND, ref.id if isinstance(ref, IdReference) else "@" + ref.key)
        resolved[ref] = row
    return resolved


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    id: str
    key: str


class ReferenceBatch:
    """All references of one accepted input, deduplicated across its nested selections."""

    def __init__(self) -> None:
        self._wanted: dict[type[KeyedRow], set[Reference]] = {}
        self._rows: dict[type[KeyedRow], dict[Reference, ResolvedReference]] = {}

    def add(self, table: type[KeyedRow], reference: Reference | None) -> None:
        if reference is not None:
            self._wanted.setdefault(table, set()).add(address(reference))

    async def resolve(self, session: AsyncSession, workspace_id: str) -> None:
        for table, wanted in self._wanted.items():
            rows = await resolve_many(session, table, workspace_id, wanted)
            self._rows[table] = {ref: ResolvedReference(row.id, row.key) for ref, row in rows.items()}

    def row(self, table: type[KeyedRow], reference: Reference) -> ResolvedReference:
        return self._rows[table][address(reference)]

    def id(self, table: type[KeyedRow], reference: Reference) -> str:
        return self.row(table, reference).id


def address(selection: Reference) -> Reference:
    """Discard a typed selection's settings/revision, retaining only its explicit address."""
    return IdReference(id=selection.id) if isinstance(selection, IdReference) else KeyReference(key=selection.key)


def resolved_model[M: BaseModel](model: type[M], values: object) -> M:
    """Resolved IDs may reveal duplicate selections that used different address forms."""
    try:
        return model.model_validate(values)
    except ValidationError as error:
        raise ServiceError(
            "invalid_argument",
            "Invalid resource selection",
            {
                "fields": [
                    {"field": ".".join(str(item) for item in issue["loc"]), "reason": issue["type"]}
                    for issue in error.errors(include_input=False, include_context=False)[:20]
                ]
            },
        ) from None


async def resolve_id(storage: Storage, workspace_id: str, table: type[KeyedRow], reference: str) -> str:
    """Resolve a path or query address after the caller authorizes the workspace."""
    parsed = parse_reference(reference)
    async with short_session(storage) as session:
        rows = await resolve_many(session, table, workspace_id, [parsed])
        return rows[parsed].id
