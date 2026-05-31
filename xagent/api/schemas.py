"""API入出力スキーマ。DBモデル(Draft)を、segmentsをパースした読みやすい形で返す。"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel

from ..models import Draft, DraftKind, DraftStatus, TargetKind


class DraftRead(BaseModel):
    id: int | None
    kind: DraftKind
    status: DraftStatus
    source_text: str
    segments: list[str]
    media_paths: list[str]
    target_tweet_id: str | None
    target_handle: str | None
    scheduled_at: datetime | None
    posted_at: datetime | None
    posted_tweet_id: str | None
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
        scheduled_at=d.scheduled_at,
        posted_at=d.posted_at,
        posted_tweet_id=d.posted_tweet_id,
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


class ProfileLearnRequest(BaseModel):
    handle: str
    max_total: int = 200
    is_self: bool = False


class MonitorSettingsRequest(BaseModel):
    mentions_enabled: bool | None = None
    manual_targets_enabled: bool | None = None
    keyword_search_enabled: bool | None = None
    following_enabled: bool | None = None


class UpdateDraftRequest(BaseModel):
    segments: list[str] | None = None
    scheduled_at: datetime | None = None


class QueueRequest(BaseModel):
    mode: str = "optimal"  # "optimal" | "time"
    scheduled_at: datetime | None = None


class StyleRequest(BaseModel):
    guide_text: str


class TargetRequest(BaseModel):
    handle: str
    kind: TargetKind = TargetKind.MANUAL
    keyword: str | None = None
    notes: str | None = None
    resolve_user_id: bool = True  # X APIでuser_idを解決するか
