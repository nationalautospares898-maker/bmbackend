"""Access-token minting and verification.

`SECURITY.md §2.2`: HS256 JWT, 15-minute lifetime, claims `sub`, `business_id`,
`branch_id`, `counter_id`, `device_id`, `role`, `permissions`, `jti`, `exp`, `iat`.

Access tokens are **not individually revocable** — the 15-minute lifetime is the bound,
and device revocation stops refresh immediately. The worst case after a revoke is 15
minutes of read access. That is an accepted risk, recorded in `SECURITY.md §15`, not an
oversight: introspecting a token on every request would add a database round trip to the
hot path of a busy counter.

A **previous signing key** is accepted during a rotation overlap so a key change does
not sign everyone out mid-sale.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from jwt.exceptions import InvalidTokenError

from bm.domain.shared.errors import TokenExpired

_ALGORITHM = "HS256"
_ISSUER = "bm"


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID | None
    counter_id: uuid.UUID | None
    device_id: uuid.UUID | None
    role: str
    permissions: frozenset[str]
    token_id: str
    business_timezone: str
    full_name: str
    must_change_password: bool


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def mint_access_token(
    *,
    signing_key: str,
    ttl_seconds: int,
    user_id: uuid.UUID,
    business_id: uuid.UUID,
    branch_id: uuid.UUID | None,
    counter_id: uuid.UUID | None,
    device_id: uuid.UUID | None,
    role: str,
    permissions: frozenset[str] | set[str],
    business_timezone: str,
    full_name: str,
    must_change_password: bool,
    issued_at: datetime | None = None,
) -> tuple[str, str]:
    """Return `(token, jti)`.

    The `jti` is returned separately so the caller can record it on the audit row for
    the login, which is what makes "which session did this" answerable.
    """
    now = issued_at or datetime.now(UTC)
    token_id = str(uuid.uuid4())
    payload = {
        "iss": _ISSUER,
        "sub": str(user_id),
        "jti": token_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "business_id": str(business_id),
        "branch_id": str(branch_id) if branch_id else None,
        "counter_id": str(counter_id) if counter_id else None,
        "device_id": str(device_id) if device_id else None,
        "role": role,
        # Sorted so a token is byte-stable for the same grant, which makes a diff in a
        # support conversation meaningful.
        "permissions": sorted(str(p) for p in permissions),
        "business_timezone": business_timezone,
        "full_name": full_name,
        "must_change_password": must_change_password,
    }
    return jwt.encode(payload, signing_key, algorithm=_ALGORITHM), token_id


def verify_access_token(
    token: str, *, signing_key: str, previous_key: str | None = None
) -> AccessTokenClaims:
    """Decode and validate. Raises `TokenExpired` for any unusable token.

    Every failure mode collapses to the same error on purpose: telling a caller
    *why* their token was rejected ("bad signature" versus "expired") is information
    they cannot act on differently, and the client's handling is identical — refresh
    once, then prompt for login.
    """
    keys = [signing_key] + ([previous_key] if previous_key else [])
    last_error: InvalidTokenError | None = None

    for key in keys:
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                issuer=_ISSUER,
                options={"require": ["exp", "iat", "sub", "jti"]},
            )
        except InvalidTokenError as exc:
            last_error = exc
            continue

        business_id = _uuid_or_none(payload.get("business_id"))
        user_id = _uuid_or_none(payload.get("sub"))
        if business_id is None or user_id is None:
            raise TokenExpired()

        return AccessTokenClaims(
            user_id=user_id,
            business_id=business_id,
            branch_id=_uuid_or_none(payload.get("branch_id")),
            counter_id=_uuid_or_none(payload.get("counter_id")),
            device_id=_uuid_or_none(payload.get("device_id")),
            role=str(payload.get("role", "")),
            permissions=frozenset(payload.get("permissions") or ()),
            token_id=str(payload.get("jti", "")),
            business_timezone=str(payload.get("business_timezone") or "Asia/Kolkata"),
            full_name=str(payload.get("full_name") or ""),
            must_change_password=bool(payload.get("must_change_password")),
        )

    raise TokenExpired() from last_error
