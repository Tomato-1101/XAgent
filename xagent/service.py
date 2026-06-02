"""サービス層。下書きの生成→承認→キュー→投稿を、ガードとコスト記録込みで束ねる。

DBセッションは引数で受け取り(テスト容易性のため)、外部依存(formatter / x_client)も注入する。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from . import maintenance
from .blackout import get_blackout_settings, is_blackout
from .config import Settings, get_settings
from .cost import bill_formatter_usage, log_cost
from .formatter import Formatter
from .guards import (
    PolicyViolation,
    PostTrigger,
    RateLimitConfig,
    RateLimiter,
    can_transition,
    ensure_not_blackout,
    ensure_post_authorized,
)
from .media import validate_media_set
from .models import CostKind, Draft, DraftKind, DraftStatus, TemplateKind
from .profiles import example_posts_for_account, get_profile_by_handle
from .style import active_style_guide
from . import templates as templates_mod
from .text import split_into_thread
from .x_client import XClient


def _emulate_inputs(session: Session, emulate_handle: str | None) -> tuple[str, list[str] | None]:
    """「真似る相手」が指定されていれば、その特徴テキストと代表投稿を返す(選択時のみ)。"""
    if not emulate_handle:
        return "", None
    prof = get_profile_by_handle(session, emulate_handle)
    if prof is None:
        return "", None
    return prof.profile_text or "", example_posts_for_account(session, emulate_handle)


def _playbook_for(
    session: Session,
    formatter: Formatter,
    kind: TemplateKind,
    source_text: str,
    template_id: int | None,
    auto_template: bool,
) -> str:
    """使う「型」の本文を返す。auto_template時はAIが候補から最適な型を選ぶ(「AIに任せる」)。"""
    if auto_template:
        candidates = templates_mod.list_templates(session, kind)
        template_id = templates_mod.choose_template(formatter.complete, candidates, source_text)
    return templates_mod.resolve_body(session, template_id)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """予約時刻を内部表現(naive UTC)へ正規化する。

    API/CLI から aware datetime(+09:00 や Z 付き)が来ても、内部の naive UTC 比較
    (scheduler.due_drafts)とフロントの表示(+\"Z\")が壊れないように揃える。
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _segments(draft: Draft) -> list[str]:
    return json.loads(draft.segments_json or "[]")


def _media(draft: Draft) -> list[str]:
    return json.loads(draft.media_paths_json or "[]")


def _finalize_draft(
    session: Session, formatter: Formatter, draft: Draft, note: str
) -> Draft:
    """下書きを永続化し、Claudeコストを記録、DB容量上限を点検する共通処理。"""
    session.add(draft)
    bill_formatter_usage(session, formatter, note=note)
    session.commit()
    session.refresh(draft)
    maintenance.enforce_db_capacity(session)
    return draft


# --- 下書き生成 -------------------------------------------------------------

def create_post_draft(
    session: Session,
    formatter: Formatter,
    source_text: str,
    style_guide: str | None = None,
    allow_long: bool = False,
    media_paths: list[str] | None = None,
    emulate_handle: str | None = None,
    raw: bool = False,
    template_id: int | None = None,
    auto_template: bool = False,
) -> Draft:
    """テキストを整形して未承認の下書きを作る。

    常時適用は手入力のスタイルガイド(active_style_guide)のみ。学習データは自動注入しない。
    emulate_handle を指定したときだけ、そのアカウントの特徴・代表投稿を整形に乗せる。
    template_id/auto_template で「型」を適用(auto_template=AIが最適な型を自動選択)。
    raw=True なら整形(LLM)を行わず、入力をそのまま本文にする(スレッド分割のみ)。
    """
    validate_media_set(media_paths)
    if raw:
        segments = [source_text.strip()] if allow_long else split_into_thread(source_text)
    else:
        sg = style_guide if style_guide is not None else active_style_guide(session)
        emulate_text, emulate_examples = _emulate_inputs(session, emulate_handle)
        playbook = _playbook_for(
            session, formatter, TemplateKind.POST, source_text, template_id, auto_template
        )
        res = formatter.format_post(
            source_text, sg, allow_long=allow_long,
            emulate_profile_text=emulate_text, emulate_examples=emulate_examples,
            playbook=playbook,
        )
        segments = res.segments
    draft = Draft(
        kind=DraftKind.POST,
        status=DraftStatus.DRAFT,
        source_text=source_text,
        segments_json=json.dumps(segments, ensure_ascii=False),
        media_paths_json=json.dumps(media_paths or [], ensure_ascii=False),
    )
    return _finalize_draft(session, formatter, draft, "compose")


def create_post_variations(
    session: Session,
    formatter: Formatter,
    source_text: str,
    n: int = 3,
    style_guide: str | None = None,
    allow_long: bool = False,
    emulate_handle: str | None = None,
    media_paths: list[str] | None = None,
    template_id: int | None = None,
    auto_template: bool = False,
) -> list[Draft]:
    """1つのメモから言い回し違いのn案を未承認下書きとして作る(同じメディアを各案に付与)。"""
    validate_media_set(media_paths)
    sg = style_guide if style_guide is not None else active_style_guide(session)
    emulate_text, emulate_examples = _emulate_inputs(session, emulate_handle)
    playbook = _playbook_for(
        session, formatter, TemplateKind.POST, source_text, template_id, auto_template
    )
    results = formatter.format_variations(
        source_text, n=n, style_guide=sg, allow_long=allow_long,
        emulate_profile_text=emulate_text, emulate_examples=emulate_examples,
        playbook=playbook,
    )
    drafts: list[Draft] = []
    for res in results:
        d = Draft(
            kind=DraftKind.POST,
            status=DraftStatus.DRAFT,
            source_text=source_text,
            segments_json=json.dumps(res.segments, ensure_ascii=False),
            media_paths_json=json.dumps(media_paths or [], ensure_ascii=False),
        )
        session.add(d)
        drafts.append(d)
    bill_formatter_usage(session, formatter, note="compose-variations")
    session.commit()
    for d in drafts:
        session.refresh(d)
    maintenance.enforce_db_capacity(session)
    return drafts


def create_reply_draft(
    session: Session,
    formatter: Formatter,
    target_tweet_id: str,
    target_text: str,
    target_handle: str | None = None,
) -> Draft:
    res = formatter.generate_reply(
        target_text, target_handle or "", active_style_guide(session),
        playbook=templates_mod.active_body(session, TemplateKind.REPLY),
    )
    draft = Draft(
        kind=DraftKind.REPLY,
        status=DraftStatus.DRAFT,
        segments_json=json.dumps(res.segments, ensure_ascii=False),
        target_tweet_id=target_tweet_id,
        target_handle=target_handle,
        target_text=target_text,  # 元ポスト本文を表示用に保持(人間が承認判断できるように)
    )
    return _finalize_draft(session, formatter, draft, "reply")


def create_quote_draft(
    session: Session,
    formatter: Formatter,
    target_tweet_id: str,
    target_text: str,
    target_handle: str | None = None,
) -> Draft:
    res = formatter.generate_quote(
        target_text, target_handle or "", active_style_guide(session),
        playbook=templates_mod.active_body(session, TemplateKind.QUOTE),
    )
    draft = Draft(
        kind=DraftKind.QUOTE,
        status=DraftStatus.DRAFT,
        segments_json=json.dumps(res.segments, ensure_ascii=False),
        target_tweet_id=target_tweet_id,
        target_handle=target_handle,
        target_text=target_text,  # 引用元の本文を表示用に保持
    )
    return _finalize_draft(session, formatter, draft, "quote")


def _compose_command_segments(
    session: Session,
    formatter: Formatter,
    comment: str,
    raw: bool,
    allow_long: bool,
    style_guide: str | None,
    emulate_handle: str | None,
    playbook: str = "",
) -> list[str]:
    """指令フロー(quote/reply)で、自分のコメントを raw or 整形して本文セグメントにする共通処理。"""
    if raw:
        return [comment.strip()] if allow_long else split_into_thread(comment)
    sg = style_guide if style_guide is not None else active_style_guide(session)
    emulate_text, emulate_examples = _emulate_inputs(session, emulate_handle)
    res = formatter.format_post(
        comment, sg, allow_long=allow_long,
        emulate_profile_text=emulate_text, emulate_examples=emulate_examples,
        playbook=playbook,
    )
    return res.segments


def create_quote_command_draft(
    session: Session,
    formatter: Formatter,
    target_tweet_id: str,
    comment: str,
    target_handle: str | None = None,
    target_text: str = "",
    raw: bool = False,
    style_guide: str | None = None,
    allow_long: bool = False,
    emulate_handle: str | None = None,
    media_paths: list[str] | None = None,
    template_id: int | None = None,
    auto_template: bool = False,
) -> Draft:
    """指令フロー用: ユーザー/エージェント自身のコメントを引用RTの本文として下書き化する。

    create_quote_draft は相手投稿から AI がコメントを生成するが、こちらは
    自分が書いたコメントを口調整形(または raw でそのまま)して引用に添える。
    target_text には引用元の本文(取得できれば)を表示用に保持する。
    """
    validate_media_set(media_paths)
    playbook = (
        ""
        if raw
        else _playbook_for(
            session, formatter, TemplateKind.QUOTE, comment, template_id, auto_template
        )
    )
    segments = _compose_command_segments(
        session, formatter, comment, raw, allow_long, style_guide, emulate_handle, playbook
    )
    draft = Draft(
        kind=DraftKind.QUOTE,
        status=DraftStatus.DRAFT,
        source_text=comment,
        segments_json=json.dumps(segments, ensure_ascii=False),
        media_paths_json=json.dumps(media_paths or [], ensure_ascii=False),
        target_tweet_id=target_tweet_id,
        target_handle=target_handle,
        target_text=target_text,
    )
    return _finalize_draft(session, formatter, draft, "command-quote")


def create_reply_command_draft(
    session: Session,
    formatter: Formatter,
    target_tweet_id: str,
    comment: str,
    target_handle: str | None = None,
    target_text: str = "",
    raw: bool = False,
    style_guide: str | None = None,
    allow_long: bool = False,
    emulate_handle: str | None = None,
    media_paths: list[str] | None = None,
    template_id: int | None = None,
    auto_template: bool = False,
) -> Draft:
    """指令フロー用: 指定ツイートに対し、自分(ユーザー/エージェント)が書いた文でリプライする下書き。

    create_reply_draft は相手投稿から AI が返信文を生成するが、こちらは
    自分が書いた文を口調整形(または raw でそのまま)して返信本文にする。
    target_text には返信先の本文(取得できれば)を表示用に保持する。
    """
    validate_media_set(media_paths)
    playbook = (
        ""
        if raw
        else _playbook_for(
            session, formatter, TemplateKind.REPLY, comment, template_id, auto_template
        )
    )
    segments = _compose_command_segments(
        session, formatter, comment, raw, allow_long, style_guide, emulate_handle, playbook
    )
    draft = Draft(
        kind=DraftKind.REPLY,
        status=DraftStatus.DRAFT,
        source_text=comment,
        segments_json=json.dumps(segments, ensure_ascii=False),
        media_paths_json=json.dumps(media_paths or [], ensure_ascii=False),
        target_tweet_id=target_tweet_id,
        target_handle=target_handle,
        target_text=target_text,
    )
    return _finalize_draft(session, formatter, draft, "command-reply")


def create_repost_draft(
    session: Session,
    target_tweet_id: str,
    target_text: str = "",
    target_handle: str | None = None,
) -> Draft:
    """自分の投稿の通常リポスト(コメント無し)の下書きを作る。整形(LLM)は不要。

    本文セグメントは持たず、target_tweet_id を RT 対象にする。
    target_text は一覧表示用にリポスト元の本文を控えとして保存する。
    """
    draft = Draft(
        kind=DraftKind.REPOST,
        status=DraftStatus.DRAFT,
        segments_json="[]",
        target_tweet_id=target_tweet_id,
        target_handle=target_handle,
        target_text=target_text,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    maintenance.enforce_db_capacity(session)
    return draft


# --- 取得・状態遷移 ---------------------------------------------------------

def get_draft(session: Session, draft_id: int) -> Draft | None:
    return session.get(Draft, draft_id)


def list_drafts(
    session: Session,
    status: DraftStatus | None = None,
    kind: DraftKind | None = None,
) -> list[Draft]:
    stmt = select(Draft)
    if status is not None:
        stmt = stmt.where(Draft.status == status)
    if kind is not None:
        stmt = stmt.where(Draft.kind == kind)
    stmt = stmt.order_by(Draft.created_at.desc())
    return list(session.exec(stmt).all())


def _set_status(session: Session, draft: Draft, new: DraftStatus) -> Draft:
    if not can_transition(draft.status, new):
        raise PolicyViolation(
            f"不正な状態遷移: {draft.status.value} -> {new.value}"
        )
    draft.status = new
    draft.updated_at = _utcnow()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def update_segments(session: Session, draft: Draft, segments: list[str]) -> Draft:
    """承認前に本文を手修正する。"""
    draft.segments_json = json.dumps(segments, ensure_ascii=False)
    draft.updated_at = _utcnow()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def approve_draft(session: Session, draft: Draft) -> Draft:
    return _set_status(session, draft, DraftStatus.APPROVED)


def reject_draft(session: Session, draft: Draft) -> Draft:
    return _set_status(session, draft, DraftStatus.REJECTED)


def cancel_draft(session: Session, draft: Draft) -> Draft:
    """取消(ゴミ箱へ)。予約済みなら予約も解除し、自動投稿されないようにする。

    行はDBに残す(容量超過時に古い順で物理削除)。投稿済みは取消できない。
    """
    if draft.status == DraftStatus.QUEUED:
        draft.scheduled_at = None  # 予約解除(到来しても投稿対象にならない)
    return _set_status(session, draft, DraftStatus.CANCELED)


def restore_draft(session: Session, draft: Draft) -> Draft:
    """取消(ゴミ箱)から下書きへ復元する。"""
    return _set_status(session, draft, DraftStatus.DRAFT)


def queue_draft(
    session: Session,
    draft: Draft,
    scheduled_at: datetime | None = None,
    blackout_override: bool = False,
) -> Draft:
    if scheduled_at is not None:
        draft.scheduled_at = to_naive_utc(scheduled_at)
    if blackout_override:
        # 制限帯への予約を二段階確認した → 発火時刻に人がいなくても投稿を許可する
        draft.blackout_override = True
    return _set_status(session, draft, DraftStatus.QUEUED)


# --- 投稿 -------------------------------------------------------------------

def recent_posted_times(session: Session) -> list[datetime]:
    stmt = select(Draft.posted_at).where(Draft.posted_at != None)  # noqa: E711
    return [t for t in session.exec(stmt).all() if t is not None]


def _rate_limiter(settings: Settings) -> RateLimiter:
    return RateLimiter(
        RateLimitConfig(
            max_per_day=settings.max_posts_per_day,
            hard_cap_per_day=settings.hard_cap_posts_per_day,
            min_interval_seconds=settings.min_post_interval_seconds,
        )
    )


def post_draft(
    session: Session,
    x_client: XClient,
    draft: Draft,
    settings: Settings | None = None,
    now: datetime | None = None,
    trigger: PostTrigger = PostTrigger.MANUAL,
    override: bool = False,
) -> list[str]:
    """下書きを実際にXへ投稿する。ガード(緊急停止/認証・予約/制限帯/頻度)を必ず通す。

    trigger=MANUAL(既定): 人の明示操作。MANUAL は承認済み/キュー済みのみ可。
    trigger=SCHEDULED: スケジューラ発火。予約時刻のある到来済みキューのみ可。
    override: 制限時間帯でも投稿してよいか(手動の二段階確認 or 予約時に保存した許可)。
    """
    settings = settings or get_settings()
    now = now or _utcnow()

    if not settings.posting_enabled:
        raise PolicyViolation("投稿は停止中です(posting_enabled=False)。")

    # 認証(人の明示操作) か 予約(到来済みscheduled_at) でなければ投稿しない
    ensure_post_authorized(draft.status, draft.scheduled_at, trigger, now)

    # 制限時間帯(平日の指定帯)は、二段階確認(override)が無い限りブロックする
    in_blackout, reason = is_blackout(now, get_blackout_settings(session), settings.timezone)
    ensure_not_blackout(in_blackout, override or draft.blackout_override, reason)

    decision = _rate_limiter(settings).check(recent_posted_times(session), now)
    if not decision.allowed:
        raise PolicyViolation(decision.reason)

    if draft.kind == DraftKind.REPOST:
        # 通常リポスト(コメント無し)。本文は不要、対象IDをRTする。
        if not draft.target_tweet_id:
            raise PolicyViolation("リポスト対象のツイートIDがありません。")
        ids = [x_client.retweet(draft.target_tweet_id)]
        log_cost(session, CostKind.WRITE, units=1, note=f"repost#{draft.id}")
        draft.posted_at = now
        draft.posted_tweet_id = draft.target_tweet_id  # RTには新規IDが無いため対象IDを記録
        draft.status = DraftStatus.POSTED
        draft.updated_at = now
        session.add(draft)
        session.commit()
        session.refresh(draft)
        return ids

    segments = _segments(draft)
    if not segments:
        raise PolicyViolation("本文が空の下書きは投稿できません。")

    media_ids: list[str] | None = None
    media_paths = _media(draft)
    if media_paths:
        media_ids = [x_client.upload_media(p) for p in media_paths]

    if draft.kind == DraftKind.REPLY:
        ids = [
            x_client.post(
                segments[0], in_reply_to_tweet_id=draft.target_tweet_id, media_ids=media_ids
            )
        ]
    elif draft.kind == DraftKind.QUOTE:
        ids = [
            x_client.post(
                segments[0], quote_tweet_id=draft.target_tweet_id, media_ids=media_ids
            )
        ]
    else:  # POST(スレッド対応)
        ids = x_client.post_thread(segments, media_ids_first=media_ids)

    log_cost(session, CostKind.WRITE, units=len(ids), note=f"draft#{draft.id}")

    draft.posted_at = now
    draft.posted_tweet_id = ids[0]
    draft.status = DraftStatus.POSTED
    draft.updated_at = now
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return ids
