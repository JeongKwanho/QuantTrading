from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Broker
    BROKER: str = "binance"
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    BINANCE_TESTNET: bool = True

    # API Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_KEY: str = "change-me"

    # Database
    POSTGRES_URL: str = ""
    REDIS_URL: str = "redis://localhost:6379"

    class Config:
        env_file = "config/.env"
        env_file_encoding = "utf-8"


settings = Settings()
