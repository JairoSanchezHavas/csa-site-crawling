"""Configuration settings for CSA Tech Auditor using pydantic-settings.
"""

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable overrides."""
    PROJECT_NAME: str = "CSA Tech Auditor · Havas"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False

    # Server binding
    HOST: str = "0.0.0.0"
    PORT: int = 8080

    # CORS configuration
    ALLOWED_ORIGINS: str = "*"

    # Auditing defaults
    DEFAULT_TIMEOUT_MS: int = 20000
    MAX_CRAWL_PAGES: int = 15
    PLAYWRIGHT_HEADLESS: bool = True
    STATIC_DIR: str = "static"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse comma-separated ALLOWED_ORIGINS into a clean list."""
        if not self.ALLOWED_ORIGINS or self.ALLOWED_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]


settings = Settings()
