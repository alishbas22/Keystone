"""Runtime settings, read from environment variables or a local `.env` file."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"

    # LLM (extraction + answers) and embeddings (search). OpenAI is Graphiti's default.
    openai_api_key: SecretStr = SecretStr("")
    llm_model: str | None = None  # None = Graphiti's default model
    embedding_model: str | None = None

    # Industry packs loaded on top of the core ontology (files in ontology/packs/).
    # In .env: ONTOLOGY_PACKS=["retail"]
    ontology_packs: list[str] = ["retail"]

    # Graph partition the demo writes into (Graphiti "group_id").
    graph_group: str = "keystone-demo"


@lru_cache
def get_settings() -> Settings:
    return Settings()
