from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    sqlite_path: Path = Path(__file__).resolve().parent.parent / "data" / "library.db"
    retrieval_top_k: int = 40
    max_context_papers: int = 60
    context_token_warn_threshold: int = 200_000

    embedding_batch_size: int = 16
    embedding_batch_delay_sec: float = 0.5
    embedding_max_retries: int = 12
    embedding_initial_backoff_sec: float = 2.0
    embedding_max_backoff_sec: float = 120.0


settings = Settings()
