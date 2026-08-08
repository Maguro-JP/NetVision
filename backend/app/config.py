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

    # ベンダーマニュアル等の実体を置くディレクトリ。DB にはメタデータとパスだけ持つ。
    document_storage_path: str = "./data/documents"
    max_document_bytes: int = 200 * 1024 * 1024

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # 起動時にテーブルが無ければ作る。1台完結の個人利用（SQLite）向け。
    # 複数人で運用する場合は alembic でマイグレーションするので false のまま。
    auto_create_schema: bool = False

    # プレビュー用のデモ運用。有効にするとスキーマ作成に加えてデモデータも入れる。
    # データは永続しない前提で、画面側にもその旨を出す。本番運用では使わない。
    demo_mode: bool = False

    # API と同じプロセスから画面も配信する（1台完結モード）。
    # ビルド済みの frontend/dist を指す。
    serve_frontend: str | None = None


settings = Settings()
