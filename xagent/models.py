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
    QUOTE = "quote"    # 引用RT(他人の投稿にコメントを付けて拡散)
    REPOST = "repost"  # 通常リポスト(コメント無し)。自分の過去投稿の再拡散に使う


class DraftStatus(str, Enum):
    DRAFT = "draft"        # AI生成直後・未承認
    APPROVED = "approved"  # 人間が承認
    QUEUED = "queued"      # 投稿キュー(予約/最適時間待ち)
    POSTED = "posted"      # 投稿済み
    REJECTED = "rejected"  # 却下
    CANCELED = "canceled"  # 取消(ゴミ箱)。DBには残し、容量超過時に古い順で物理削除する


class Draft(SQLModel, table=True):
    """投稿/リプライ/引用RT の下書き。承認を経てキュー→投稿される。"""

    id: int | None = Field(default=None, primary_key=True)
    kind: DraftKind = DraftKind.POST
    status: DraftStatus = DraftStatus.DRAFT

    source_text: str = ""                 # 自分(ユーザー/エージェント)の入力(整形前の種)
    segments_json: str = "[]"             # 整形後のセグメント(スレッド)をJSON配列で保持
    media_paths_json: str = "[]"          # 添付画像のローカルパス(任意)

    target_tweet_id: str | None = None    # reply/quote/repost の対象ツイートID
    target_handle: str | None = None      # 表示用
    # 絡む相手の元ポスト本文。reply/quote/repost で「何に対してか」を人間が判断するために保持する。
    # source_text(自分の入力)とは別物。自動生成(monitor)・URL指定の絡みで取得して入れる。
    target_text: str = ""
    # 元ポストが投稿された時刻(naive UTC)。取得できた時のみ。案の鮮度判断に使う(古い投稿への絡みは効果が薄い)。
    target_created_at: datetime | None = None

    scheduled_at: datetime | None = None  # 指定時刻/最適時間の予約
    posted_at: datetime | None = None
    posted_tweet_id: str | None = None

    # 制限時間帯(平日の指定帯)でも投稿してよいか。二段階確認(警告を無視→最終確認)で True にする。
    # 予約投稿はUIから人がいない時刻に発火するため、許可をこの列に保存しておく。
    blackout_override: bool = False

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TargetKind(str, Enum):
    MANUAL = "manual"        # 手動リスト(主)
    LIST = "list"            # Xリスト連携(list_idのメンバーを毎回展開して巡回)
    GENRE = "genre"          # ジャンル/キーワード探索
    FOLLOWING = "following"  # フォロー中


class EngageTarget(SQLModel, table=True):
    """絡む対象(有名人等)。新規投稿を監視して絡み案を生成する。"""

    id: int | None = Field(default=None, primary_key=True)
    kind: TargetKind = TargetKind.MANUAL
    handle: str | None = None     # @なしのスクリーンネーム。kind=LISTではリスト名(表示用)
    user_id: str | None = None    # X user id
    list_id: str | None = None    # kind=LIST時のXリストID。巡回時に現在のメンバーへ毎回展開する
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


class TemplateKind(str, Enum):
    POST = "post"      # 通常投稿の型(バズの型A〜P/掴み)
    REPLY = "reply"    # 絡みリプの型(R1〜R6)
    QUOTE = "quote"    # 引用RTの型


class PromptTemplate(SQLModel, table=True):
    """投稿/リプ生成に使う「型」。本文(=LLMへ渡す指示文)を名前付き・カテゴリ別に複数保持する。

    Composeで選択(または「AIに任せる」でAIが自動選択)し、整形プロンプトに注入する。
    active はカテゴリ(kind)ごとに1つ=monitorの自動リプ/引用で使う既定の型。
    builtin はシード投入(buzz-playbook由来)の目印で、再シード時の重複投入を防ぐ。
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
    kind: TemplateKind = TemplateKind.POST
    body: str = ""               # 型の中身(LLMへ「この型・狙いで書け」と渡す指示文)
    active: bool = False         # 同kindで1つだけ。monitorの自動生成が使う既定
    builtin: bool = False        # シード投入(buzz-playbook)の目印
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


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
    # 常駐デーモンの自動実行スイッチ。Falseにすると、プロセスは動いていても各ティックが
    # その処理をスキップする(=デーモンを止めずにUIから一時停止/再開できる)。
    auto_monitor_enabled: bool = True   # 監視ティック(絡み案の自動生成)を動かすか
    auto_post_enabled: bool = True      # キューティック(予約分の自動投稿)を動かすか
    # 1監視サイクルで作る下書きの総数上限。一気に生成しすぎてAPIを圧迫しないための安全弁。
    max_drafts_per_run: int = 10
    updated_at: datetime = Field(default_factory=_utcnow)


class BlackoutSettings(SQLModel, table=True):
    """投稿/リポスト等の自分の書き込みを禁止する時間帯(制限帯)の設定。

    単一行(id=1)。指定曜日(既定 月〜金)の指定時間帯(JST)は、自分の公開書き込みを
    一切ブロックする。土日は既定で対象外。監視(読み取り)はブロックしない。
    二段階確認(警告を無視→最終確認)を通した操作だけ override で投稿できる。
    weekdays_json: 制限する曜日(月=0..日=6)のJSON配列。
    windows_json: [["HH:MM","HH:MM"], ...] のJSON配列(その日の制限時間帯)。
    """

    id: int | None = Field(default=None, primary_key=True)
    enabled: bool = True
    weekdays_json: str = "[0, 1, 2, 3, 4]"  # 月〜金
    windows_json: str = '[["09:00", "12:00"], ["13:00", "19:00"]]'
    updated_at: datetime = Field(default_factory=_utcnow)


class CostKind(str, Enum):
    READ = "read"    # X API 読み取り $0.005/件
    WRITE = "write"  # X API 投稿 $0.01/件
    TL = "tl"        # X API タイムライン取得 $0.01/件
    LLM = "llm"      # Claude(Anthropic) API。トークン課金。cost_usd に実額を入れる


class ApiCostLog(SQLModel, table=True):
    """X API 従量課金のコスト記録(監視コストの可視化用)。"""

    id: int | None = Field(default=None, primary_key=True)
    kind: CostKind
    units: int = 1
    cost_usd: float = 0.0
    note: str | None = None
    ts: datetime = Field(default_factory=_utcnow)
