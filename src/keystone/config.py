"""Runtime settings, read from environment variables or a local `.env` file."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"

    # LLM for extraction and answers, embeddings for search. One key covers all of them.
    llm_provider: Literal["gemini", "openai"] = "gemini"
    gemini_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    openai_api_key: SecretStr = SecretStr("")
    llm_model: str | None = None  # None = Graphiti's default for the provider
    llm_small_model: str | None = None
    embedding_model: str | None = None

    # Industry packs loaded on top of the core ontology (files in ontology/packs/).
    # In .env: ONTOLOGY_PACKS=["retail"]
    ontology_packs: list[str] = ["retail"]

    # Graph partition Keystone writes into (Graphiti "group_id").
    graph_group: str = "keystone"

    # Business function for documents whose source does not suggest one.
    default_business_function: str = "Operations"

    @property
    def api_key(self) -> str:
        """The key for the configured provider ('' if missing)."""
        key = self.gemini_api_key if self.llm_provider == "gemini" else self.openai_api_key
        return key.get_secret_value()

    @property
    def api_key_name(self) -> str:
        return "GEMINI_API_KEY" if self.llm_provider == "gemini" else "OPENAI_API_KEY"


@lru_cache
def get_settings() -> Settings:
    return Settings()
