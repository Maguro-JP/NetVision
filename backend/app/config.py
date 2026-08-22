import os

from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_files() -> tuple[str, ...]:
    """読み込む設定ファイル。後ろに書いたものが勝つ。

    客先ごとに、置き場所・しきい値・モデルの宛先が少しずつ違う。共通の `.env` に
    書き足していくと、どれがその現場の都合で、どれが全社の既定なのかが混ざる。
    現場ぶんは `NETVISION_ENV_FILE` で別ファイルに分け、共通は触らないでおく。

      NETVISION_ENV_FILE=config/aomori.env
    """
    files = [".env"]
    extra = os.environ.get("NETVISION_ENV_FILE")
    if extra:
        files.append(extra)
    return tuple(files)


class Settings(BaseSettings):
    """環境変数から読み込む設定。閉域環境での運用を想定し外部依存は持たない。"""

    model_config = SettingsConfigDict(
        env_prefix="NETVISION_", env_file=_env_files(), extra="ignore"
    )

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
    # モデルを使うかどうか。置いてあっても、切って確かめたいことがある
    # （規則ベースだけのときと結果を見比べる、遅いので一時的に止める、など）
    llm_enabled: bool = True
    llm_url: str | None = None
    llm_model: str | None = None
    llm_timeout: float = 60.0
    # 事業者の API を使う場合の鍵。閉域では空のまま。使う人が自分の契約を
    # 入れる想定で、こちらでは持たない
    llm_api_key: str | None = None
    # 役割ごとにモデルを分ける場合の一覧（JSON）。要約は小さいモデル、
    # Config の下書きは大きいモデル、のように使い分ける
    llm_profiles: str | None = None
    # 外の事業者へ出してよいか。既定は閉域なので不可。人が明示したときだけ許す
    llm_allow_external: bool = False
    # 出してよい宛先（カンマ区切り）。並べると、そこ以外へは出さない。
    # 「外向き可」を1つ立てただけで、どこへでも送れる状態にしないため
    llm_allowed_hosts: str | None = None


settings = Settings()
