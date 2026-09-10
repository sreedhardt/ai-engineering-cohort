"""Token issuance and the trust boundary around role claims."""

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import DEMO_USERS, authenticate, current_user, issue_token
from app.config import settings


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_every_role_has_a_demo_account():
    assert {u.role for u in DEMO_USERS.values()} == {
        "doctor", "nurse", "billing_executive", "technician", "admin"
    }


def test_valid_login():
    user = authenticate("nurse.priya", "nurse123")
    assert user is not None and user.role == "nurse"


@pytest.mark.parametrize(
    "username,password",
    [("nurse.priya", "wrong"), ("nobody", "nurse123"), ("", ""), ("nurse.priya", "")],
)
def test_rejected_logins(username, password):
    assert authenticate(username, password) is None


def test_passwords_are_not_stored_in_clear():
    user = DEMO_USERS["nurse.priya"]
    assert "nurse123" not in user.password_hash
    assert len(user.password_hash) == 64


def test_round_trip_preserves_role():
    token, _ = issue_token(DEMO_USERS["dr.mehta"])
    assert current_user(_creds(token)).role == "doctor"


def test_tampered_role_is_rejected():
    """Editing "nurse" to "admin" in the payload must fail signature checks."""
    token, _ = issue_token(DEMO_USERS["nurse.priya"])
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    payload["role"] = "admin"
    forged = jwt.encode(payload, "attacker-guessed-secret", algorithm="HS256")

    with pytest.raises(HTTPException) as exc:
        current_user(_creds(forged))
    assert exc.value.status_code == 401


def test_unsigned_token_is_rejected():
    """The classic alg=none downgrade."""
    forged = jwt.encode({"sub": "x", "role": "admin"}, key="", algorithm="none")
    with pytest.raises(HTTPException):
        current_user(_creds(forged))


def test_missing_credentials_rejected():
    with pytest.raises(HTTPException) as exc:
        current_user(None)
    assert exc.value.status_code == 401
