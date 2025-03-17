from functools import lru_cache
from pydantic import BaseSettings

class Settings(BaseSettings):
    """Application settings with validation"""
    GOOGLE_BOOKS_API_KEY: str
    GOOGLE_APPLICATION_CREDENTIALS: str
    MAX_RETRIES: int = 3
    CACHE_TIMEOUT: int = 3600
    
    class Config:
        env_file = '.env'
        case_sensitive = True

@lru_cache()
def get_settings() -> Settings:
    """Cache settings to avoid reloading .env file"""
    return Settings()