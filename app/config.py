from arq.connections import RedisSettings
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "FastAPI AI Worker Backend"
    REDIS_URL: str = ""  # Cloud Redis string (e.g. rediss://...)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    AI_PROVIDER: str = "openai"
    AI_TIMEOUT_SECONDS: float = 45.0
    AI_MAX_RETRIES: int = 4

    RATE_LIMIT_REQUESTS: int = 10
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    JOB_TTL_SECONDS: int = 86_400

    @property
    def redis_url(self) -> str:
        """Redis URL used by ARQ, the job store, and rate limiting."""
        if self.REDIS_URL.strip():
            return self.REDIS_URL.strip()
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def arq_redis_settings(self) -> RedisSettings:
        """ARQ pool settings: prefer REDIS_URL (Render/rediss), else host/port."""
        url = self.REDIS_URL.strip()
        if url:
            return RedisSettings.from_dsn(url)
        return RedisSettings(host=self.REDIS_HOST, port=self.REDIS_PORT, database=self.REDIS_DB)


settings = Settings()
