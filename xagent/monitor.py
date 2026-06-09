"""受信監視。メンション/自分宛リプライと、絡み対象(有名人等)の新規投稿をポーリングし、
返信案(絡み先へのリプライを含む)を「下書き(未承認)」として生成する。承認は人間が行う。

ストリーム不可のため定期ポーリング。MonitorCursor で since_id を管理し重複を防ぐ。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from .formatter import Formatter
from .models import EngageTarget, MonitorCursor, MonitorSettings, TargetKind
from .service import create_quote_draft, create_reply_draft, to_naive_utc
from .x_client import XClient

FOLLOWING_MAX_ACCOUNTS = 20  # フォロー中巡回の1ティック上限(コスト抑制)
LIST_MEMBERS_MAX = 500       # リスト連携で展開するメンバー数の上限
TARGET_MAX_AGE_DAYS = 3      # これより古い投稿にはリプ案を作らない(古いリプは無意味・乱造防止)


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


def _within_age(
    tweets: list[dict], max_age_days: int = TARGET_MAX_AGE_DAYS, now: datetime | None = None
) -> list[dict]:
    """投稿日時が max_age_days 以内のツイートだけに絞る(古い投稿への無意味なリプ案を防ぐ)。

    created_at(tweepy の aware datetime)を naive UTC に正規化して比較する。created_at が
    取れないものは判定不能として通す(実データは created_at を必ず持つ。テスト互換のため)。
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=max_age_days)
    out = []
    for t in tweets:
        dt = to_naive_utc(t.get("created_at"))
        if dt is None or dt >= cutoff:
            out.append(t)
    return out


def _drop_retweets(tweets: list[dict]) -> list[dict]:
    """単純リポスト(コメント無しRT)を除外する。本人のポストと引用RT(本人のコメント有り)だけ残す。

    単純RTは本文が 'RT @<user>: ...' で始まる(公式API・twitterapi.io 共通の慣習)。引用RTは
    本人のコメントが本文に入るため 'RT @' では始まらず残る。リポストには反応しない方針。
    """
    return [t for t in tweets if not str(t.get("text", "")).startswith("RT @")]


def _emit_engage_drafts(
    session: Session,
    formatter: Formatter,
    t: dict,
    handle: str | None,
    budget_left: int | None,
) -> tuple[int, int]:
    """絡み対象の1投稿に「返信案」と「引用案(引用RT)」を両方下書きする。(返信数, 引用数)を返す。

    返信はXの制限でアプリから送信できず手動(Inboxでコピー&元ポストを開く)、引用RTは送信可。
    人間がどちらで絡むかを選べるよう両方出す。バジェット残りが1件しか無いときは返信のみ作る。
    """
    text = t.get("text", "")
    ca = t.get("created_at")
    create_reply_draft(
        session, formatter, t["id"], text, target_handle=handle, target_created_at=ca
    )
    if budget_left is not None and budget_left <= 1:
        return 1, 0
    create_quote_draft(
        session, formatter, t["id"], text, target_handle=handle, target_created_at=ca
    )
    return 1, 1


def poll_mentions(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str,
    limit: int | None = None,
) -> tuple[int, int]:
    """自分宛メンションを取得し、返信案の下書きを作る(自分宛は引用RTしないので返信のみ)。

    (返信数, 引用数=0) を返す。limit で生成件数を上限管理。
    """
    cur = _get_cursor(session, "mentions")
    tweets = _cap_oldest(
        _within_age(_drop_retweets(x_client.get_mentions(me_user_id, since_id=cur.last_seen_id))),
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


def poll_targets(
    session: Session, x_client: XClient, formatter: Formatter, limit: int | None = None
) -> tuple[int, int]:
    """手動追加(MANUAL・user_id付き)の新規投稿→返信案＋引用案。limit は全対象で共有。

    (返信数, 引用数) を返す。1投稿につき返信と引用RTの両方を下書きする(_emit_engage_drafts)。
    """
    targets = session.exec(
        select(EngageTarget).where(
            EngageTarget.active == True,  # noqa: E712
            EngageTarget.user_id != None,  # noqa: E711
            EngageTarget.kind == TargetKind.MANUAL,
        )
    ).all()
    created = 0
    replies = 0
    quotes = 0
    for target in targets:
        if limit is not None and created >= limit:
            break
        remaining = None if limit is None else limit - created
        cur = _get_cursor(session, f"target:{target.user_id}")
        tweets = _cap_oldest(
            _within_age(
                _drop_retweets(
                    x_client.get_user_timeline(target.user_id, since_id=cur.last_seen_id)
                )
            ),
            remaining,
        )
        processed = []
        for t in tweets:
            if limit is not None and created >= limit:
                break
            bl = None if limit is None else limit - created
            r, q = _emit_engage_drafts(session, formatter, t, target.handle, bl)
            created += r + q
            replies += r
            quotes += q
            processed.append(t["id"])
        new_max = _max_id(processed)
        if new_max:
            cur.last_seen_id = new_max
            session.add(cur)
            session.commit()
    return replies, quotes


def poll_lists(
    session: Session, x_client: XClient, formatter: Formatter, limit: int | None = None
) -> tuple[int, int]:
    """Xリスト連携(kind=LIST)→リストの現メンバーを毎回展開し各人の新規投稿→返信案＋引用案。

    メンバーは巡回ごとに get_list_members で取り直すため、Xリスト側の増減が自動で反映される
    (=リスト更新が絡む相手に連携)。limit は全対象で共有。(返信数, 引用数) を返す。
    """
    lists = session.exec(
        select(EngageTarget).where(
            EngageTarget.active == True,  # noqa: E712
            EngageTarget.kind == TargetKind.LIST,
            EngageTarget.list_id != None,  # noqa: E711
        )
    ).all()
    created = 0
    replies = 0
    quotes = 0
    for lt in lists:
        if limit is not None and created >= limit:
            break
        members = x_client.get_list_members(lt.list_id, max_total=LIST_MEMBERS_MAX)
        for m in members:
            if limit is not None and created >= limit:
                break
            uid = m.get("id")
            if not uid:
                continue
            remaining = None if limit is None else limit - created
            cur = _get_cursor(session, f"target:{uid}")
            tweets = _cap_oldest(
                _within_age(
                    _drop_retweets(x_client.get_user_timeline(uid, since_id=cur.last_seen_id))
                ),
                remaining,
            )
            processed = []
            for t in tweets:
                if limit is not None and created >= limit:
                    break
                bl = None if limit is None else limit - created
                r, q = _emit_engage_drafts(session, formatter, t, m.get("username"), bl)
                created += r + q
                replies += r
                quotes += q
                processed.append(t["id"])
            new_max = _max_id(processed)
            if new_max:
                cur.last_seen_id = new_max
                session.add(cur)
                session.commit()
    return replies, quotes


def poll_genre(
    session: Session, x_client: XClient, formatter: Formatter, limit: int | None = None
) -> tuple[int, int]:
    """ジャンル/キーワード探索(GENRE対象)→該当投稿に返信案＋引用案。limit は全対象で共有。

    (返信数, 引用数) を返す。1投稿につき返信と引用RTの両方を下書きする(_emit_engage_drafts)。
    """
    targets = session.exec(
        select(EngageTarget).where(
            EngageTarget.active == True,  # noqa: E712
            EngageTarget.kind == TargetKind.GENRE,
            EngageTarget.keyword != None,  # noqa: E711
        )
    ).all()
    created = 0
    replies = 0
    quotes = 0
    for target in targets:
        if limit is not None and created >= limit:
            break
        remaining = None if limit is None else limit - created
        cur = _get_cursor(session, f"genre:{target.keyword}")
        tweets = _cap_oldest(
            _within_age(
                _drop_retweets(x_client.search_recent(target.keyword, since_id=cur.last_seen_id))
            ),
            remaining,
        )
        processed = []
        for t in tweets:
            if limit is not None and created >= limit:
                break
            bl = None if limit is None else limit - created
            r, q = _emit_engage_drafts(session, formatter, t, t.get("author_id"), bl)
            created += r + q
            replies += r
            quotes += q
            processed.append(t["id"])
        new_max = _max_id(processed)
        if new_max:
            cur.last_seen_id = new_max
            session.add(cur)
            session.commit()
    return replies, quotes


def poll_following(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str,
    limit: int | None = None,
) -> tuple[int, int]:
    """フォロー中アカウントの新規投稿→返信案＋引用案。limit は全対象で共有。

    (返信数, 引用数) を返す。1投稿につき返信と引用RTの両方を下書きする(_emit_engage_drafts)。
    """
    following = x_client.get_following(me_user_id, max_total=FOLLOWING_MAX_ACCOUNTS)
    created = 0
    replies = 0
    quotes = 0
    for user in following:
        if limit is not None and created >= limit:
            break
        uid = user.get("id")
        if not uid:
            continue
        remaining = None if limit is None else limit - created
        cur = _get_cursor(session, f"follow:{uid}")
        tweets = _cap_oldest(
            _within_age(
                _drop_retweets(x_client.get_user_timeline(uid, since_id=cur.last_seen_id))
            ),
            remaining,
        )
        processed = []
        for t in tweets:
            if limit is not None and created >= limit:
                break
            bl = None if limit is None else limit - created
            r, q = _emit_engage_drafts(session, formatter, t, user.get("username"), bl)
            created += r + q
            replies += r
            quotes += q
            processed.append(t["id"])
        new_max = _max_id(processed)
        if new_max:
            cur.last_seen_id = new_max
            session.add(cur)
            session.commit()
    return replies, quotes


def run_once(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str,
    max_drafts: int | None = None,
) -> dict:
    """1回分の監視サイクル。各ソースはトグル(MonitorSettings)で個別にオン/オフ。

    総生成数バジェットを全ソースで共有し、一気に作りすぎてAPIを圧迫しないようにする
    (使い切ったら以降のソースはスキップ)。手動の1回実行で件数を絞りたいときは max_drafts に
    その回限りの上限を渡す(指定なしは設定値 max_drafts_per_run を使う)。
    """
    cfg = get_monitor_settings(session)
    cap = cfg.max_drafts_per_run if max_drafts is None else max_drafts
    budget = max(0, int(cap or 0))
    replies = 0
    quotes = 0

    def _acc(pair):
        nonlocal replies, quotes, budget
        r, q = pair
        replies += r
        quotes += q
        budget -= (r + q)

    if cfg.mentions_enabled and budget > 0:
        _acc(poll_mentions(session, x_client, formatter, me_user_id, limit=budget))
    if cfg.manual_targets_enabled and budget > 0:
        _acc(poll_targets(session, x_client, formatter, limit=budget))
    if cfg.manual_targets_enabled and budget > 0:
        _acc(poll_lists(session, x_client, formatter, limit=budget))
    if cfg.keyword_search_enabled and budget > 0:
        _acc(poll_genre(session, x_client, formatter, limit=budget))
    if cfg.following_enabled and budget > 0:
        _acc(poll_following(session, x_client, formatter, me_user_id, limit=budget))
    # 絡み対象(targets/lists/genre/following)は返信案＋引用案の両方を生成。メンションは
    # 自分宛のため返信のみ。総生成数はバジェット(max_drafts_per_run)で全ソース共有上限。
    return {"reply_suggestions": replies, "quote_suggestions": quotes}
