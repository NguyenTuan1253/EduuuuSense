"""Google identity verification client (US 2.1/2.2 social login).

Verifies Google-issued ID tokens via Google's public ``tokeninfo`` endpoint —
Google performs the signature and expiry validation, so no crypto dependency
is required. The call is blocking stdlib HTTP executed off the event loop.

This client never sees or stores passwords; it returns only the claims the
auth service needs (email, email_verified, name, aud).
"""

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.core.config import settings

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_TIMEOUT_SECONDS = 10


class GoogleOAuthError(Exception):
    """Raised when Google identity verification fails (network or claims)."""


class GoogleOAuthClient:
    """Verifies Google ID tokens. Injectable for tests."""

    async def verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Return verified claims for ``id_token`` or raise GoogleOAuthError."""
        try:
            data = await asyncio.to_thread(self._fetch_tokeninfo, id_token)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GoogleOAuthError(f"Google token verification failed: {type(exc).__name__}") from exc

        email = str(data.get("email") or "").strip().lower()
        email_verified = str(data.get("email_verified", "")).lower() == "true"
        if not email or not email_verified:
            raise GoogleOAuthError("Google account email is missing or not verified.")

        expected_aud = settings.google_oauth_client_id
        if expected_aud and data.get("aud") != expected_aud:
            raise GoogleOAuthError("ID token audience mismatch.")

        return {
            "email": email,
            "email_verified": email_verified,
            "name": str(data.get("name") or "")[:150],
            "sub": str(data.get("sub") or ""),
        }

    @staticmethod
    def _fetch_tokeninfo(id_token: str) -> dict[str, Any]:
        url = f"{TOKENINFO_URL}?{urllib.parse.urlencode({'id_token': id_token})}"
        request = urllib.request.Request(url, headers={"User-Agent": "EduSense-Backend"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise GoogleOAuthError(f"Google tokeninfo returned HTTP {response.status}")
            return dict(json.loads(response.read().decode("utf-8")))
