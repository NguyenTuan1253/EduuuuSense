"""Security primitives: password hashing, JWT creation/verification, one-time tokens.

This module is the single source of security primitives for EduSense. It lives in
the ``core`` layer: no database access, no routers, no repository imports. Services
(M1-02/M1-03) consume these functions.

Decisions implemented here:
- Password hashing uses Argon2id via argon2-cffi (DECISION-CONFIRMED-01). Note:
  an older QAS-03 mentions "bcrypt"; the confirmed architecture decision and the
  backlog task title take precedence (Argon2id).
- JWTs are signed with HS256 and the algorithm is always pinned from
  ``settings.algorithm``; the ``alg`` header of an incoming token is never trusted.
- A single module-level exception ``InvalidTokenError`` is raised for ANY token
  failure (bad signature, expired, missing claims, wrong type). Callers catch
  exactly this one type and map it to HTTP 401.
- Single-use verification tokens (email verification / password reset): only a
  SHA-256 hash of the raw token is ever stored; the raw token goes to the email.
  Comparison uses ``hmac.compare_digest`` (constant time).

This module never logs tokens, passwords, or secrets.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from app.core.config import settings

TokenType = Literal["access", "refresh"]

# Required JWT claims; presence is enforced on every decode.
_REQUIRED_CLAIMS: tuple[str, ...] = ("sub", "type", "iat", "exp", "jti")

# Argon2id defaults of argon2-cffi 23.1.0: time_cost=3, memory_cost=64 MiB,
# parallelism=4, hash_len=32, salt_len=16, type=Type.ID (Argon2id).
_password_hasher = PasswordHasher()


class InvalidTokenError(Exception):
    """Raised when a JWT fails signature, expiry, claim, or type validation.

    This is the ONLY exception raised by :func:`decode_token`, so the service
    layer can map any token failure to a single HTTP 401 response.
    """


def hash_password(plain: str) -> str:
    """Hash a plaintext password with Argon2id and return the encoded hash string.

    Each call generates a unique random salt, so hashing the same password twice
    yields different hashes. The returned string is a PHC-format Argon2id hash
    (e.g. ``$argon2id$v=19$m=65536,t=3,p=4$...``) suitable for storage.
    """
    return _password_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if the plaintext password matches the stored Argon2id hash.

    Any failure (mismatch, malformed stored hash, or verification error) returns
    False instead of raising, so callers can treat every failure uniformly.
    Note: in argon2-cffi 23.1.0 ``InvalidHashError`` is not an ``Argon2Error``
    subclass, so both must be caught explicitly. Invalid argument types (e.g.
    ``None``) are programming errors that still raise; only argon2
    verification-related failures return False.
    """
    try:
        return _password_hasher.verify(hashed, plain)
    except (Argon2Error, InvalidHashError):
        return False


def create_access_token(subject: str) -> str:
    """Create a signed access JWT for ``subject`` with access-type claims.

    Claims: ``sub`` (subject), ``type="access"``, ``iat``, ``exp``
    (``iat + settings.access_token_expire_minutes``), and a random ``jti``.

    Raises:
        ValueError: If ``subject`` is empty or None.
    """
    return _create_token(
        subject=subject,
        token_type="access",
        lifetime_seconds=settings.access_token_expire_minutes * 60,
    )


def create_refresh_token(subject: str) -> str:
    """Create a signed refresh JWT for ``subject`` with refresh-type claims.

    Claims: ``sub`` (subject), ``type="refresh"``, ``iat``, ``exp``
    (``iat + settings.refresh_token_expire_days``), and a random ``jti``.

    Raises:
        ValueError: If ``subject`` is empty or None.
    """
    return _create_token(
        subject=subject,
        token_type="refresh",
        lifetime_seconds=settings.refresh_token_expire_days * 24 * 60 * 60,
    )


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode and validate a JWT, returning its claims payload.

    Validation performed:
    - Signature against ``settings.secret_key`` with the algorithm pinned to
      ``settings.algorithm`` (the token's ``alg`` header is ignored).
    - Expiry (``exp``) and numeric ``iat`` per PyJWT.
    - Presence and types of all required claims: ``sub``, ``type``, ``iat``,
      ``exp``, ``jti``.
    - If ``expected_type`` is given, the ``type`` claim must match it, so callers
      can require an access token or a refresh token specifically.

    Raises:
        InvalidTokenError: On any failure (bad signature, expired, missing or
            invalid claims, or a ``type`` mismatch with ``expected_type``).
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            key=settings.secret_key,
            algorithms=[settings.algorithm],
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except jwt.InvalidTokenError as exc:
        # PyJWT exception messages never embed the token value, only a reason
        # (e.g. "Signature has expired"), so this is safe to expose upward.
        raise InvalidTokenError(f"Token validation failed: {exc}") from exc

    token_type = payload.get("type")
    if not isinstance(token_type, str) or token_type not in ("access", "refresh"):
        raise InvalidTokenError("Token has an invalid 'type' claim.")
    if expected_type is not None and token_type != expected_type:
        raise InvalidTokenError(f"Token type mismatch: expected {expected_type!r}.")

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise InvalidTokenError("Token has an invalid 'sub' claim.")

    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise InvalidTokenError("Token has an invalid 'jti' claim.")

    # PyJWT converts string-typed numeric claims via int() during its own
    # validation, so a JSON string like "1700000000" passes PyJWT; enforce
    # the integer type here (bool is excluded: isinstance(True, int) is True).
    for claim in ("iat", "exp"):
        value = payload.get(claim)
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidTokenError(f"Token has an invalid {claim!r} claim.")

    return payload


def generate_verification_token() -> tuple[str, str]:
    """Generate a single-use verification token pair ``(raw_token, hashed_token)``.

    The raw token is a URL-safe random string intended to be sent by email; the
    hash (SHA-256 hex of the raw token) is the value to persist in the database.
    Only the hash is stored so a database leak does not expose usable tokens.
    """
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_verification_token(raw_token)


def hash_verification_token(raw_token: str) -> str:
    """Return the deterministic SHA-256 hex digest of a raw verification token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def verify_verification_token(raw_token: str, hashed_token: str) -> bool:
    """Return True if the raw verification token hashes to the stored hash.

    Uses :func:`constant_time_compare` so the comparison does not leak timing
    information about the stored hash.
    """
    return constant_time_compare(hash_verification_token(raw_token), hashed_token)


def constant_time_compare(a: str, b: str) -> bool:
    """Return True if the two strings are equal, using a constant-time comparison.

    Inputs are compared as UTF-8 bytes via ``hmac.compare_digest``; strings of
    different lengths are never equal.
    """
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _create_token(subject: str, token_type: TokenType, lifetime_seconds: int) -> str:
    """Build and sign a JWT payload with the configured algorithm and secret.

    Raises:
        ValueError: If ``subject`` is empty or None (defense in depth; decoding
            rejects such tokens as well).
    """
    if not subject:
        raise ValueError("Token subject must be a non-empty string.")
    now = int(datetime.now(tz=UTC).timestamp())
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, key=settings.secret_key, algorithm=settings.algorithm)
