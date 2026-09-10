"""Runtime settings, loaded from .env at the project root."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "db" / "mediassist.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    groq_api_key: str
    groq_model: str = "openai/gpt-oss-120b"

    jwt_secret: str
    jwt_ttl_minutes: int = 480
    jwt_algorithm: str = "HS256"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "medibot_docs"

    # Retrieval shape: cast a wide net, then let the cross-encoder narrow it.
    retrieval_candidates: int = 10
    rerank_top_k: int = 3

    dense_model: str = "BAAI/bge-small-en-v1.5"
    sparse_model: str = "Qdrant/bm25"
    rerank_model: str = "jinaai/jina-reranker-v1-turbo-en"


settings = Settings()
