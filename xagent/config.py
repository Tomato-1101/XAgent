"""設定。環境変数 / .env から読み込む(pydantic-settings)。

秘密情報(APIキー等)はコードに置かず環境変数で与える。.env.example を参照。
全フィールドを Optional にし、キー未設定でもインポート時にクラッシュしないようにする
(投稿系の実行時に未設定なら明示エラーを出す方針)。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Claude (整形エンジン) ---
    anthropic_api_key: str | None = None
    claude_model: str = "claude-sonnet-4-6"  # 整形は速度重視。必要に応じ opus に変更可

    # --- X API (OAuth1.0a: 投稿/書込, Bearer: 読取) ---
    x_api_key: str | None = None
    x_api_secret: str | None = None
    x_access_token: str | None = None
    x_access_token_secret: str | None = None
    x_bearer_token: str | None = None

    # --- twitterapi.io (他人の投稿の読み取り用。安価・読取専用の非公式プロキシ) ---
    # 自分のアカウントの書き込みは公式Xのみ(BAN回避)。読み取りはコスト削減でこちらを優先し、
    # 失敗時のみ公式Xにフォールバックする。未設定なら読み取りも公式X(オプトイン)。
    twitterapi_io_key: str | None = None

    # --- DB ---
    db_path: str = "xagent.db"
    # DBファイルの容量上限(バイト)。超えると古い端末状態(投稿済み/却下/取消)から物理削除する。
    # 既定2GB(テキスト主体なら数千〜数万件保存できる安全弁。通常は発動しない)。
    max_db_bytes: int = 2 * 1024 * 1024 * 1024

    # --- メディア(画像/動画)保存先 ---
    media_dir: str = "media"  # アップロード画像/動画のローカル保存先(投稿時にXへ上げる)

    # --- ポリシーガード(安全運用の既定値) ---
    max_posts_per_day: int = 10       # 自然な上限(2-10)。超過は既定で抑止
    hard_cap_posts_per_day: int = 100  # 安全側のハード上限(規約上限2400より十分低く)
    min_post_interval_seconds: int = 300  # 連投の最小間隔
    posting_enabled: bool = True      # 緊急停止スイッチ。Falseで手動/予約とも全投稿を停止

    # --- 予約投稿の自動発火(APIプロセス内の常駐スケジューラ) ---
    scheduler_enabled: bool = True    # Falseで予約キューの自動処理を止める(手動投稿は可)
    scheduler_interval_seconds: int = 60  # 予約キューを点検する間隔

    # --- Web API (任意の認証) ---
    api_token: str | None = None      # 設定時はFastAPIの書込系で X-API-Token を必須にする

    # --- 運用 ---
    timezone: str = "Asia/Tokyo"

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """`.env`/環境変数を編集した後に設定を読み直す(lru_cache を破棄して再生成)。

    注意: 稼働中のプロセスは起動時の値をキャッシュするため、確実なのはプロセス再起動。
    """
    get_settings.cache_clear()
    return get_settings()
