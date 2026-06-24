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
    # 元ポストのエンゲージ指標(取得できた時のみ=None は不明)。絡み案の承認判断材料として表示する。
    # view_count=インプレッション。twitterapi.io 経由でのみ取れ、公式APIフォールバック経路では None。
    target_view_count: int | None = None
    target_like_count: int | None = None
    target_retweet_count: int | None = None

    scheduled_at: datetime | None = None  # 指定時刻/最適時間の予約
    posted_at: datetime | None = None
    posted_tweet_id: str | None = None

    # 制限時間帯(平日の指定帯)でも投稿してよいか。二段階確認(警告を無視→最終確認)で True にする。
    # 予約投稿はUIから人がいない時刻に発火するため、許可をこの列に保存しておく。
    blackout_override: bool = False

    # 予約時刻にPCオフ等で投稿できず失効した印。承認済みに戻して再予約を促すためのフラグ。
    # 再予約(queue_draft)時にクリアする。古い予約を遅れて投稿しないための安全弁。
    schedule_missed: bool = False

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
    NEWS = "news"      # ニュース速報の型(N1〜N5。大手の実投稿から抽出)


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
    # 既定OFF: 自分宛メンション(自分のポストへの返信)は手動で返す方針。自動生成しない。
    # トグルは残すのでONにすれば poll_mentions で返信案を作れる。
    mentions_enabled: bool = False
    manual_targets_enabled: bool = True
    keyword_search_enabled: bool = False
    following_enabled: bool = False
    # 絡み案の自動生成スイッチ。既定OFF: 絡み生成は自動では回さず手動スキャンでのみ行う方針
    # (APIコスト節約)。スタンドアロン `daemon run()` 経由で使う場合のみ意味を持つ。
    # 注: API常駐(launchd)では monitor ジョブ自体をスケジューラに登録しない(main.py)。
    auto_monitor_enabled: bool = False
    # 予約投稿は常時実行する方針のため現在は未使用(緊急停止は config.posting_enabled が担う)。
    # 互換のため列は残す。
    auto_post_enabled: bool = True
    # 1監視サイクルで作る下書きの総数上限。一気に生成しすぎてAPIを圧迫しないための安全弁。
    max_drafts_per_run: int = 10
    # 絡み候補の最低インプレッション。これ未満の投稿には絡まない(伸びていない投稿への
    # リプは露出が取れないため)。view数が取得できない投稿は判定不能として通す。
    min_impressions: int = 10000
    # 有名人ウォッチ: celeb_list_id のXリストのメンバーが AI について投稿したら即絡み案を作る。
    # 検出は検索1〜2クエリ/回(タイムライン巡回なし・API節約)。既定OFF。
    # 有名人は min_impressions の対象外(投稿直後の素早い反応を優先)。
    celeb_watch_enabled: bool = False
    celeb_list_id: str | None = None
    # バズウォッチ: アカウントを問わず「既にバズった投稿」(min_faves検索)を網羅的に拾い
    # 絡み案を作る。バズ予測はせず、付いたいいね数という実績だけで機械的に検出する。既定OFF。
    buzz_watch_enabled: bool = False
    buzz_min_faves: int = 3000
    updated_at: datetime = Field(default_factory=_utcnow)


class NewsSettings(SQLModel, table=True):
    """ニュース速報投稿(XNewsBot連携)の設定。単一行(id=1)。

    XNewsBot(別プロジェクト)が朝夕に収集したニュースDBを読み取り専用で参照し、
    対象ジャンルの新着から速報風の下書きを生成する。生成は下書きまで(投稿は人間承認必須)。
    自動生成はダイジェスト連動(新着が現れたときだけ)で、トグル既定OFF(乱造防止)。
    """

    id: int | None = Field(default=None, primary_key=True)
    auto_news_enabled: bool = False
    genres_json: str = '["AI", "テクノロジー"]'  # 対象ジャンル(XNewsBotのジャンル名)
    max_posts_per_run: int = 3  # 1回の生成で作る下書きの上限(乱造防止の安全弁)
    # 処理済みの XNewsBot genredigest.id。これ以下のダイジェストは生成済みとして再処理しない。
    last_digest_id: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)


class PostSettings(SQLModel, table=True):
    """オリジナル投稿の叩き台生成(A機能)の設定。単一行(id=1)。

    フォロー転換の受け皿=オリジナル発信が月2件と欠落しているため、型に沿った叩き台を
    定期生成して下書きにする。中身の一次情報(実体験・数字)はユーザーが埋める前提。
    生成系のため既定OFF・乱造ガード(承認待ちPOSTが post_backlog_max 以上ならスキップ)。
    """

    id: int | None = Field(default=None, primary_key=True)
    auto_post_gen_enabled: bool = False  # 既定OFF(乱造防止・恒久方針)。ONはユーザーが押す
    max_posts_per_run: int = 2
    post_backlog_max: int = 10  # 承認待ちPOST下書きがこれ以上なら自動生成スキップ
    # 叩き台の切り口(テーマ角度)。formatter に渡して型に沿った叩き台を作らせる。
    themes_json: str = (
        '["AIを営業/BtoBの現場で使って何が変わったか(一次情報・数字)",'
        ' "AI活用でやりがちな勘違い・つまずきと正しいやり方",'
        ' "実際に試したAIツール/ワークフローの所感",'
        ' "AI時代の営業・キャリアについての持論"]'
    )
    updated_at: datetime = Field(default_factory=_utcnow)


class MetricsSettings(SQLModel, table=True):
    """自分の投稿メトリクス取得(B機能)の設定。単一行(id=1)。

    読み取りのみ(生成しない)で1日数リクエストと軽量・無害なため既定ON。
    トグルは用意しOFFも可能。lookback_days 分の自分の投稿を定期取得して PostMetric に保存する。
    """

    id: int | None = Field(default=None, primary_key=True)
    metrics_enabled: bool = True  # 読み取りのみ・無害なので既定ON
    lookback_days: int = 30
    updated_at: datetime = Field(default_factory=_utcnow)


class PostMetric(SQLModel, table=True):
    """自分の投稿(手動リプ含む)の実績メトリクス。tweet_id で upsert する。

    impression_count は公式API v2 の non_public_metrics 由来(自分のツイートのみ・
    OAuth1.0a user context で取得可)。改善を数字で追えるようにするための可視化用。
    """

    id: int | None = Field(default=None, primary_key=True)
    tweet_id: str = Field(default="", index=True)
    kind: str = ""  # post / reply / quote / repost
    text: str = ""
    created_at: datetime | None = None  # X上の投稿時刻(naive UTC)
    captured_at: datetime = Field(default_factory=_utcnow)
    impression_count: int | None = None  # non_public_metrics(自分のツイートのみ)
    like_count: int = 0
    retweet_count: int = 0
    reply_count: int = 0
    quote_count: int = 0
    bookmark_count: int = 0


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


class TwitterApiKey(SQLModel, table=True):
    """twitterapi.io(読み取り用)のAPIキー。複数登録し優先度順にフォールバックする。

    他人投稿の読み取りで priority 昇順(小さいほど先)に各キーを試し、失敗(残高切れ402・
    タイムアウト等)なら次のキーへ。全キー失敗で初めて公式APIへフォールバックする
    (x_client._read)。キーは秘密情報のため一覧APIではマスク表示し、ローカルSQLite
    (非公開・API_TOKEN認証下)にのみ保存する。.env の TWITTERAPI_IO_KEY は init_db で
    DBが空のとき1件だけ取り込む(以後はDBが正)。
    """

    id: int | None = Field(default=None, primary_key=True)
    label: str = ""               # UI表示用の名前(例: メイン/予備1)。空でも可
    api_key: str                  # twitterapi.io の X-API-Key(秘密)
    priority: int = 100           # 小さいほど優先。同値は id 昇順
    enabled: bool = True          # OFFのキーはフォールバック対象から外す
    last_ok_at: datetime | None = None  # 最後に疎通成功した時刻(UIの「テスト」で更新)
    last_error: str | None = None       # 最後の疎通失敗の内容(UIの「テスト」で更新)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
