"""The access matrix and its two consumers."""

import pytest

from app.rbac import (
    ALL_ROLES,
    COLLECTION_ROLES,
    UnknownRoleError,
    can_use_sql_rag,
    collections_for,
    qdrant_filter,
    roles_for_collection,
    validate_role,
)

# The matrix exactly as written in the assignment brief.
EXPECTED = {
    "doctor": {"general", "clinical", "nursing"},
    "nurse": {"general", "nursing"},
    "billing_executive": {"general", "billing"},
    "technician": {"general", "equipment"},
    "admin": {"general", "clinical", "nursing", "billing", "equipment"},
}


@pytest.mark.parametrize("role", ALL_ROLES)
def test_collections_match_brief(role):
    assert set(collections_for(role)) == EXPECTED[role]


def test_admin_sees_everything():
    assert set(collections_for("admin")) == set(COLLECTION_ROLES)


@pytest.mark.parametrize(
    "role,forbidden",
    [
        ("nurse", "billing"),
        ("nurse", "clinical"),
        ("nurse", "equipment"),
        ("technician", "clinical"),
        ("technician", "billing"),
        ("billing_executive", "clinical"),
        ("billing_executive", "nursing"),
        ("doctor", "billing"),
        ("doctor", "equipment"),
    ],
)
def test_role_excluded_from_collection(role, forbidden):
    assert forbidden not in collections_for(role)
    assert role not in roles_for_collection(forbidden)


def test_general_is_open_to_all():
    assert set(roles_for_collection("general")) == set(ALL_ROLES)


def test_ingest_and_query_agree():
    """The write side and the read side must derive from the same matrix."""
    for collection in COLLECTION_ROLES:
        for role in roles_for_collection(collection):
            assert collection in collections_for(role)


@pytest.mark.parametrize(
    "role,expected", [("billing_executive", True), ("admin", True), ("nurse", False),
                      ("doctor", False), ("technician", False)]
)
def test_sql_rag_permission(role, expected):
    assert can_use_sql_rag(role) is expected


def test_unknown_role_rejected():
    for func in (validate_role, collections_for, can_use_sql_rag, qdrant_filter):
        with pytest.raises(UnknownRoleError):
            func("superuser")


def test_filter_targets_access_roles_field():
    condition = qdrant_filter("nurse").must[0]
    assert condition.key == "access_roles"
    assert condition.match.any == ["nurse"]
