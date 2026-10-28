"""Gemini embedding adapter (Invariant 4: the only Gemini SDK import point).

The google-genai SDK is imported lazily: when it is not installed or no API
key is configured, the adapter fails closed with ProviderNotConfigured — the
ingestion pipeline marks the document failed instead of crashing the worker.
The vector dimension comes strictly from configuration and must match the
``document_chunks.embedding`` column (vector(768) per the baseline schema);
a mismatch raises EmbeddingDimensionError rather than inserting bad vectors.
"""

import asyncio
from typing import Any

from app.core.config import settings


class ProviderNotConfiguredError(Exception):
    """Raised when the Gemini SDK or its API key is unavailable (fail closed)."""


class EmbeddingDimensionError(Exception):
    """Raised when a returned embedding's dimension mismatches configuration."""


class EmbeddingAdapter:
    """Embedding generation. Injectable fake in tests; SDK lazy in production."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not settings.gemini_api_key:
            raise ProviderNotConfiguredError("GEMINI_API_KEY is not configured.")
        try:
            from google import genai  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "The google-genai SDK is not installed."
            ) from exc
        self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; returns one vector per input, config-sized."""
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        client = self._ensure_client()
        try:
            response = client.models.embed_content(
                model=settings.gemini_embedding_model,
                contents=texts,
            )
        except Exception as exc:  # noqa: BLE001 — mapped for retry semantics
            raise ProviderNotConfiguredError(f"Embedding call failed: {type(exc).__name__}") from exc

        vectors = [list(item.values) for item in response.embeddings]
        expected = settings.gemini_embedding_dimension
        for vector in vectors:
            if len(vector) != expected:
                raise EmbeddingDimensionError(
                    f"Embedding dimension {len(vector)} does not match configured {expected}."
                )
        return vectors
