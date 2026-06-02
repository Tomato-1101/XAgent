"""API入出力スキーマ。DBモデル(Draft)を、segmentsをパースした読みやすい形で返す。"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel

from ..models import Draft, DraftKind, DraftStatus, TargetKind, TemplateKind


class DraftRead(BaseModel):
    id: int | None
    kind: DraftKind
    status: DraftStatus
    source_text: str
    segments: list[str]
    media_paths: list[str]
    target_tweet_id: str | None
    target_handle: str | None
    target_text: str = ""  # 絡む相手の元ポスト本文(reply/quote/repost の表示用)
    target_created_at: datetime | None = None  # 元ポストの投稿時刻(取得できた時のみ)
    scheduled_at: datetime | None
    posted_at: datetime | None
    posted_tweet_id: str | None
    blackout_override: bool = False
    created_at: datetime
    updated_at: datetime


def draft_to_read(d: Draft) -> DraftRead:
    return DraftRead(
        id=d.id,
        kind=d.kind,
        status=d.status,
        source_text=d.source_text,
        segments=json.loads(d.segments_json or "[]"),
        media_paths=json.loads(d.media_paths_json or "[]"),
        target_tweet_id=d.target_tweet_id,
        target_handle=d.target_handle,
        target_text=d.target_text,
        target_created_at=d.target_created_at,
        scheduled_at=d.scheduled_at,
        posted_at=d.posted_at,
        posted_tweet_id=d.posted_tweet_id,
        blackout_override=d.blackout_override,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


class ComposeRequest(BaseModel):
    text: str
    allow_long: bool = False
    style_guide: str | None = None
    media_paths: list[str] = []
    emulate_handle: str | None = None  # 真似るアカウント(学習済みのみ有効)
    n_variations: int = 1              # 1なら単発、2以上で言い回し違いをN案
    raw: bool = False                  # Trueなら整形(LLM)せず入力をそのまま投稿
    template_id: int | None = None     # 使う「型」。Noneで型なし
    auto_template: bool = False        # TrueでAIが最適な型を自動選択(「AIに任せる」)


class InterpretRequest(BaseModel):
    text: str


class InterpretResponse(BaseModel):
    """自由文の指令解析結果(下書きは作らず、UIで確認するための情報)。"""

    action: str  # "quote" | "post"
    target_url: str | None
    target_tweet_id: str | None
    target_handle: str | None
    body: str
    raw: bool
    note: str


class CommandRequest(BaseModel):
    """確認済みの指令から下書きを作るための入力。"""

    action: str  # "quote" | "reply" | "post"
    text: str    # 投稿本文(引用/返信なら自分のコメント)
    target_tweet_id: str | None = None
    target_handle: str | None = None
    raw: bool = False
    allow_long: bool = False
    emulate_handle: str | None = None
    media_paths: list[str] = []
    style_guide: str | None = None
    template_id: int | None = None     # 使う「型」。Noneで型なし
    auto_template: bool = False        # TrueでAIが最適な型を自動選択(「AIに任せる」)


class TemplateRead(BaseModel):
    id: int | None
    name: str
    kind: TemplateKind
    body: str
    active: bool
    builtin: bool


class TemplateCreate(BaseModel):
    name: str
    kind: TemplateKind = TemplateKind.POST
    body: str = ""
    active: bool = False


class TemplateUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    kind: TemplateKind | None = None
    active: bool | None = None


class ProfileLearnRequest(BaseModel):
    handle: str
    max_total: int = 200
    is_self: bool = False


class MonitorSettingsRequest(BaseModel):
    mentions_enabled: bool | None = None
    manual_targets_enabled: bool | None = None
    keyword_search_enabled: bool | None = None
    following_enabled: bool | None = None
    auto_monitor_enabled: bool | None = None  # デーモン: 自動監視(絡み案生成)
    auto_post_enabled: bool | None = None      # デーモン: 予約分の自動投稿
    max_drafts_per_run: int | None = None  # 1監視サイクルの総生成数上限


class UpdateDraftRequest(BaseModel):
    segments: list[str] | None = None
    scheduled_at: datetime | None = None


class QueueRequest(BaseModel):
    mode: str = "optimal"  # "optimal" | "time"
    scheduled_at: datetime | None = None
    override: bool = False  # 制限時間帯への予約を二段階確認で許可したか


class PostNowRequest(BaseModel):
    override: bool = False  # 制限時間帯でも投稿するか(二段階確認済み)


# --- 自分の直近投稿 / リポスト ----------------------------------------------

class RecentPost(BaseModel):
    tweet_id: str
    text: str
    created_at: datetime | None
    like_count: int
    retweet_count: int
    url: str


class RepostRequest(BaseModel):
    mode: str = "now"  # "now"=即時リポスト / "time"=指定時刻に予約
    scheduled_at: datetime | None = None
    text: str = ""     # 一覧表示用に元投稿本文の控え(任意)
    override: bool = False


# --- 制限時間帯(ブラックアウト) --------------------------------------------

class BlackoutRead(BaseModel):
    enabled: bool
    weekdays: list[int]            # 月=0..日=6
    windows: list[list[str]]       # [["09:00","12:00"], ...]
    updated_at: datetime | None = None


class BlackoutUpdateRequest(BaseModel):
    enabled: bool | None = None
    weekdays: list[int] | None = None
    windows: list[list[str]] | None = None


class BlackoutStatus(BaseModel):
    blackout: bool
    reason: str
    at: datetime  # 判定に使った時刻(naive UTC)


class StyleRequest(BaseModel):
    guide_text: str


class TargetRequest(BaseModel):
    handle: str
    kind: TargetKind = TargetKind.MANUAL
    list_id: str | None = None    # kind=LIST時のXリストID(handleはリスト名)
    keyword: str | None = None
    notes: str | None = None
    resolve_user_id: bool = True  # X APIでuser_idを解決するか


# --- Xネイティブ「リスト」 ---
class ListRead(BaseModel):
    id: str
    name: str
    description: str = ""
    private: bool = False
    member_count: int = 0


class ListMember(BaseModel):
    id: str
    username: str | None = None
    name: str | None = None
    description: str = ""
    profile_image_url: str | None = None
    followers_count: int = 0


class ListCreateRequest(BaseModel):
    name: str
    accounts: list[str] = []  # ハンドル一覧(@有無どちらでも可)
    description: str = ""
    private: bool = True


class ListCreateResult(BaseModel):
    list_id: str
    url: str
    name: str
    added: list[str]
    skipped: list[dict]


class ListUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    private: bool | None = None


class ListMemberAddRequest(BaseModel):
    handle: str | None = None
    user_id: str | None = None
