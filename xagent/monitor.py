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


def set_monitor_settings(session: Session, **flags) -> MonitorSettings:
    """監視設定を更新する(指定キーのみ)。トグルは bool、生成数上限は非負 int。"""
    row = get_monitor_settings(session)
    for k, v in flags.items():
        if not hasattr(row, k):
            continue
        if k == "max_drafts_per_run" and isinstance(v, int) and not isinstance(v, bool):
            setattr(row, k, max(0, v))
        elif isinstance(v, bool):
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


def _cap_oldest(tweets: list[dict], limit: int | None) -> list[dict]:
    """生成数上限のため、処理するツイートを limit 件までに絞る。

    超過時は古い順(id昇順)に limit 件だけ残す。残りはカーソルを進めないことで次サイクルに回り、
    新着の取りこぼしを防ぐ(古い分から順に消化する)。limit=None なら全件(従来挙動)。
    """
    if limit is None or len(tweets) <= limit:
        return tweets
    if limit <= 0:
        return []
    return sorted(
        tweets, key=lambda t: int(t["id"]) if str(t.get("id", "")).isdigit() else 0
    )[:limit]


def poll_mentions(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str,
    limit: int | None = None,
) -> int:
    """自分宛メンションを取得し、返信案の下書きを作る。limit で生成件数を上限管理。生成件数を返す。"""
    cur = _get_cursor(session, "mentions")
    tweets = _cap_oldest(x_client.get_mentions(me_user_id, since_id=cur.last_seen_id), limit)
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
    session: Session, x_client: XClient, formatter: Formatter, limit: int | None = None
) -> int:
    """手動リスト等(user_id付き・GENRE以外)の新規投稿→引用RT案。limit は全対象で共有。生成件数を返す。"""
    targets = session.exec(
        select(EngageTarget).where(
            EngageTarget.active == True,  # noqa: E712
            EngageTarget.user_id != None,  # noqa: E711
            EngageTarget.kind != TargetKind.GENRE,
        )
    ).all()
    created = 0
    for target in targets:
        if limit is not None and created >= limit:
            break
        remaining = None if limit is None else limit - created
        cur = _get_cursor(session, f"target:{target.user_id}")
        tweets = _cap_oldest(
            x_client.get_user_timeline(target.user_id, since_id=cur.last_seen_id), remaining
        )
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


def poll_genre(
    session: Session, x_client: XClient, formatter: Formatter, limit: int | None = None
) -> int:
    """ジャンル/キーワード探索(GENRE対象)→該当投稿に引用RT案。limit は全対象で共有。生成件数を返す。"""
    targets = session.exec(
        select(EngageTarget).where(
            EngageTarget.active == True,  # noqa: E712
            EngageTarget.kind == TargetKind.GENRE,
            EngageTarget.keyword != None,  # noqa: E711
        )
    ).all()
    created = 0
    for target in targets:
        if limit is not None and created >= limit:
            break
        remaining = None if limit is None else limit - created
        cur = _get_cursor(session, f"genre:{target.keyword}")
        tweets = _cap_oldest(
            x_client.search_recent(target.keyword, since_id=cur.last_seen_id), remaining
        )
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
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str,
    limit: int | None = None,
) -> int:
    """フォロー中アカウントの新規投稿→引用RT案。limit は全対象で共有。生成件数を返す。"""
    following = x_client.get_following(me_user_id, max_total=FOLLOWING_MAX_ACCOUNTS)
    created = 0
    for user in following:
        if limit is not None and created >= limit:
            break
        uid = user.get("id")
        if not uid:
            continue
        remaining = None if limit is None else limit - created
        cur = _get_cursor(session, f"follow:{uid}")
        tweets = _cap_oldest(
            x_client.get_user_timeline(uid, since_id=cur.last_seen_id), remaining
        )
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
    """1回分の監視サイクル。各ソースはトグル(MonitorSettings)で個別にオン/オフ。

    max_drafts_per_run を総生成数バジェットとして全ソースで共有し、一気に作りすぎてAPIを
    圧迫しないようにする。バジェットを使い切ったら以降のソースはスキップする。
    """
    cfg = get_monitor_settings(session)
    budget = max(0, int(cfg.max_drafts_per_run or 0))
    replies = 0
    quotes = 0
    if cfg.mentions_enabled and budget > 0:
        replies = poll_mentions(session, x_client, formatter, me_user_id, limit=budget)
        budget -= replies
    if cfg.manual_targets_enabled and budget > 0:
        c = poll_targets(session, x_client, formatter, limit=budget)
        quotes += c
        budget -= c
    if cfg.keyword_search_enabled and budget > 0:
        c = poll_genre(session, x_client, formatter, limit=budget)
        quotes += c
        budget -= c
    if cfg.following_enabled and budget > 0:
        c = poll_following(session, x_client, formatter, me_user_id, limit=budget)
        quotes += c
        budget -= c
    return {"reply_suggestions": replies, "quote_suggestions": quotes}
