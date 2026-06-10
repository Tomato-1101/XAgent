"""受信監視。メンション/自分宛リプライと、絡み対象(有名人等)の直近24時間の投稿を収集し、
AIのバッチ判断で「絡む価値が高い投稿」を分散選定して下書き(未承認)を生成する。承認は人間が行う。

メンションは MonitorCursor(since_id) で重複を防ぐ。絡み案は since_id を使わず24時間窓で
毎回取り直し、「同じ tweet を対象にした Draft が既にあれば(状態問わず)再生成しない」ことで
重複と乱造(却下済みへの再生成)を防ぐ。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from . import templates as templates_mod
from .cost import bill_formatter_usage
from .formatter import Formatter
from .models import (
    Draft,
    DraftKind,
    EngageTarget,
    MonitorCursor,
    MonitorSettings,
    TargetKind,
    TemplateKind,
)
from .service import create_engage_draft_from_text, create_reply_draft, to_naive_utc
from .style import active_style_guide
from .x_client import XClient

FOLLOWING_MAX_ACCOUNTS = 20  # フォロー中巡回の1ティック上限(コスト抑制)
LIST_MEMBERS_MAX = 500       # リスト連携で展開するメンバー数の上限
TARGET_MAX_AGE_HOURS = 24    # 「今から24時間前まで」の投稿だけ絡み候補にする(古いリプは無意味)
SELECT_MAX_CANDIDATES = 120  # AIバッチ選定に渡す候補数の安全弁(新しい順に残す)


def get_monitor_settings(session: Session) -> MonitorSettings:
    """監視トグル設定(単一行)。無ければ既定で作成する。"""
    row = session.exec(select(MonitorSettings)).first()
    if row is None:
        row = MonitorSettings()  # 既定: manual=ON, mentions/keyword/following=OFF
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


def _within_age(
    tweets: list[dict], max_age_hours: int = TARGET_MAX_AGE_HOURS, now: datetime | None = None
) -> list[dict]:
    """投稿日時が max_age_hours 以内のツイートだけに絞る(古い投稿への無意味なリプ案を防ぐ)。

    created_at(tweepy の aware datetime)を naive UTC に正規化して比較する。created_at が
    取れないものは判定不能として通す(実データは created_at を必ず持つ。テスト互換のため)。
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=max_age_hours)
    out = []
    for t in tweets:
        dt = to_naive_utc(t.get("created_at"))
        if dt is None or dt >= cutoff:
            out.append(t)
    return out


def _drop_reposts(tweets: list[dict]) -> list[dict]:
    """リポスト(単純RT・引用RT)を除外し、対象アカウントの本人オリジナル投稿だけ残す。

    判定は2系統: (1)正規化辞書の is_repost(retweeted_tweet/quoted_tweet または公式APIの
    referenced_tweets[type=retweeted/quoted]由来)、(2)後方互換の本文 'RT @<user>:' 始まり。
    引用RTも対象外(本人のオリジナルではないため絡み案を作らない方針)。
    """
    return [
        t for t in tweets
        if not t.get("is_repost") and not str(t.get("text", "")).startswith("RT @")
    ]


def poll_mentions(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str,
    limit: int | None = None,
) -> tuple[int, int]:
    """自分宛メンションを取得し、返信案の下書きを作る(自分宛は引用RTしないので返信のみ)。

    (返信数, 引用数=0) を返す。limit で生成件数を上限管理。
    """
    cur = _get_cursor(session, "mentions")
    tweets = _cap_oldest(
        _within_age(_drop_reposts(x_client.get_mentions(me_user_id, since_id=cur.last_seen_id))),
        limit,
    )
    created = 0
    for t in tweets:
        create_reply_draft(
            session, formatter, t["id"], t.get("text", ""),
            target_handle=t.get("author_id"), target_created_at=t.get("created_at"),
        )
        created += 1
    new_max = _max_id([t["id"] for t in tweets])
    if new_max:
        cur.last_seen_id = new_max
        session.add(cur)
        session.commit()
    return created, 0


def _candidate(t: dict, handle: str | None) -> dict:
    """正規化ツイート辞書を、AIバッチ選定に渡す候補の形に変換する。"""
    return {
        "tweet_id": str(t.get("id", "")),
        "handle": handle or t.get("author_handle") or t.get("author_id"),
        "text": t.get("text", ""),
        "created_at": t.get("created_at"),
        "like_count": t.get("like_count", 0),
        "retweet_count": t.get("retweet_count", 0),
    }


def collect_engage_candidates(
    session: Session, x_client: XClient, me_user_id: str,
    cfg: MonitorSettings | None = None,
) -> list[dict]:
    """全有効ソース(手動対象/リスト/ジャンル/フォロー中)から直近24時間の本人オリジナル投稿を
    集め、AIバッチ選定の候補一覧を返す。

    since_id カーソルは使わない(24時間窓で毎回取り直す)。重複防止は
    (1)既に同じ tweet を対象にした Draft があれば状態問わず除外(却下済みへの再生成もしない)、
    (2)ソース横断の tweet_id 重複排除。候補は新しい順に SELECT_MAX_CANDIDATES 件まで。
    """
    cfg = cfg or get_monitor_settings(session)
    raw: list[tuple[dict, str | None]] = []
    if cfg.manual_targets_enabled:
        targets = session.exec(
            select(EngageTarget).where(
                EngageTarget.active == True,  # noqa: E712
                EngageTarget.user_id != None,  # noqa: E711
                EngageTarget.kind == TargetKind.MANUAL,
            )
        ).all()
        for target in targets:
            for t in _drop_reposts(x_client.get_user_timeline(target.user_id)):
                raw.append((t, target.handle))
        lists = session.exec(
            select(EngageTarget).where(
                EngageTarget.active == True,  # noqa: E712
                EngageTarget.kind == TargetKind.LIST,
                EngageTarget.list_id != None,  # noqa: E711
            )
        ).all()
        for lt in lists:
            for m in x_client.get_list_members(lt.list_id, max_total=LIST_MEMBERS_MAX):
                uid = m.get("id")
                if not uid:
                    continue
                for t in _drop_reposts(x_client.get_user_timeline(uid)):
                    raw.append((t, m.get("username")))
    if cfg.keyword_search_enabled:
        genres = session.exec(
            select(EngageTarget).where(
                EngageTarget.active == True,  # noqa: E712
                EngageTarget.kind == TargetKind.GENRE,
                EngageTarget.keyword != None,  # noqa: E711
            )
        ).all()
        for target in genres:
            for t in _drop_reposts(x_client.search_recent(target.keyword)):
                raw.append((t, None))
    if cfg.following_enabled:
        for user in x_client.get_following(me_user_id, max_total=FOLLOWING_MAX_ACCOUNTS):
            uid = user.get("id")
            if not uid:
                continue
            for t in _drop_reposts(x_client.get_user_timeline(uid)):
                raw.append((t, user.get("username")))

    existing = {
        tid
        for tid in session.exec(
            select(Draft.target_tweet_id).where(Draft.target_tweet_id != None)  # noqa: E711
        ).all()
        if tid
    }
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=TARGET_MAX_AGE_HOURS)
    seen: set[str] = set()
    out: list[dict] = []
    for t, handle in raw:
        tid = str(t.get("id", ""))
        if not tid or tid in seen or tid in existing:
            continue
        dt = to_naive_utc(t.get("created_at"))
        if dt is not None and dt < cutoff:
            continue
        seen.add(tid)
        out.append(_candidate(t, handle))
    out.sort(
        key=lambda c: int(c["tweet_id"]) if str(c["tweet_id"]).isdigit() else 0,
        reverse=True,
    )
    return out[:SELECT_MAX_CANDIDATES]


def run_once(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str,
    max_drafts: int | None = None,
) -> dict:
    """1回分の監視サイクル。各ソースはトグル(MonitorSettings)で個別にオン/オフ。

    メンション返信は従来通り(since_idカーソル・自分宛は返信のみ)。絡み案は全有効ソースの
    直近24時間の投稿をまとめて収集し、1回のAIバッチ判断で「どれに・reply/quoteどちらで・
    どんな本文で」絡むかを分散選定する(従来の直列スキャンによる同一アカウント偏りの解消)。
    総生成数はバジェットで上限管理(手動の1回実行は max_drafts でその回限りの上限を渡せる。
    指定なしは設定値 max_drafts_per_run)。
    """
    cfg = get_monitor_settings(session)
    cap = cfg.max_drafts_per_run if max_drafts is None else max_drafts
    budget = max(0, int(cap or 0))
    replies = 0
    quotes = 0

    if cfg.mentions_enabled and budget > 0:
        r, _ = poll_mentions(session, x_client, formatter, me_user_id, limit=budget)
        replies += r
        budget -= r

    if budget > 0:
        candidates = collect_engage_candidates(session, x_client, me_user_id, cfg)
        if candidates:
            selections = formatter.select_engagements(
                candidates,
                budget,
                style_guide=active_style_guide(session),
                reply_playbook=templates_mod.active_body(session, TemplateKind.REPLY),
                quote_playbook=templates_mod.active_body(session, TemplateKind.QUOTE),
            )
            for s in selections:
                cand = s["candidate"]
                kind = DraftKind.QUOTE if s["kind"] == "quote" else DraftKind.REPLY
                create_engage_draft_from_text(
                    session,
                    kind,
                    s["tweet_id"],
                    s["text"],
                    target_text=cand.get("text", ""),
                    target_handle=cand.get("handle"),
                    target_created_at=cand.get("created_at"),
                    reason=s.get("reason", ""),
                )
                if kind == DraftKind.QUOTE:
                    quotes += 1
                else:
                    replies += 1
            # 選定0件でもバッチ判断のトークンは消費しているので記録する
            note = "engage-select" if selections else "engage-select-empty"
            bill_formatter_usage(session, formatter, note=note)
            session.commit()
    return {"reply_suggestions": replies, "quote_suggestions": quotes}
