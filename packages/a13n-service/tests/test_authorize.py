"""Scope truth table and durable authority ceilings shared by every caller."""

from dataclasses import replace

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.tenancy.authorize import (
    ExecutionAuthority,
    Grant,
    Principal,
    Scope,
    allowed_verbs,
    authorize,
    execution_authority,
)


@pytest.mark.parametrize(
    ("grant_scope", "resource", "expected"),
    [
        (None, Scope("org_a", "ws_a"), {"read", "run", "write", "admin"}),
        ("ws_a", Scope("org_a", "ws_a"), {"read", "run", "write", "admin"}),
        ("ws_b", Scope("org_a", "ws_a"), set()),
        (None, Scope("org_a"), {"read", "run", "write", "admin"}),
        ("ws_a", Scope("org_a"), {"read", "run"}),
        (None, Scope("org_b", "ws_b"), set()),
    ],
)
def test_grant_scope_rule(grant_scope, resource, expected):
    principal = Principal("usr_a", "user", (Grant("org_a", grant_scope, "admin"),))
    assert allowed_verbs(principal, resource) == expected


@pytest.mark.parametrize(
    "role,expected", [("viewer", {"read"}), ("runner", {"read", "run"}), ("builder", {"read", "run", "write"})]
)
def test_roles_union_without_changing_scope(role, expected):
    principal = Principal("usr_a", "user", (Grant("org_a", "ws_a", role), Grant("org_b", None, "admin")))
    assert allowed_verbs(principal, Scope("org_a", "ws_a")) == expected
    assert allowed_verbs(principal, Scope("org_a")) == expected & {"read", "run"}


def test_workspace_key_never_inherits_cross_workspace_or_shared_mutation_authority():
    principal = Principal(
        "usr_a", "user", (Grant("org_a", None, "admin"), Grant("org_b", None, "admin")), Scope("org_a", "ws_a")
    )
    assert allowed_verbs(principal, Scope("org_a", "ws_a")) == {"read", "run", "write", "admin"}
    assert allowed_verbs(principal, Scope("org_a", "ws_b")) == set()
    assert allowed_verbs(principal, Scope("org_b", "ws_a")) == set()
    assert allowed_verbs(principal, Scope("org_a")) == {"read", "run"}
    with pytest.raises(ServiceError, match="cannot perform"):
        authorize(principal, Scope("org_a"), "admin")
    # A malformed global confinement can never turn a key into an organization credential.
    assert allowed_verbs(replace(principal, confinement=Scope("org_a")), Scope("org_a")) == set()


def test_unknown_roles_fail_closed_even_if_another_grant_would_allow():
    principal = Principal("usr_a", "user", (Grant("org_a", None, "admin"), Grant("org_a", "ws_a", "removed_role")))
    with pytest.raises(ServiceError, match="unknown role"):
        authorize(principal, Scope("org_a", "ws_a"), "run")


def test_disabled_principal_and_removed_grants_stop_accepted_authority():
    scope = Scope("org_a", "ws_a")
    principal = Principal("usr_a", "user", (Grant("org_a", None, "builder"),))
    authority = execution_authority(principal, scope)
    for current in (replace(principal, active=False), replace(principal, grants=())):
        with pytest.raises(ServiceError):
            authorize(current, scope, "run", authority=authority)


def test_persisted_ceiling_cannot_widen_when_current_grants_increase():
    scope = Scope("org_a", "ws_a")
    principal = Principal("usr_a", "user", (Grant("org_a", "ws_a", "runner"),))
    accepted = execution_authority(principal, scope)
    restored = ExecutionAuthority.model_validate_json(accepted.model_dump_json())
    upgraded = replace(principal, grants=(Grant("org_a", None, "admin"),))
    authorize(upgraded, scope, "run", authority=restored)
    authorize(upgraded, Scope("org_a"), "run", authority=restored)
    for target, verb in [(scope, "write"), (Scope("org_a", "ws_b"), "run"), (Scope("org_a"), "admin")]:
        with pytest.raises(ServiceError, match="delegation"):
            authorize(upgraded, target, verb, authority=restored)
    with pytest.raises(ServiceError, match="delegation"):
        authorize(replace(upgraded, id="usr_b"), scope, "run", authority=restored)
