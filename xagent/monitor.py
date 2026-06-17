"""受信監視。メンション/自分宛リプライと、絡み対象(有名人等)の直近24時間の投稿を収集し、
AIのバッチ判断で「絡む価値が高い投稿」を分散選定して下書き(未承認)を生成する。承認は人間が行う。

メンションは MonitorCursor(since_id) で重複を防ぐ。絡み案は since_id を使わず24時間窓で
毎回取り直し、「同じ tweet を対象にした Draft が既にあれば(状態問わず)再生成しない」ことで
重複と乱造(却下済みへの再生成)を防ぐ。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from . import templates as templates_mod
from .cost import bill_formatter_usage
from .formatter import Formatter
from .models import (
    Draft,
    DraftKind,
    DraftStatus,
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

CELEB_MAX_PER_TICK = 3       # 有名人ウォッチ1回で作る絡み案の上限(乱造防止)
CELEB_SEARCH_CHUNK = 12      # 1検索クエリに入れる from: の数(クエリ長の安全弁)

BUZZ_MAX_PER_TICK = 3        # バズウォッチ1回で作る絡み案の上限(乱造防止)
BUZZ_WINDOW_HOURS = 6        # バズ検索の時間窓。Latest順×100件上限のため実効は直近数時間
BUZZ_BACKLOG_MAX = 30        # 承認待ち絡み案がこれ以上溜まっていたら自動tickは生成しない
# 有名人の「AIについての投稿」を検索で拾うキーワード(X検索のOR条件)。
# 検索クエリ側で絞るのでタイムライン全件を読まず、ヒットが無ければLLMも呼ばない(API節約)。
AI_TOPIC_KEYWORDS = (
    "AI", "人工知能", "生成AI", "ChatGPT", "GPT", "Claude",
    "Gemini", "LLM", "OpenAI", "AGI",
)


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
        if (
            k in ("max_drafts_per_run", "min_impressions", "buzz_min_faves")
            and isinstance(v, int)
            and not isinstance(v, bool)
        ):
            setattr(row, k, max(0, v))
        elif k == "celeb_list_id" and isinstance(v, str):
            setattr(row, k, v.strip() or None)
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
            target_view_count=t.get("view_count"), target_like_count=t.get("like_count"),
            target_retweet_count=t.get("retweet_count"),
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
        "view_count": t.get("view_count", 0),
    }


def collect_engage_candidates(
    session: Session, x_client: XClient, me_user_id: str,
    cfg: MonitorSettings | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """全有効ソース(手動対象/リスト/ジャンル/フォロー中)から直近24時間の本人オリジナル投稿を
    集め、AIバッチ選定の候補一覧を返す。

    since_id カーソルは使わない(24時間窓で毎回取り直す)。重複防止は
    (1)既に同じ tweet を対象にした Draft があれば状態問わず除外(却下済みへの再生成もしない)、
    (2)ソース横断の tweet_id 重複排除。候補は新しい順に SELECT_MAX_CANDIDATES 件まで。
    収集は1アカウント1リクエストで数十分かかりうるため、progress で進捗を逐次通知する。
    """
    cfg = cfg or get_monitor_settings(session)
    note = progress or (lambda m: None)
    raw: list[tuple[dict, str | None]] = []
    if cfg.manual_targets_enabled:
        targets = session.exec(
            select(EngageTarget).where(
                EngageTarget.active == True,  # noqa: E712
                EngageTarget.user_id != None,  # noqa: E711
                EngageTarget.kind == TargetKind.MANUAL,
            )
        ).all()
        for i, target in enumerate(targets):
            note(f"候補収集中: 手動対象 {i + 1}/{len(targets)}人 (@{target.handle})")
            for t in _drop_reposts(
                x_client.get_user_timeline(target.user_id, official_fallback=False)
            ):
                raw.append((t, target.handle))
        lists = session.exec(
            select(EngageTarget).where(
                EngageTarget.active == True,  # noqa: E712
                EngageTarget.kind == TargetKind.LIST,
                EngageTarget.list_id != None,  # noqa: E711
            )
        ).all()
        for lt in lists:
            note(f"リスト「{lt.handle}」のメンバー一覧を取得中")
            members = [
                m for m in x_client.get_list_members(lt.list_id, max_total=LIST_MEMBERS_MAX)
                if m.get("id")
            ]
            for i, m in enumerate(members):
                note(
                    f"候補収集中: リスト「{lt.handle}」 {i + 1}/{len(members)}人"
                    f" (@{m.get('username')})"
                )
                for t in _drop_reposts(
                    x_client.get_user_timeline(m["id"], official_fallback=False)
                ):
                    raw.append((t, m.get("username")))
    if cfg.keyword_search_enabled:
        genres = session.exec(
            select(EngageTarget).where(
                EngageTarget.active == True,  # noqa: E712
                EngageTarget.kind == TargetKind.GENRE,
                EngageTarget.keyword != None,  # noqa: E711
            )
        ).all()
        for i, target in enumerate(genres):
            note(f"候補収集中: キーワード検索 {i + 1}/{len(genres)}件 ({target.keyword})")
            for t in _drop_reposts(x_client.search_recent(target.keyword)):
                raw.append((t, None))
    if cfg.following_enabled:
        note("候補収集中: フォロー中アカウントの一覧を取得中")
        users = [
            u for u in x_client.get_following(me_user_id, max_total=FOLLOWING_MAX_ACCOUNTS)
            if u.get("id")
        ]
        for i, user in enumerate(users):
            note(f"候補収集中: フォロー中 {i + 1}/{len(users)}人 (@{user.get('username')})")
            for t in _drop_reposts(
                x_client.get_user_timeline(user["id"], official_fallback=False)
            ):
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
        # 最低インプレッション未満には絡まない(伸びていない投稿へのリプは露出が取れない)。
        # view数が取れない投稿(公式APIフォールバック経路・テスト)は判定不能として通す。
        vc = t.get("view_count")
        if vc is not None and int(vc) < cfg.min_impressions:
            continue
        seen.add(tid)
        out.append(_candidate(t, handle))
    out.sort(
        key=lambda c: int(c["tweet_id"]) if str(c["tweet_id"]).isdigit() else 0,
        reverse=True,
    )
    return out[:SELECT_MAX_CANDIDATES]


def _research_engage_body(
    session: Session,
    formatter: Formatter,
    s: dict,
    note: Callable[[str], None],
) -> str:
    """選定された絡み案の本文を、ウェブ検索つきで作り直す(元投稿の文脈を調べてから書く)。

    select_engagements が作った本文は叩き台。ここで WebSearch/WebFetch 付きの生成に置き換える。
    失敗・タイムアウト時は叩き台(s["text"])にフォールバックする(候補収集に費やしたAPIを
    無駄にしない・乱造ガードを崩さない)。
    """
    cand = s["candidate"]
    kind = DraftKind.QUOTE if s["kind"] == "quote" else DraftKind.REPLY
    target_text = cand.get("text", "")
    target_handle = cand.get("handle") or ""
    target_tweet_id = str(s.get("tweet_id", ""))
    try:
        if kind == DraftKind.QUOTE:
            res = formatter.generate_quote(
                target_text, target_handle, active_style_guide(session),
                playbook=templates_mod.active_body(session, TemplateKind.QUOTE),
                target_tweet_id=target_tweet_id,
            )
        else:
            res = formatter.generate_reply(
                target_text, target_handle, active_style_guide(session),
                playbook=templates_mod.active_body(session, TemplateKind.REPLY),
                target_tweet_id=target_tweet_id,
            )
        body = (res.segments[0] if res.segments else "").strip()
        return body or s["text"]
    except Exception as e:  # noqa: BLE001 - リサーチ生成の失敗は叩き台で代替する
        note(f"リサーチ生成に失敗、叩き台を使用: {str(e)[:80]}")
        return s["text"]


def run_once(
    session: Session, x_client: XClient, formatter: Formatter, me_user_id: str,
    max_drafts: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """1回分の監視サイクル。各ソースはトグル(MonitorSettings)で個別にオン/オフ。

    メンション返信は従来通り(since_idカーソル・自分宛は返信のみ)。絡み案は全有効ソースの
    直近24時間の投稿をまとめて収集し、1回のAIバッチ判断で「どれに・reply/quoteどちらで・
    どんな本文で」絡むかを分散選定する(従来の直列スキャンによる同一アカウント偏りの解消)。
    総生成数はバジェットで上限管理(手動の1回実行は max_drafts でその回限りの上限を渡せる。
    指定なしは設定値 max_drafts_per_run)。戻り値の candidates は選定に渡した候補数
    (0なら「新しい候補なし」をフロントが区別して表示できる)。
    """
    note = progress or (lambda m: None)
    cfg = get_monitor_settings(session)
    cap = cfg.max_drafts_per_run if max_drafts is None else max_drafts
    budget = max(0, int(cap or 0))
    replies = 0
    quotes = 0
    n_candidates = 0

    if cfg.mentions_enabled and budget > 0:
        note("自分宛メンションを確認中")
        r, _ = poll_mentions(session, x_client, formatter, me_user_id, limit=budget)
        replies += r
        budget -= r

    if budget > 0:
        candidates = collect_engage_candidates(
            session, x_client, me_user_id, cfg, progress=note
        )
        n_candidates = len(candidates)
        if candidates:
            note(
                f"AIバッチ選定中: 候補{len(candidates)}件から最大{budget}件を選定"
                "(数分〜15分かかることがあります)"
            )
            selections = formatter.select_engagements(
                candidates,
                budget,
                style_guide=active_style_guide(session),
                reply_playbook=templates_mod.active_body(session, TemplateKind.REPLY),
                quote_playbook=templates_mod.active_body(session, TemplateKind.QUOTE),
            )
            for i, s in enumerate(selections):
                note(f"リサーチして本文作成中 {i + 1}/{len(selections)}件")
                cand = s["candidate"]
                kind = DraftKind.QUOTE if s["kind"] == "quote" else DraftKind.REPLY
                body = _research_engage_body(session, formatter, s, note)
                create_engage_draft_from_text(
                    session,
                    kind,
                    s["tweet_id"],
                    body,
                    target_text=cand.get("text", ""),
                    target_handle=cand.get("handle"),
                    target_created_at=cand.get("created_at"),
                    reason=s.get("reason", ""),
                    target_view_count=cand.get("view_count"),
                    target_like_count=cand.get("like_count"),
                    target_retweet_count=cand.get("retweet_count"),
                )
                if kind == DraftKind.QUOTE:
                    quotes += 1
                else:
                    replies += 1
            # 選定0件でもバッチ判断のトークンは消費しているので記録する
            bill_note = "engage-select" if selections else "engage-select-empty"
            bill_formatter_usage(session, formatter, note=bill_note)
            session.commit()
    return {
        "reply_suggestions": replies,
        "quote_suggestions": quotes,
        "candidates": n_candidates,
    }


def _celeb_queries(usernames: list[str]) -> list[str]:
    """有名人ウォッチの検索クエリを組み立てる(from: をチャンク分割してクエリ長を抑える)。"""
    kw = " OR ".join(AI_TOPIC_KEYWORDS)
    out = []
    for i in range(0, len(usernames), CELEB_SEARCH_CHUNK):
        froms = " OR ".join(f"from:{u}" for u in usernames[i : i + CELEB_SEARCH_CHUNK])
        out.append(f"({froms}) ({kw}) within_time:24h")
    return out


def run_celeb_once(
    session: Session, x_client: XClient, formatter: Formatter,
    max_drafts: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """有名人ウォッチを1回実行: リスト(celeb_list_id)のメンバーがAIについて投稿していたら
    絡み案(主にreply)を即生成する。

    検出はアカウント横断の検索1〜2クエリで行い、メンバーのタイムラインを巡回しない(API節約)。
    キーワードに当たる投稿が無ければLLMを呼ばない。カーソル(stream="celeb")で同じ投稿を
    二度LLMにかけない。有名人は min_impressions の対象外(投稿直後の素早い反応を優先)。
    下書きを作るだけで投稿しない(人間承認必須)。
    """
    note = progress or (lambda m: None)
    cfg = get_monitor_settings(session)
    if not cfg.celeb_list_id:
        return {"candidates": 0, "reply_suggestions": 0, "quote_suggestions": 0,
                "error": "有名人リストが未設定です(celeb_list_id)"}
    note("有名人リストのメンバーを取得中")
    usernames = [
        m["username"]
        for m in x_client.get_list_members(cfg.celeb_list_id, max_total=LIST_MEMBERS_MAX)
        if m.get("username")
    ]
    if not usernames:
        return {"candidates": 0, "reply_suggestions": 0, "quote_suggestions": 0,
                "error": "有名人リストにメンバーがいません"}
    cur = _get_cursor(session, "celeb")
    raw: list[dict] = []
    for q in _celeb_queries(usernames):
        note(f"有名人{len(usernames)}人のAI言及投稿を検索中")
        raw.extend(x_client.search_recent(q, since_id=cur.last_seen_id))
    existing = {
        tid
        for tid in session.exec(
            select(Draft.target_tweet_id).where(Draft.target_tweet_id != None)  # noqa: E711
        ).all()
        if tid
    }
    seen: set[str] = set()
    candidates: list[dict] = []
    for t in _within_age(_drop_reposts(raw)):
        tid = str(t.get("id", ""))
        if not tid or tid in seen or tid in existing:
            continue
        seen.add(tid)
        c = _candidate(t, None)
        c["note"] = "有名人のAI関連投稿。即時の反応に価値がある(リプ欄で認知を取る)。"
        candidates.append(c)
    replies = 0
    quotes = 0
    budget = CELEB_MAX_PER_TICK if max_drafts is None else max(0, int(max_drafts))
    if candidates and budget > 0:
        note(f"AIが選定と本文生成中: 候補{len(candidates)}件から最大{budget}件")
        selections = formatter.select_engagements(
            candidates,
            budget,
            style_guide=active_style_guide(session),
            reply_playbook=templates_mod.active_body(session, TemplateKind.REPLY),
            quote_playbook=templates_mod.active_body(session, TemplateKind.QUOTE),
        )
        for i, s in enumerate(selections):
            note(f"リサーチして本文作成中 {i + 1}/{len(selections)}件")
            cand = s["candidate"]
            kind = DraftKind.QUOTE if s["kind"] == "quote" else DraftKind.REPLY
            body = _research_engage_body(session, formatter, s, note)
            create_engage_draft_from_text(
                session, kind, s["tweet_id"], body,
                target_text=cand.get("text", ""),
                target_handle=cand.get("handle"),
                target_created_at=cand.get("created_at"),
                reason=s.get("reason", ""),
                target_view_count=cand.get("view_count"),
                target_like_count=cand.get("like_count"),
                target_retweet_count=cand.get("retweet_count"),
            )
            if kind == DraftKind.QUOTE:
                quotes += 1
            else:
                replies += 1
        bill_formatter_usage(session, formatter, note="celeb-select")
    # 選定されなかった投稿も再度LLMにかけない(カーソルは取得した全hitの最大IDまで進める)
    new_max = _max_id([str(t.get("id", "")) for t in raw])
    if new_max and int(new_max) > int(cur.last_seen_id or 0):
        cur.last_seen_id = new_max
        session.add(cur)
    session.commit()
    return {"candidates": len(candidates), "reply_suggestions": replies,
            "quote_suggestions": quotes}


def engage_backlog_count(session: Session) -> int:
    """承認待ち(DRAFT)の絡み案(reply/quote)の件数。自動tickの乱造ガードに使う。"""
    return len(
        session.exec(
            select(Draft.id).where(
                Draft.status == DraftStatus.DRAFT,
                Draft.kind.in_((DraftKind.REPLY, DraftKind.QUOTE)),  # type: ignore[attr-defined]
            )
        ).all()
    )


def run_buzz_once(
    session: Session, x_client: XClient, formatter: Formatter,
    max_drafts: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """バズウォッチを1回実行: アカウントを問わず「既にバズった投稿」に絡み案を作る。

    バズの予測はしない。X検索の min_faves 演算子で「いいねが buzz_min_faves 以上付いた」
    という実績だけを条件に検索1クエリで網羅的に拾う(Latest順・最大100件)。since_id カーソルは
    使わない: バズ投稿は作成後しばらくして閾値を超えるため、作成ID基準のカーソルでは
    「後から閾値を超えた投稿」を全て取りこぼす。重複防止は既存Draft除外(状態問わず)のみ。
    どれに・何と絡むかの判断はバッチAI選定(select_engagements)に委ね、公式アカウントの告知や
    ファンダム向け定常投稿など「部外者のリプが浮く投稿」はそこで落とす。
    下書きを作るだけで投稿しない(人間承認必須)。
    """
    note = progress or (lambda m: None)
    cfg = get_monitor_settings(session)
    query = f"min_faves:{cfg.buzz_min_faves} lang:ja within_time:{BUZZ_WINDOW_HOURS}h -filter:replies"
    note(f"バズ投稿を検索中(いいね{cfg.buzz_min_faves}以上・{BUZZ_WINDOW_HOURS}時間以内)")
    raw = x_client.search_recent(query)
    existing = {
        tid
        for tid in session.exec(
            select(Draft.target_tweet_id).where(Draft.target_tweet_id != None)  # noqa: E711
        ).all()
        if tid
    }
    seen: set[str] = set()
    candidates: list[dict] = []
    for t in _within_age(_drop_reposts(raw), max_age_hours=BUZZ_WINDOW_HOURS):
        tid = str(t.get("id", ""))
        if not tid or tid in seen or tid in existing:
            continue
        seen.add(tid)
        c = _candidate(t, None)
        c["note"] = (
            "バズ投稿(高エンゲージ実績)。リプ欄上位に入って認知を取る狙い。"
            "公式アカウントの告知・ファンダム向け定常投稿・部外者のリプが浮く話題は選ばない。"
        )
        candidates.append(c)
    replies = 0
    quotes = 0
    budget = BUZZ_MAX_PER_TICK if max_drafts is None else max(0, int(max_drafts))
    if candidates and budget > 0:
        note(f"AIが選定と本文生成中: 候補{len(candidates)}件から最大{budget}件")
        selections = formatter.select_engagements(
            candidates,
            budget,
            style_guide=active_style_guide(session),
            reply_playbook=templates_mod.active_body(session, TemplateKind.REPLY),
            quote_playbook=templates_mod.active_body(session, TemplateKind.QUOTE),
        )
        for i, s in enumerate(selections):
            note(f"リサーチして本文作成中 {i + 1}/{len(selections)}件")
            cand = s["candidate"]
            kind = DraftKind.QUOTE if s["kind"] == "quote" else DraftKind.REPLY
            body = _research_engage_body(session, formatter, s, note)
            create_engage_draft_from_text(
                session, kind, s["tweet_id"], body,
                target_text=cand.get("text", ""),
                target_handle=cand.get("handle"),
                target_created_at=cand.get("created_at"),
                reason=s.get("reason", ""),
                target_view_count=cand.get("view_count"),
                target_like_count=cand.get("like_count"),
                target_retweet_count=cand.get("retweet_count"),
            )
            if kind == DraftKind.QUOTE:
                quotes += 1
            else:
                replies += 1
        bill_formatter_usage(session, formatter, note="buzz-select")
        session.commit()
    return {"candidates": len(candidates), "reply_suggestions": replies,
            "quote_suggestions": quotes}
