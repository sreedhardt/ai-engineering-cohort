"""Authentication: credentials in, signed role-tagged token out.

This stands in for a real identity provider. Only the *user store* is a stub —
the token mechanics are real, so the role a request runs under is always derived
server-side from a verified signature and can never be supplied by the client.

Swapping in OIDC later means replacing ``authenticate()``; nothing downstream of
``current_user()`` changes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.rbac import validate_role

_PBKDF2_ROUNDS = 120_000


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS
    )
    return digest.hex()


@dataclass(frozen=True)
class DemoUser:
    username: str
    display_name: str
    role: str
    department: str
    salt: str
    password_hash: str


def _demo_user(
    username: str, password: str, display_name: str, role: str, department: str
) -> DemoUser:
    # Salts are fixed per demo user so the credentials in the README stay valid
    # across restarts. A real store would generate them per registration.
    salt = hashlib.sha256(username.encode()).hexdigest()[:16]
    return DemoUser(
        username=username,
        display_name=display_name,
        role=role,
        department=department,
        salt=salt,
        password_hash=_hash_password(password, salt),
    )


# Demo credentials — documented in the README, one per role.
DEMO_USERS: dict[str, DemoUser] = {
    u.username: u
    for u in [
        _demo_user("dr.mehta", "doctor123", "Dr. Anjali Mehta", "doctor", "Clinical"),
        _demo_user("nurse.priya", "nurse123", "Priya Nair", "nurse", "Clinical"),
        _demo_user(
            "billing.ravi",
            "billing123",
            "Ravi Kumar",
            "billing_executive",
            "Billing & Insurance",
        ),
        _demo_user(
            "tech.anand", "tech123", "Anand Rao", "technician", "Medical Equipment"
        ),
        _demo_user("admin.sys", "admin123", "System Admin", "admin", "Executive / IT"),
    ]
}


def authenticate(username: str, password: str) -> DemoUser | None:
    """Verify credentials. Returns the user, or None on any failure."""
    user = DEMO_USERS.get(username)
    if user is None:
        # Hash anyway so a missing username costs the same time as a wrong
        # password, leaving no timing signal for user enumeration.
        _hash_password(password, "decoy_salt_000")
        return None
    candidate = _hash_password(password, user.salt)
    if not hmac.compare_digest(candidate, user.password_hash):
        return None
    return user


def issue_token(user: DemoUser) -> tuple[str, int]:
    """Sign a token carrying the role claim. Returns (token, expires_in_seconds)."""
    ttl = timedelta(minutes=settings.jwt_ttl_minutes)
    now = datetime.now(UTC)
    payload = {
        "sub": user.username,
        "role": user.role,
        "name": user.display_name,
        "department": user.department,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(8),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


@dataclass(frozen=True)
class Principal:
    """The verified identity a request runs under."""

    username: str
    role: str
    display_name: str
    department: str


_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated. Sign in via /login and send the token as a Bearer header.",
    headers={"WWW-Authenticate": "Bearer"},
)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """FastAPI dependency: the only supported way to learn a caller's role.

    A tampered payload (e.g. "nurse" edited to "admin") fails signature
    verification here and never reaches the retrieval layer.
    """
    if credentials is None:
        raise _UNAUTHORIZED
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.InvalidTokenError:
        raise _UNAUTHORIZED from None

    try:
        role = validate_role(payload.get("role", ""))
    except ValueError:
        raise _UNAUTHORIZED from None

    return Principal(
        username=payload.get("sub", ""),
        role=role,
        display_name=payload.get("name", ""),
        department=payload.get("department", ""),
    )
