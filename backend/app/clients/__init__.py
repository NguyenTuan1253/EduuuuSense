"""External service clients. Routers and services reach external systems
through these adapters only."""

from .google_oauth import GoogleOAuthClient, GoogleOAuthError

__all__ = ["GoogleOAuthClient", "GoogleOAuthError"]
