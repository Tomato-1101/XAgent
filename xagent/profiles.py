"""アカウント単位のプロファイル学習。

指定ユーザー(自分/他人)の投稿を可能な限り取得し、AIで「特徴」をテキスト抽出して
AccountProfile としてDBに保存する。投稿時に「誰を真似るか」として選択できる
(常時適用の口調は StyleProfile.guide_text。こちらは選択時のみ整形に乗せる)。

設計:
- LLM呼び出しは complete(system, user)->str に抽象化(Formatter.complete を渡す)。テストはフェイク注入。
- 投稿時刻のヒストグラムは created_at から純計算(LLM不使用)。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from .cost import log_cost
from .models import AccountProfile, CostKind, PastPost

CompleteFn = Callable[[str, str], str]

PROFILE_MAX_POSTS = 200      # 1アカウントあたり取得上限(コスト/API上限対策)
_SAMPLE_FOR_LLM = 60         # 抽出プロンプトに渡す投稿サンプル数


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def fetch_account_posts(
    session: Session,
    x_client,
    handle: str,
    max_total: int = PROFILE_MAX_POSTS,
    is_self: bool = False,
) -> tuple[str, str, list[dict], int]:
    """handle のユーザーの投稿を取得し PastPost に保存。(user_id, username, tweets, saved) を返す。"""
    handle = handle.lstrip("@")
    info = x_client.get_user_by_username(handle)
    if not info:
        raise ValueError(f"ユーザーが見つかりません: @{handle}")
    user_id = info["id"]
    username = info.get("username", handle)
    tweets = x_client.get_user_tweets_full(user_id, max_total=max_total)
    log_cost(session, CostKind.READ, units=len(tweets), note=f"learn:@{handle}")

    saved = 0
    for t in tweets:
        exists = session.exec(
            select(PastPost).where(PastPost.tweet_id == t["id"])
        ).first()
        if exists:
            continue
        session.add(
            PastPost(
                tweet_id=t["id"],
                text=t.get("text", ""),
                created_at=_to_naive_utc(t.get("created_at")),
                like_count=int(t.get("like_count", 0)),
                retweet_count=int(t.get("retweet_count", 0)),
                author_user_id=user_id,
                author_handle=handle,
                is_own=is_self,
            )
        )
        saved += 1
    session.commit()
    return user_id, username, tweets, saved


def compute_posting_times(tweets: list[dict], tz_name: str = "Asia/Tokyo") -> list[list[int]]:
    """投稿の created_at をローカル時間帯のヒストグラム [[hour, count], ...] にする。"""
    tz = ZoneInfo(tz_name)
    counter: Counter[int] = Counter()
    for t in tweets:
        dt = t.get("created_at")
        if dt is None:
            continue
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        counter[dt.astimezone(tz).hour] += 1
    return [[h, counter.get(h, 0)] for h in range(24)]


_EXTRACT_SYSTEM = """あなたはSNS運用アナリスト。あるXアカウントの投稿サンプルから、その人の発信の特徴を抽出する。
出力は厳密なJSONのみ(前後に説明やコードフェンスを付けない)。キーは次の通り:
{
 "tone": "口調・語り口の特徴(一文)",
 "themes": ["主な話題/テーマ", ...],
 "post_style": "投稿の型・構成の特徴(一文)",
 "hooks": "引きの作り方・冒頭の癖(一文)",
 "hashtags": "ハッシュタグ/絵文字の使い方(一文)",
 "cadence": "長さ・頻度の傾向(一文)",
 "summary": "このアカウントを真似て書くときの指針を3〜5文で"
}"""


def extract_profile(complete: CompleteFn, tweets: list[dict], handle: str) -> tuple[str, str]:
    """投稿サンプルからAIで特徴を抽出。(profile_json, profile_text) を返す。"""
    samples = [t.get("text", "") for t in tweets if t.get("text")][:_SAMPLE_FOR_LLM]
    if not samples:
        return "{}", ""
    joined = "\n".join(f"- {s}" for s in samples)
    raw = complete(_EXTRACT_SYSTEM, f"@{handle} の投稿サンプル:\n{joined}").strip()
    # コードフェンスが付いた場合に剥がす
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{") :] if "{" in raw else raw
    try:
        data = json.loads(raw)
        text = str(data.get("summary", "")).strip()
        return json.dumps(data, ensure_ascii=False), text
    except (json.JSONDecodeError, ValueError):
        # JSONにできなければ全文をprofile_textとして温存
        return "{}", raw


def learn_account(
    session: Session,
    x_client,
    complete: CompleteFn,
    handle: str,
    max_total: int = PROFILE_MAX_POSTS,
    is_self: bool = False,
    tz_name: str = "Asia/Tokyo",
) -> AccountProfile:
    """取得→時間集計→特徴抽出→AccountProfile を upsert する。"""
    handle = handle.lstrip("@")
    user_id, username, tweets, _saved = fetch_account_posts(
        session, x_client, handle, max_total=max_total, is_self=is_self
    )
    active_hours = compute_posting_times(tweets, tz_name)
    likes = [int(t.get("like_count", 0)) for t in tweets]
    rts = [int(t.get("retweet_count", 0)) for t in tweets]
    avg_likes = sum(likes) / len(likes) if likes else 0.0
    avg_rts = sum(rts) / len(rts) if rts else 0.0
    profile_json, profile_text = extract_profile(complete, tweets, handle)

    prof = session.exec(
        select(AccountProfile).where(AccountProfile.handle == handle)
    ).first()
    if prof is None:
        prof = AccountProfile(handle=handle)
        session.add(prof)
    prof.user_id = user_id
    prof.is_self = is_self
    prof.display_name = username
    prof.posts_fetched = len(tweets)
    prof.avg_likes = avg_likes
    prof.avg_retweets = avg_rts
    prof.active_hours_json = json.dumps(active_hours, ensure_ascii=False)
    prof.profile_json = profile_json
    prof.profile_text = profile_text
    prof.updated_at = _utcnow_naive()
    session.commit()
    session.refresh(prof)
    return prof


def list_profiles(session: Session) -> list[AccountProfile]:
    return list(session.exec(select(AccountProfile).order_by(AccountProfile.handle)).all())


def get_profile(session: Session, account_id: int) -> AccountProfile | None:
    return session.get(AccountProfile, account_id)


def get_profile_by_handle(session: Session, handle: str) -> AccountProfile | None:
    handle = handle.lstrip("@")
    return session.exec(
        select(AccountProfile).where(AccountProfile.handle == handle)
    ).first()


def example_posts_for_account(session: Session, handle: str, n: int = 8) -> list[str]:
    """その相手を真似る際の few-shot 用に、エンゲージの高い代表投稿を返す。"""
    handle = handle.lstrip("@")
    rows = session.exec(
        select(PastPost)
        .where(PastPost.author_handle == handle)
        .order_by(PastPost.like_count.desc())
        .limit(n)
    ).all()
    return [r.text for r in rows]
