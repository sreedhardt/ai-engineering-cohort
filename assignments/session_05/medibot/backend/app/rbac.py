"""The access matrix — the single source of truth for who may see what.

Two consumers import this module:

  * ``ingest``   stamps ``access_roles`` onto every chunk it writes to Qdrant
  * ``retrieval`` filters on that same field at query time

Because both sides derive from ``COLLECTION_ROLES``, the write side and the read
side cannot drift apart. Editing this dict is the only way to change access.
"""

from qdrant_client import models

ALL_ROLES = ("doctor", "nurse", "billing_executive", "technician", "admin")

# collection -> roles permitted to retrieve its chunks
COLLECTION_ROLES: dict[str, list[str]] = {
    "general": ["doctor", "nurse", "billing_executive", "technician", "admin"],
    "clinical": ["doctor", "admin"],
    "nursing": ["nurse", "doctor", "admin"],
    "billing": ["billing_executive", "admin"],
    "equipment": ["technician", "admin"],
}

# SQL RAG exposes operational figures, so it is limited to analytical roles.
SQL_RAG_ROLES = frozenset({"billing_executive", "admin"})

# Human-readable labels used in refusal messages.
COLLECTION_LABELS = {
    "general": "general hospital policy",
    "clinical": "clinical",
    "nursing": "nursing",
    "billing": "billing and insurance",
    "equipment": "equipment",
}


class UnknownRoleError(ValueError):
    """Raised when a role is not part of the access matrix."""


def validate_role(role: str) -> str:
    if role not in ALL_ROLES:
        raise UnknownRoleError(f"unknown role: {role!r}")
    return role


def roles_for_collection(collection: str) -> list[str]:
    """Roles allowed to see a collection. Used at ingestion time."""
    try:
        return list(COLLECTION_ROLES[collection])
    except KeyError:
        raise ValueError(f"unknown collection: {collection!r}") from None


def collections_for(role: str) -> list[str]:
    """Collections a role may retrieve from. Used by /collections and the UI."""
    validate_role(role)
    return [c for c, roles in COLLECTION_ROLES.items() if role in roles]


def can_use_sql_rag(role: str) -> bool:
    validate_role(role)
    return role in SQL_RAG_ROLES


def qdrant_filter(role: str) -> models.Filter:
    """The RBAC filter applied to *every* retrieval query.

    Qdrant evaluates this server-side, so chunks outside the role's collections
    are never returned to the application and therefore never reach the LLM.
    """
    validate_role(role)
    return models.Filter(
        must=[
            models.FieldCondition(
                key="access_roles",
                match=models.MatchAny(any=[role]),
            )
        ]
    )
