"""Unit tests for security primitives in app.core.security.

Covers password hashing (Argon2id), JWT creation/validation (access and refresh),
and single-use verification token helpers. All failures of ``decode_token`` must
surface as the single ``InvalidTokenError`` type so services can map to 401.
"""

import hashlib
import time
from typing import Any

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    InvalidTokenError,
    constant_time_compare,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_verification_token,
    hash_password,
    hash_verification_token,
    verify_password,
    verify_verification_token,
)

_PLAIN_PASSWORD = "correct-horse-battery-staple"
_SUBJECT = "user-123"


def _make_token(claims: dict[str, Any], secret: str | None = None) -> str:
    """Encode test claims as a JWT with the real settings secret (or an override)."""
    return jwt.encode(
        claims,
        key=secret if secret is not None else settings.secret_key,
        algorithm=settings.algorithm,
    )


def _valid_claims(now: int, token_type: str = "access") -> dict[str, Any]:
    """Return a complete, valid set of JWT claims for testing."""
    return {
        "sub": _SUBJECT,
        "type": token_type,
        "iat": now,
        "exp": now + 900,
        "jti": "test-jti-value",
    }


# ---------------------------------------------------------------------------
# Password hashing (Argon2id)
# ---------------------------------------------------------------------------


def test_hash_password_uses_argon2id_format() -> None:
    """Verify the stored hash is a PHC-format Argon2id hash string."""
    hashed = hash_password(_PLAIN_PASSWORD)
    assert hashed.startswith("$argon2id$")


def test_hash_and_verify_password_roundtrip() -> None:
    """Verify the correct password validates against its hash."""
    hashed = hash_password(_PLAIN_PASSWORD)
    assert verify_password(_PLAIN_PASSWORD, hashed) is True


def test_verify_password_wrong_password_fails() -> None:
    """Verify an incorrect password does not validate against the hash."""
    hashed = hash_password(_PLAIN_PASSWORD)
    assert verify_password("wrong-password", hashed) is False


def test_hash_password_unique_salts() -> None:
    """Verify hashing the same password twice yields different hashes."""
    first = hash_password(_PLAIN_PASSWORD)
    second = hash_password(_PLAIN_PASSWORD)
    assert first != second


def test_hash_output_is_not_reversible_plaintext() -> None:
    """Verify the plaintext password is not recoverable from the hash string."""
    hashed = hash_password(_PLAIN_PASSWORD)
    assert _PLAIN_PASSWORD not in hashed
    assert hashed != _PLAIN_PASSWORD


def test_verify_password_malformed_hash_returns_false() -> None:
    """Verify a malformed stored hash fails verification instead of raising."""
    assert verify_password(_PLAIN_PASSWORD, "not-a-valid-hash") is False


# ---------------------------------------------------------------------------
# Access token
# ---------------------------------------------------------------------------


def test_settings_default_token_lifetimes() -> None:
    """Pin default settings so exp-iat assertions match the accepted criteria."""
    assert settings.access_token_expire_minutes * 60 == 900
    assert settings.refresh_token_expire_days * 24 * 60 * 60 == 604800


def test_access_token_roundtrip_contains_required_claims() -> None:
    """Verify an access token decodes with sub/type/iat/exp/jti and 900s lifetime."""
    token = create_access_token(_SUBJECT)
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == _SUBJECT
    assert payload["type"] == "access"
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    assert isinstance(payload["jti"], str) and payload["jti"]
    assert payload["exp"] - payload["iat"] == 900


# ---------------------------------------------------------------------------
# Refresh token
# ---------------------------------------------------------------------------


def test_refresh_token_roundtrip_type_and_lifetime() -> None:
    """Verify a refresh token decodes as type=refresh with 604800s lifetime."""
    token = create_refresh_token(_SUBJECT)
    payload = decode_token(token, expected_type="refresh")
    assert payload["sub"] == _SUBJECT
    assert payload["type"] == "refresh"
    assert payload["exp"] - payload["iat"] == 604800


def test_access_and_refresh_tokens_have_distinct_jti() -> None:
    """Verify each token gets its own jti so tokens are individually identifiable."""
    first = decode_token(create_access_token(_SUBJECT))
    second = decode_token(create_refresh_token(_SUBJECT))
    assert first["jti"] != second["jti"]


# ---------------------------------------------------------------------------
# Token rejection cases
# ---------------------------------------------------------------------------


def test_expired_token_rejected() -> None:
    """Verify a token whose exp is in the past raises InvalidTokenError."""
    now = int(time.time())
    token = _make_token(_valid_claims(now - 3600))
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access")


def test_tampered_token_rejected() -> None:
    """Verify altering the signature invalidates the token.

    The first signature character is fully significant in base64url (unlike the
    last character, whose trailing bits are ignored when decoding), so changing
    it always changes the decoded signature bytes.
    """
    header, payload_segment, signature = create_access_token(_SUBJECT).split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered_token = f"{header}.{payload_segment}.{tampered_signature}"
    with pytest.raises(InvalidTokenError):
        decode_token(tampered_token, expected_type="access")


def test_garbage_token_rejected() -> None:
    """Verify a non-JWT string raises InvalidTokenError."""
    with pytest.raises(InvalidTokenError):
        decode_token("this-is-not-a-token")


def test_token_signed_with_wrong_secret_rejected() -> None:
    """Verify a structurally valid token signed with a foreign secret fails."""
    now = int(time.time())
    token = _make_token(_valid_claims(now), secret="attacker-controlled-secret")
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access")


def test_token_missing_required_claims_rejected() -> None:
    """Verify a token lacking required claims raises InvalidTokenError."""
    token = _make_token({"sub": _SUBJECT})
    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_token_with_unknown_type_claim_rejected() -> None:
    """Verify a token whose type is neither access nor refresh is rejected."""
    now = int(time.time())
    token = _make_token(_valid_claims(now, token_type="banana"))
    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_refresh_token_rejected_where_access_required() -> None:
    """Verify a refresh token fails the access-token type check but passes its own."""
    refresh_token = create_refresh_token(_SUBJECT)
    with pytest.raises(InvalidTokenError):
        decode_token(refresh_token, expected_type="access")
    assert decode_token(refresh_token, expected_type="refresh")["type"] == "refresh"


def test_access_token_rejected_where_refresh_required() -> None:
    """Verify an access token fails the refresh-token type check."""
    access_token = create_access_token(_SUBJECT)
    with pytest.raises(InvalidTokenError):
        decode_token(access_token, expected_type="refresh")


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: _make_token(_valid_claims(int(time.time()) - 3600)),
        lambda: _make_token(_valid_claims(int(time.time())), secret="attacker-secret"),
        lambda: _make_token({"sub": _SUBJECT}),
        lambda: _make_token(_valid_claims(int(time.time()), token_type="banana")),
        lambda: _make_token(_valid_claims(int(time.time())))[:-1] + "x",
        lambda: "garbage-token",
    ],
    ids=[
        "expired",
        "wrong-secret",
        "missing-claims",
        "unknown-type",
        "tampered-signature",
        "garbage",
    ],
)
def test_decode_token_raises_single_exception_type(token_factory: Any) -> None:
    """Verify every failure mode raises exactly InvalidTokenError for 401 mapping."""
    with pytest.raises(InvalidTokenError):
        decode_token(token_factory())


# ---------------------------------------------------------------------------
# Algorithm-confusion regression tests (QA)
# ---------------------------------------------------------------------------


def test_alg_none_token_rejected() -> None:
    """Verify an unsigned alg=none token is rejected despite a valid signature area."""
    now = int(time.time())
    token = jwt.encode(_valid_claims(now), key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access")


def test_hs512_token_with_real_secret_rejected() -> None:
    """Verify a token signed with HS512 and the real secret is still rejected.

    The verification algorithm is pinned to settings.algorithm (HS256), so a
    token using any other algorithm must fail even with a correct signature.
    """
    now = int(time.time())
    token = jwt.encode(_valid_claims(now), key=settings.secret_key, algorithm="HS512")
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access")


# ---------------------------------------------------------------------------
# Claim type/shape regression tests (QA)
# ---------------------------------------------------------------------------


def test_string_typed_exp_claim_rejected() -> None:
    """Verify a string-typed exp claim (e.g. \"1700000000\") is rejected."""
    now = int(time.time())
    claims = _valid_claims(now)
    claims["exp"] = str(now + 900)
    with pytest.raises(InvalidTokenError):
        decode_token(_make_token(claims), expected_type="access")


def test_string_typed_iat_claim_rejected() -> None:
    """Verify a string-typed iat claim (e.g. \"1700000000\") is rejected."""
    now = int(time.time())
    claims = _valid_claims(now)
    claims["iat"] = str(now)
    with pytest.raises(InvalidTokenError):
        decode_token(_make_token(claims), expected_type="access")


def test_empty_sub_claim_rejected() -> None:
    """Verify a token with an empty sub claim is rejected."""
    now = int(time.time())
    claims = _valid_claims(now)
    claims["sub"] = ""
    with pytest.raises(InvalidTokenError):
        decode_token(_make_token(claims), expected_type="access")


def test_empty_jti_claim_rejected() -> None:
    """Verify a token with an empty jti claim is rejected."""
    now = int(time.time())
    claims = _valid_claims(now)
    claims["jti"] = ""
    with pytest.raises(InvalidTokenError):
        decode_token(_make_token(claims), expected_type="access")


def test_expired_refresh_token_rejected_with_refresh_type() -> None:
    """Verify an expired refresh token fails even with the matching type check."""
    now = int(time.time())
    claims = _valid_claims(now, token_type="refresh")
    claims["exp"] = now - 60
    with pytest.raises(InvalidTokenError):
        decode_token(_make_token(claims), expected_type="refresh")


# ---------------------------------------------------------------------------
# Token creation guards (QA)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "create",
    [create_access_token, create_refresh_token],
    ids=["access", "refresh"],
)
@pytest.mark.parametrize("subject", [None, ""], ids=["none", "empty"])
def test_create_token_rejects_invalid_subject(create: Any, subject: Any) -> None:
    """Verify token creation raises ValueError for None or empty subjects."""
    with pytest.raises(ValueError):
        create(subject)


# ---------------------------------------------------------------------------
# Verification tokens (single use, stored hashed)
# ---------------------------------------------------------------------------


def test_generate_verification_token_two_generations_differ() -> None:
    """Verify each generated token pair is unique."""
    raw_first, hashed_first = generate_verification_token()
    raw_second, hashed_second = generate_verification_token()
    assert raw_first != raw_second
    assert hashed_first != hashed_second


def test_verification_hash_is_deterministic_sha256_of_raw() -> None:
    """Verify the stored hash equals the SHA-256 hex digest of the raw token."""
    raw, hashed = generate_verification_token()
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert hashed == expected
    assert hash_verification_token(raw) == expected


def test_raw_token_not_recoverable_from_hash() -> None:
    """Verify the hash is a 64-char hex digest and does not contain the raw token."""
    raw, hashed = generate_verification_token()
    assert len(hashed) == 64
    assert int(hashed, 16) >= 0  # strictly hexadecimal
    assert raw not in hashed


def test_verify_verification_token_matches_only_correct_raw() -> None:
    """Verify the correct raw token validates and a wrong one does not."""
    raw, hashed = generate_verification_token()
    assert verify_verification_token(raw, hashed) is True
    assert verify_verification_token(raw + "x", hashed) is False


def test_constant_time_compare_equal_and_unequal() -> None:
    """Verify constant-time comparison returns True only for equal strings."""
    assert constant_time_compare("same-value", "same-value") is True
    assert constant_time_compare("value-a", "value-b") is False
    assert constant_time_compare("value-a", "value-a-longer") is False
    assert constant_time_compare("", "") is True
