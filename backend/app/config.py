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

    # 台帳取り込み（CSV / Excel）1ファイルの上限。16万行規模の Excel は
    # 100MB を超えうるので、既定は 512MB。必要なら環境変数で上げる。
    max_import_bytes: int = 512 * 1024 * 1024

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # 画面や書き出しに出す時刻の地域。DB には UTC で持ち、見せるときだけ変換する。
    # サーバーの時計が UTC でも、資料に載る「◯◯時点」は使う人の時刻でなければ意味が無い。
    display_timezone: str = "Asia/Tokyo"

    # 互換のため残している。空の DB は起動時に表を作る。
    # 中身のある DB の差分は、専用ページで控えを取ってから当てる。
    auto_create_schema: bool = False

    # プレビュー用のデモ運用。有効にするとスキーマ作成に加えてデモデータも入れる。
    # データは永続しない前提で、画面側にもその旨を出す。本番運用では使わない。
    demo_mode: bool = False

    # API と同じプロセスから画面も配信する（1台完結モード）。
    # ビルド済みの frontend/dist を指す。
    serve_frontend: str | None = None

    # 表を更新する前の控えの置き場所。空なら DB または資料フォルダの隣の backups/
    backup_path: str | None = None

    # 閉域の中に置いたモデル（OpenAI 互換 API）。設定しなければどこへも繋がず、
    # 規則ベースだけで動く。社内に立てたものを想定していて、外の事業者へは出さない
    llm_url: str | None = None
    llm_model: str | None = None
    llm_timeout: float = 60.0


settings = Settings()
