from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """環境変数から読み込む設定。閉域環境での運用を想定し外部依存は持たない。"""

    model_config = SettingsConfigDict(env_prefix="NETVISION_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://netvision:netvision@localhost:5432/netvision"

    # 保守期限 / EoL の警告しきい値（日数）
    warning_days: int = 365
    critical_days: int = 90

    # リファレンス（EoL / 脆弱性）がこの日数より古ければ「情報が古い」と表示する。
    # 閉域環境では自動更新されないため、鮮度を意識させる必要がある。
    reference_stale_days: int = 180

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


settings = Settings()
