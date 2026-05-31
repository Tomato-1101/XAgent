"""DBモデル(SQLModel)。SQLiteに永続化。

下書き/予約・エンゲージ対象・監視カーソル・スタイル・過去投稿・コストログを保持する。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    # SQLite は tz情報を保持しないため、内部は naive UTC に統一して比較ズレを防ぐ
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DraftKind(str, Enum):
    POST = "post"      # 通常投稿(スレッド可)
    REPLY = "reply"    # 自分宛/対象へのリプライ
    QUOTE = "quote"    # 引用RT


class DraftStatus(str, Enum):
    DRAFT = "draft"        # AI生成直後・未承認
    APPROVED = "approved"  # 人間が承認
    QUEUED = "queued"      # 投稿キュー(予約/最適時間待ち)
    POSTED = "posted"      # 投稿済み
    REJECTED = "rejected"  # 却下


class Draft(SQLModel, table=True):
    """投稿/リプライ/引用RT の下書き。承認を経てキュー→投稿される。"""

    id: int | None = Field(default=None, primary_key=True)
    kind: DraftKind = DraftKind.POST
    status: DraftStatus = DraftStatus.DRAFT

    source_text: str = ""                 # ユーザーが投げた元テキスト(整形前)
    segments_json: str = "[]"             # 整形後のセグメント(スレッド)をJSON配列で保持
    media_paths_json: str = "[]"          # 添付画像のローカルパス(任意)

    target_tweet_id: str | None = None    # reply/quote の対象ツイートID
    target_handle: str | None = None      # 表示用

    scheduled_at: datetime | None = None  # 指定時刻/最適時間の予約
    posted_at: datetime | None = None
    posted_tweet_id: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TargetKind(str, Enum):
    MANUAL = "manual"        # 手動リスト(主)
    GENRE = "genre"          # ジャンル/キーワード探索
    FOLLOWING = "following"  # フォロー中


class EngageTarget(SQLModel, table=True):
    """絡む対象(有名人等)。新規投稿を監視して絡み案を生成する。"""

    id: int | None = Field(default=None, primary_key=True)
    kind: TargetKind = TargetKind.MANUAL
    handle: str | None = None     # @なしのスクリーンネーム
    user_id: str | None = None    # X user id
    keyword: str | None = None    # genre探索用
    active: bool = True
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class MonitorCursor(SQLModel, table=True):
    """監視ストリームごとの最終取得ID(since_id)。重複処理を避ける。"""

    id: int | None = Field(default=None, primary_key=True)
    stream: str = Field(index=True)  # 例: "mentions" / "target:<user_id>"
    last_seen_id: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class StyleProfile(SQLModel, table=True):
    """整形のトーン指定。文章のスタイルガイド(+将来は過去投稿の要約)。"""

    id: int | None = Field(default=None, primary_key=True)
    name: str = "default"
    guide_text: str = ""   # 文章で指定する口調・NG・テンプレ
    active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class PastPost(SQLModel, table=True):
    """学習用に取得した投稿(自分/他人)。アカウントごとに保持し特徴抽出の素材にする。"""

    id: int | None = Field(default=None, primary_key=True)
    tweet_id: str = Field(index=True)
    text: str = ""
    created_at: datetime | None = None
    like_count: int = 0
    retweet_count: int = 0
    fetched_at: datetime = Field(default_factory=_utcnow)
    # どのアカウントの投稿か(他人学習機能で追加。既定は自分=後方互換)
    author_user_id: str | None = Field(default=None, index=True)
    author_handle: str | None = None
    is_own: bool = True


class AccountProfile(SQLModel, table=True):
    """アカウント単位の抽出プロファイル(口調/内容/投稿時間など)。

    整形時に「誰を真似るか」として選択できる。自分のアカウントも他人と同列に扱う
    (常時適用の口調は StyleProfile.guide_text、こちらは選択時のみ使う)。
    """

    id: int | None = Field(default=None, primary_key=True)
    handle: str = Field(index=True)
    user_id: str | None = Field(default=None, index=True)
    is_self: bool = False
    display_name: str | None = None
    posts_fetched: int = 0
    avg_likes: float = 0.0
    avg_retweets: float = 0.0
    active_hours_json: str = "[]"   # 投稿時間帯(JST hour→count)。created_atから純計算
    profile_json: str = "{}"        # 構造化プロファイル(口調/テーマ/型/フック/ハッシュタグ/頻度)
    profile_text: str = ""          # AIの散文サマリ(整形プロンプトに乗せる)
    extracted_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class MonitorSettings(SQLModel, table=True):
    """監視(絡み)各ソースのオン/オフ。コスト管理のためユーザーが自由に切替える。

    単一行(id=1)で運用する。コスト高のソース(フォロー中/キーワード)は既定オフ。
    """

    id: int | None = Field(default=None, primary_key=True)
    mentions_enabled: bool = True
    manual_targets_enabled: bool = True
    keyword_search_enabled: bool = False
    following_enabled: bool = False
    updated_at: datetime = Field(default_factory=_utcnow)


class CostKind(str, Enum):
    READ = "read"    # $0.005/件
    WRITE = "write"  # $0.01/件
    TL = "tl"        # $0.01/件 (timeline lookup)


class ApiCostLog(SQLModel, table=True):
    """X API 従量課金のコスト記録(監視コストの可視化用)。"""

    id: int | None = Field(default=None, primary_key=True)
    kind: CostKind
    units: int = 1
    cost_usd: float = 0.0
    note: str | None = None
    ts: datetime = Field(default_factory=_utcnow)
