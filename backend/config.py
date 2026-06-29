from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Delta Bot"
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "sqlite:///./database/trades.db"

    delta_api_key: str = ""
    delta_api_secret: str = ""
    delta_base_url: str = "https://api.india.delta.exchange"
    delta_ws_url: str = "wss://socket.india.delta.exchange"
    delta_product_id_default: int = 27

    log_level: str = "INFO"
    default_equity: float = 10000.0
    default_leverage: int = 3

    risk_per_trade_pct: float = 1.0
    daily_loss_limit_pct: float = 3.0
    max_trades_per_day: int = 6
    max_leverage: int = 5

    default_rr: float = 2.0
    reentry_buffer_pct: float = 0.10

    live_trading_enabled: bool = False
    require_live_exchange_when_enabled: bool = True

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
