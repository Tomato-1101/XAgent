"""受信監視。メンション/自分宛リプライと、絡み対象(有名人等)の新規投稿をポーリングし、
返信案・引用RT案を「下書き(未承認)」として生成する。承認は人間が行う。

ストリーム不可のため定期ポーリング。MonitorCursor で since_id を管理し重複を防ぐ。
"""

from __future__ import annotations

from sqlmodel import Session, select

from .formatter import Formatter
from .models import EngageTarget, MonitorCursor, MonitorSettings, TargetKind
from .service import create_quote_draft, create_reply_draft
from .x_client import XClient

FOLLOWING_MAX_ACCOUNTS = 20  # フォロー中巡回の1ティック上限(コスト抑制)


def get_monitor_settings(session: Session) -> MonitorSettings:
    """監視トグル設定(単一行)。無ければ既定で作成する。"""
    row = session.exec(select(MonitorSettings)).first()
    if row is None:
        row = MonitorSettings()  # 既定: mentions/manual=ON, keyword/following=OFF
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def set_monitor_settings(session: Session, **flags: bool) -> MonitorSettings:
    """トグルを更新する(指定キーのみ)。"""
    row = get_monitor_settings(session)
    for k, v in flags.items():
        if hasattr(row, k) and isinstance(v, bool):
            setattr(row, k, v)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _get_cursor(session: Session, stream: str) -> MonitorCursor:
    cur = session.exec(
        select(MonitorCursor).where(MonitorCursor.stream == stream)
    ).first()
    if cur is None:
        cur = MonitorCursor(stream=stream, last_seen_id=None)
        session.add(cur)
        session.commit()
        session.refresh(cur)
    return cur


def _max_id(ids: list[str]) -> str | None:
    nums = [int(i) for i in ids if i and i.isdigit()]
    return str(max(nums)) if nums else None


def poll_mentions(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str
) -> int:
    """自分宛メンションを取得し、返信案の下書きを作る。生成件数を返す。"""
    cur = _get_cursor(session, "mentions")
    tweets = x_client.get_mentions(me_user_id, since_id=cur.last_seen_id)
    created = 0
    for t in tweets:
        create_reply_draft(
            session, formatter, t["id"], t.get("text", ""), target_handle=t.get("author_id")
        )
        created += 1
    new_max = _max_id([t["id"] for t in tweets])
    if new_max:
        cur.last_seen_id = new_max
        session.add(cur)
        session.commit()
    return created


def poll_targets(
    session: Session, x_client: XClient, formatter: Formatter
) -> int:
    """手動リスト等(user_id付き・GENRE以外)の新規投稿→引用RT案。生成件数を返す。"""
    targets = session.exec(
        select(EngageTarget).where(
            EngageTarget.active == True,  # noqa: E712
            EngageTarget.user_id != None,  # noqa: E711
            EngageTarget.kind != TargetKind.GENRE,
        )
    ).all()
    created = 0
    for target in targets:
        cur = _get_cursor(session, f"target:{target.user_id}")
        tweets = x_client.get_user_timeline(target.user_id, since_id=cur.last_seen_id)
        for t in tweets:
            create_quote_draft(
                session, formatter, t["id"], t.get("text", ""), target_handle=target.handle
            )
            created += 1
        new_max = _max_id([t["id"] for t in tweets])
        if new_max:
            cur.last_seen_id = new_max
            session.add(cur)
            session.commit()
    return created


def poll_genre(session: Session, x_client: XClient, formatter: Formatter) -> int:
    """ジャンル/キーワード探索(GENRE対象)→該当投稿に引用RT案。生成件数を返す。"""
    targets = session.exec(
        select(EngageTarget).where(
            EngageTarget.active == True,  # noqa: E712
            EngageTarget.kind == TargetKind.GENRE,
            EngageTarget.keyword != None,  # noqa: E711
        )
    ).all()
    created = 0
    for target in targets:
        cur = _get_cursor(session, f"genre:{target.keyword}")
        tweets = x_client.search_recent(target.keyword, since_id=cur.last_seen_id)
        for t in tweets:
            create_quote_draft(
                session, formatter, t["id"], t.get("text", ""),
                target_handle=t.get("author_id"),
            )
            created += 1
        new_max = _max_id([t["id"] for t in tweets])
        if new_max:
            cur.last_seen_id = new_max
            session.add(cur)
            session.commit()
    return created


def poll_following(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str
) -> int:
    """フォロー中アカウントの新規投稿→引用RT案。コスト高のため上限あり。生成件数を返す。"""
    following = x_client.get_following(me_user_id, max_total=FOLLOWING_MAX_ACCOUNTS)
    created = 0
    for user in following:
        uid = user.get("id")
        if not uid:
            continue
        cur = _get_cursor(session, f"follow:{uid}")
        tweets = x_client.get_user_timeline(uid, since_id=cur.last_seen_id)
        for t in tweets:
            create_quote_draft(
                session, formatter, t["id"], t.get("text", ""),
                target_handle=user.get("username"),
            )
            created += 1
        new_max = _max_id([t["id"] for t in tweets])
        if new_max:
            cur.last_seen_id = new_max
            session.add(cur)
            session.commit()
    return created


def run_once(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str
) -> dict:
    """1回分の監視サイクル。各ソースはトグル(MonitorSettings)で個別にオン/オフ。"""
    cfg = get_monitor_settings(session)
    replies = poll_mentions(session, x_client, formatter, me_user_id) if cfg.mentions_enabled else 0
    quotes = 0
    if cfg.manual_targets_enabled:
        quotes += poll_targets(session, x_client, formatter)
    if cfg.keyword_search_enabled:
        quotes += poll_genre(session, x_client, formatter)
    if cfg.following_enabled:
        quotes += poll_following(session, x_client, formatter, me_user_id)
    return {"reply_suggestions": replies, "quote_suggestions": quotes}
