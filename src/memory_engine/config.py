"""Runtime configuration. Loaded once at startup; imported everywhere else."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class _SubSettings(BaseSettings):
    """Base for nested settings that tolerate unknown keys from future phases."""

    model_config = SettingsConfigDict(extra="ignore")


class DBSettings(_SubSettings):
    backend: Literal["sqlite", "postgres"] = "sqlite"
    path: str = "data/engine.db"
    url: str | None = None


class EmbeddingSettings(_SubSettings):
    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    dimensions: int = 384
    revision: str = "sbert-minilm-l6-v2-1"


class GroundingSettings(_SubSettings):
    similarity_threshold: float = 0.40
    llm_judge_required_for_tiers: list[str] = Field(
        default_factory=lambda: ["semantic", "procedural"]
    )


class WorkingMemorySettings(_SubSettings):
    capacity: int = 64
    initial_activation: float = 1.0
    decay_half_life_minutes: int = 30


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMORY_ENGINE_",
        env_nested_delimiter="__",
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"
    monthly_budget_usd: float = 0.0
    vault_key: SecretStr | None = None
    backup_recipient: str | None = None

    db: DBSettings = Field(default_factory=DBSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    grounding: GroundingSettings = Field(default_factory=GroundingSettings)
    working_memory: WorkingMemorySettings = Field(default_factory=WorkingMemorySettings)

    @classmethod
    def load(cls, config_path: str | Path = "config/default.toml") -> Settings:
        """Load settings from TOML + env vars. Env vars take precedence."""
        config_path = Path(config_path)
        if config_path.exists():
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        else:
            data = {}
        return cls(**data)


def get_settings() -> Settings:
    """Return the module-level settings singleton.

    Deferred loading avoids import-time side effects (filesystem reads,
    env parsing) which break tests that patch config values.
    """
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


_settings: Settings | None = None
