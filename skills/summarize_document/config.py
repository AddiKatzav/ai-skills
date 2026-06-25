"""
Safe configuration for the summarize_document skill.
All secrets are loaded via environment variables and stored as SecretStr.

Env prefix: SKILL_SUMMARIZE_DOCUMENT_

Example .env (never commit this file):
    SKILL_SUMMARIZE_DOCUMENT_API_KEY=sk-ant-...
    SKILL_SUMMARIZE_DOCUMENT_MODEL=claude-sonnet-4-6
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SummarizeDocumentConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SKILL_SUMMARIZE_DOCUMENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        secrets_dir="/run/secrets",
    )

    # ── LLM Configuration ──────────────────────────────────────────────────────
    model: str = Field(default="claude-sonnet-4-6")
    fallback_model: str = Field(default="claude-haiku-4-5-20251001")
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)

    # ── Token Budget ────────────────────────────────────────────────────────────
    max_input_tokens: int = Field(
        default=2048,
        ge=1,
        le=32768,
        description="Max total input tokens (system + user). Inputs exceeding this are rejected.",
    )
    token_buffer: int = Field(
        default=128,
        ge=0,
        description="Safety buffer subtracted from max_input_tokens to prevent overflow.",
    )

    # ── Secrets — stored as SecretStr, NEVER logged ────────────────────────────
    api_key: SecretStr = Field(
        description="Anthropic API key. Set via SKILL_SUMMARIZE_DOCUMENT_API_KEY.",
    )

    # ── Timeouts & Retries ─────────────────────────────────────────────────────
    request_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_retries: int = Field(default=2, ge=0, le=5)

    # ── Feature Flags ──────────────────────────────────────────────────────────
    enable_pii_restoration: bool = Field(
        default=False,
        description=(
            "If True, PIIMap.restore() is called on the final output result. "
            "Only enable when the caller is the same trusted entity that provided the PII."
        ),
    )


@lru_cache(maxsize=1)
def get_config() -> SummarizeDocumentConfig:
    """
    Return the cached singleton config. Cache is per-process.
    In tests, call get_config.cache_clear() before patching environment variables.
    """
    return SummarizeDocumentConfig()  # type: ignore[call-arg]
