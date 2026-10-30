"""Password, PIN, token and IP hashing.

`SECURITY.md §2.1`: Argon2id at `time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`,
16-byte salt, 32-byte hash. The parameters are stored *inside* the hash string, so
raising them later re-hashes transparently on next login rather than invalidating
anyone's password.

A server-side **pepper** is mixed in before hashing. It raises the cost of an offline
attack on a stolen database because the attacker needs the application secret too. It
**cannot be rotated** without invalidating every password — documented here so nobody
tries.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: `SECURITY.md §2.1`. Tuned at deploy time to ~250 ms on the production instance and
#: recorded in DEPLOYMENT.md; these are the documented starting values.
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,  # Argon2id
)

#: Minimum length, with no composition rules and no forced rotation: those push counter
#: staff toward sticky notes, which is a worse outcome than a slightly shorter password
#: behind lockout and rate limiting (`SECURITY.md §2.1`).
MIN_PASSWORD_LENGTH = 10

#: A short, obvious blocklist. Not a dictionary check — the realistic threat here is
#: `password1234`, not a targeted attack on one cashier.
_BLOCKED_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password12",
        "password123",
        "password1234",
        "1234567890",
        "qwertyuiop",
        "letmein123",
        "nationalauto",
        "autospares",
        "changeme123",
        "admin12345",
        "welcome123",
    }
)


class WeakPassword(ValueError):
    """Raised with a message that is safe to show a counter user."""


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"Use at least {MIN_PASSWORD_LENGTH} characters. A short phrase works well."
        )
    normalised = password.strip().lower()
    if normalised in _BLOCKED_PASSWORDS:
        raise WeakPassword("That password is too easy to guess. Please choose another.")
    if len(set(normalised)) <= 2:
        raise WeakPassword("That password repeats too few characters. Please choose another.")


def _peppered(secret: str, pepper: str) -> str:
    """HMAC the secret with the pepper before Argon2.

    Pre-hashing with HMAC also removes Argon2's input-length ceiling, so a long
    passphrase is never silently truncated.
    """
    return hmac.new(pepper.encode("utf-8"), secret.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_password(password: str, *, pepper: str) -> str:
    return _HASHER.hash(_peppered(password, pepper))


def verify_password(password: str, stored_hash: str, *, pepper: str) -> bool:
    """Constant-time verification. Returns False rather than raising on a bad hash,
    because a corrupt stored value must not look different from a wrong password."""
    try:
        return _HASHER.verify(stored_hash, _peppered(password, pepper))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current policy."""
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


@functools.lru_cache(maxsize=1)
def decoy_password_hash() -> str:
    """A genuinely valid Argon2id hash of a value nobody knows.

    Login verifies against this when the username does not exist, so a missing user and
    a wrong password take the same time. A hand-written constant would not work: an
    unparseable hash raises immediately, and that fast path is exactly the timing signal
    this removes. Computed once per process.
    """
    return _HASHER.hash(secrets.token_urlsafe(32))


def hash_pin(pin: str, *, pepper: str) -> str:
    """A PIN is low-entropy, so it gets the same Argon2 cost as a password.

    A PIN is never an alternative to a password from a cold start — it only works on a
    device that already holds a valid refresh family (`SECURITY.md §2.3`).
    """
    return _HASHER.hash(_peppered(f"pin:{pin}", pepper))


def verify_pin(pin: str, stored_hash: str, *, pepper: str) -> bool:
    try:
        return _HASHER.verify(stored_hash, _peppered(f"pin:{pin}", pepper))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_refresh_token() -> str:
    """Opaque, 256 bits of entropy. Returned to the client once, never stored."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """SHA-256 is correct here, unlike for passwords.

    The token is already 256 random bits, so there is nothing to brute-force and no
    reason to pay Argon2's cost on every refresh. What matters is that a database dump
    cannot be replayed as a session, which the hash achieves.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_ip(ip: str | None, *, pepper: str) -> str | None:
    """HMAC so rate-limit buckets work without storing a raw address
    (`SECURITY.md §2.4`). Rotating this pepper makes old hashes uncorrelatable, which
    is acceptable."""
    if not ip:
        return None
    return hmac.new(pepper.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()
