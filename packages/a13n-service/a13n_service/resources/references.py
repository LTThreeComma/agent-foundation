"""Explicit workspace resource addresses and bounded, deduplicated input resolution.

Application operations resolve input references after authorization. Storage and execution retain canonical IDs.
"""

from collections.abc import Iterable
from typing import Annotated, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, TypeAdapter, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError, invalid, not_found
from a13n_service.infra.ids import OBJECT_ID_PATTERN, ObjectId
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

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
) -> dict[Reference, str]:
    """One set read per resource kind, scoped for both address forms. Callers authorize the workspace first."""
    wanted = set(references)
    if not wanted:
        return {}
    ids = {ref.id for ref in wanted if isinstance(ref, IdReference)}
    keys = {ref.key for ref in wanted if isinstance(ref, KeyReference)}
    rows = (
        await session.execute(
            select(table.id, table.key).where(
                table.workspace_id == workspace_id, or_(table.id.in_(ids), table.key.in_(keys))
            )
        )
    ).all()
    by_id = {row.id: row.id for row in rows}
    by_key = {row.key: row.id for row in rows}
    resolved = {}
    for ref in wanted:
        row = by_id.get(ref.id) if isinstance(ref, IdReference) else by_key.get(ref.key)
        if row is None:
            raise not_found(table.KIND, ref.id if isinstance(ref, IdReference) else "@" + ref.key)
        resolved[ref] = row
    return resolved


class ReferenceBatch:
    """All references of one accepted input, deduplicated across its nested selections."""

    def __init__(self) -> None:
        self._wanted: dict[type[KeyedRow], set[Reference]] = {}
        self._rows: dict[type[KeyedRow], dict[Reference, str]] = {}

    def add(self, table: type[KeyedRow], reference: Reference | None) -> None:
        if reference is not None:
            self._wanted.setdefault(table, set()).add(address(reference))

    async def resolve(self, session: AsyncSession, workspace_id: str) -> None:
        for table, wanted in self._wanted.items():
            self._rows[table] = await resolve_many(session, table, workspace_id, wanted)

    def id(self, table: type[KeyedRow], reference: Reference) -> str:
        return self._rows[table][address(reference)]


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


async def resolve_id(
    storage: Storage, actor: Principal, workspace_id: str, table: type[KeyedRow], reference: str
) -> str:
    """Authorize the workspace before resolving a key or validating a query filter's ID."""
    parsed = parse_reference(reference)
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows = await resolve_many(session, table, scope.workspace_id, [parsed])
        return rows[parsed]
