"""Embedding providers.

The fake provider is a hashing bag-of-words. The same text always produces the
same unit vector, which is what makes retrieval tests and the demo work without
an API key. It is not a semantic model. Similar wording lands nearby; a
paraphrase that shares no tokens does not.

The Gemini provider asks for the same 384 dimensions. Embedding fallback to the
fake provider is intentionally absent: the two vector spaces are not comparable,
and a silent mix would corrupt the index.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.errors import ProviderError

TOKEN_RE = re.compile(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", re.UNICODE)


class EmbeddingProvider(Protocol):
    """One vector per input text, L2-normalised, fixed dimension."""

    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch. Raise ``ProviderError`` before returning on failure."""


class FakeEmbeddings:
    """Deterministic hashing embedder used by demo mode and by the tests."""

    name = "fake"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Hash tokens into ``dim`` buckets and normalise each vector."""
        return [_hash_embed(text, self.dim) for text in texts]


class GeminiEmbeddings:
    """Gemini embeddings via the public REST API, forced to 384 dimensions."""

    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self.dim = settings.embedding_dim
        self._settings = settings

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed one text at a time so a timeout has a single, visible request."""
        if not self._settings.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY is empty.")
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(self._embed_one(text))
        return vectors

    def _embed_one(self, text: str) -> list[float]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._settings.gemini_embedding_model}:embedContent"
        )
        payload = {
            "content": {"parts": [{"text": text}]},
            "outputDimensionality": self.dim,
        }
        try:
            response = httpx.post(
                url,
                params={"key": self._settings.gemini_api_key},
                json=payload,
                timeout=self._settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            values = response.json()["embedding"]["values"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Gemini embeddings failed: {exc}") from exc
        if len(values) != self.dim:
            raise ProviderError(
                f"Gemini returned {len(values)} dimensions; the index expects {self.dim}."
            )
        return _normalise([float(value) for value in values])


def get_embedding_provider() -> EmbeddingProvider:
    """Build the configured embedder. Unknown names fail fast."""
    settings = get_settings()
    if settings.embedding_provider == "fake":
        return FakeEmbeddings(dim=settings.embedding_dim)
    if settings.embedding_provider == "gemini":
        return GeminiEmbeddings(settings)
    raise ProviderError(f"Unknown embedding provider: {settings.embedding_provider}")


def _hash_embed(text: str, dim: int) -> list[float]:
    """Signed hashing trick. Accents are folded so 'café' and 'cafe' share a bucket."""
    vector = [0.0] * dim
    tokens = TOKEN_RE.findall(_fold(text))
    if not tokens:
        vector[0] = 1.0
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    return _normalise(vector)


def _normalise(vector: list[float]) -> list[float]:
    """L2-normalise so cosine distance and the dot product rank the same way."""
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _fold(text: str) -> str:
    """Lowercase and strip accents. Used by the fake embedder and the evidence gate."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(character for character in decomposed if not unicodedata.combining(character))
