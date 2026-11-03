"""Process configuration.

Every secret and every knob comes from the environment. Defaults are the demo:
fake models, a local Postgres, and a local Redis. ``get_settings`` reads the
environment on each call so tests can point the database at a container they
just started without restarting the interpreter.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Field names map to uppercase environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://claims:claims@localhost:5432/claims"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: str = "./data"

    # ``fake`` is the deterministic offline provider. ``gemini`` and ``openai``
    # are hosted models. ``local`` is an OpenAI-compatible server on this
    # machine, such as LM Studio Bionic. Fallback is optional and is never
    # the fake provider: mixing fake answers into a real session would look
    # like a grounded answer.
    llm_provider: str = "fake"
    llm_fallback: str = ""
    embedding_provider: str = "fake"
    embedding_dim: int = 384

    gemini_api_key: str = ""
    # gemini-2.0-flash was shut down on 2026-06-01. 3.8 Flash returned HTTP 503
    # under load, so the default chat model is 3.5 Flash.
    gemini_chat_model: str = "gemini-3.5-flash"
    # Still served until 2028-05-14 and accepts outputDimensionality 384.
    gemini_embedding_model: str = "gemini-embedding-001"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4o-mini"
    # Bionic / LM Studio. The container reaches the Windows host through
    # host.docker.internal. The port is the one in Bionic's Base URL.
    local_llm_base_url: str = "http://host.docker.internal:1235/v1"
    local_llm_model: str = ""
    local_llm_api_key: str = ""
    # First request can load Gemma into memory. 20 seconds is not enough.
    local_llm_timeout_seconds: float = 180.0

    llm_timeout_seconds: float = 20.0
    max_upload_bytes: int = 15 * 1024 * 1024
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    # ``console`` prints spans. ``none`` keeps tests quiet. ``otlp`` exports them.
    otel_exporter: str = "console"
    otel_endpoint: str = "http://localhost:4318"

    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 200
    rrf_k: int = 60
    retrieval_candidates: int = 20
    retrieval_limit: int = 5

    # Illustrative USD per million tokens, used only to fill a trace attribute.
    # Gemini's figure is the paid list price for gemini-3.8-flash through
    # 2026-12-31. The free tier bills zero. This is not a provider invoice.
    price_per_million_prompt: dict[str, float] = Field(
        default_factory=lambda: {"fake": 0.0, "gemini": 0.75, "openai": 0.15, "local": 0.0}
    )
    price_per_million_completion: dict[str, float] = Field(
        default_factory=lambda: {"fake": 0.0, "gemini": 3.75, "openai": 0.60, "local": 0.0}
    )

    def cors_origin_list(self) -> list[str]:
        """Split the comma-separated CORS allow-list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


def get_settings() -> Settings:
    """Return a fresh settings object so later environment changes are visible."""
    return Settings()
