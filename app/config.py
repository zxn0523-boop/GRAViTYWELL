from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Only the server reads these values; the browser never receives them."""

    deepseek_api_key: str = Field(alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    deepseek_profile_model: str | None = Field(default=None, alias="DEEPSEEK_PROFILE_MODEL")
    amap_api_key: str = Field(alias="AMAP_API_KEY")
    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")
    zhipu_api_key: str | None = Field(default=None, alias="ZHIPU_API_KEY")
    bocha_api_key: str | None = Field(default=None, alias="BOCHA_API_KEY")
    search_provider: str = Field(default="auto", alias="SEARCH_PROVIDER")
    zhipu_search_engine: str = Field(default="search_std", alias="ZHIPU_SEARCH_ENGINE")
    atmosphere_candidate_limit: int = 5
    venue_profile_cache_days: int = 30
    database_path: Path = ROOT_DIR / "gravitywell.db"
    session_ttl_hours: int = 24
    request_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / "api.env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
