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

    # --- DB ---
    db_path: str = "xagent.db"

    # --- ポリシーガード(安全運用の既定値) ---
    max_posts_per_day: int = 10       # 自然な上限(2-10)。超過は既定で抑止
    hard_cap_posts_per_day: int = 100  # 安全側のハード上限(規約上限2400より十分低く)
    min_post_interval_seconds: int = 300  # 連投の最小間隔
    posting_enabled: bool = True      # 緊急停止スイッチ。Falseで手動/予約とも全投稿を停止

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
