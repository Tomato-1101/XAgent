"""Claude Code連携: X操作を公開するMCPサーバー(stdio)。

導入:
  pip install -e ".[mcp]"
起動:
  xagent-mcp        (または python -m xagent.mcp_server)
これをClaude CodeのMCP設定に登録すると、ターミナルでClaudeに話しかけるだけで
調査→整形→（リプライ/引用/リポストの）下書き作成→承認→投稿/予約まで操作できる。

設計の背骨は CLI と同じ「AI下書き→人間承認→投稿」の半自動フロー。投稿系(post/repost)は
承認を前提とし、完全自動はしない。各ツールの戻り値には target_text(元ポスト本文)・status・
kind・target_handle を含め、エージェントが文脈を把握しやすくする。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP
from sqlmodel import select

from . import monitor as monitor_mod
from . import scheduler as scheduler_mod
from . import service
from .commands import extract_tweet_ref
from .db import get_session, init_db
from .formatter import Formatter
from .models import DraftStatus, PastPost
from .text import exceeds_fold, split_into_thread, weighted_length
from .x_client import XClient

mcp = FastMCP("xagent")


# --- 共通ヘルパ -------------------------------------------------------------

def _draft_view(d) -> dict:
    """エージェントが扱いやすい下書き表現(元ポスト本文・状態・対象込み)。"""
    return {
        "id": d.id,
        "kind": d.kind.value,
        "status": d.status.value,
        "target_tweet_id": d.target_tweet_id,
        "target_handle": d.target_handle,
        "target_text": d.target_text,
        "segments": json.loads(d.segments_json or "[]"),
        "scheduled_at": d.scheduled_at.isoformat() if d.scheduled_at else None,
    }


def _resolve_target(url_or_id: str) -> tuple[str, str | None]:
    """URL or 素のID から tweet_id を取り出す。URLなら handle も返す。"""
    ref = extract_tweet_ref(url_or_id)
    if ref:
        tweet_id, handle, _ = ref
        return tweet_id, handle
    return url_or_id.strip(), None


def _fetch_target_text(tweet_id: str) -> tuple[str, str | None]:
    """対象ツイート本文を best-effort で取得(資格情報未設定でもエラーにしない)。"""
    try:
        t = XClient.from_settings().get_tweet(tweet_id)
    except Exception:
        return "", None
    if not t:
        return "", None
    return t.get("text", ""), t.get("author_handle")


# --- 整形・下書き作成 -------------------------------------------------------

@mcp.tool()
def preview(text: str, allow_long: bool = False) -> dict:
    """LLM不使用で文字数・折りたたみ・スレッド分割を返す。"""
    segs = [text.strip()] if allow_long else split_into_thread(text)
    return {
        "weighted_length": weighted_length(text),
        "folded": exceeds_fold(text),
        "segments": segs,
    }


@mcp.tool()
def compose(
    text: str, allow_long: bool = False, raw: bool = False, emulate: str | None = None
) -> dict:
    """テキストを整形して未承認の下書きを作成する。

    エージェントが自分で最終文を書いた場合は raw=True を使う(再整形しない)。
    emulate に学習済みアカウント(@handle)を指定すると、その口調に寄せる。
    """
    init_db()
    with get_session() as s:
        d = service.create_post_draft(
            s, Formatter(), text, allow_long=allow_long, raw=raw, emulate_handle=emulate
        )
        return _draft_view(d)


@mcp.tool()
def reply(target: str, text: str, raw: bool = False, emulate: str | None = None) -> dict:
    """指定ツイート(URL/ID)に、自分の文でリプライする下書きを作る。元ポスト本文も保存する。"""
    init_db()
    tweet_id, handle = _resolve_target(target)
    ttext, thandle = _fetch_target_text(tweet_id)
    with get_session() as s:
        d = service.create_reply_command_draft(
            s, Formatter(), tweet_id, text,
            target_handle=handle or thandle, target_text=ttext,
            raw=raw, emulate_handle=emulate,
        )
        return _draft_view(d)


@mcp.tool()
def quote(target: str, text: str, raw: bool = False, emulate: str | None = None) -> dict:
    """指定ツイート(URL/ID)を、自分のコメント付きで引用RTする下書きを作る。引用元本文も保存する。"""
    init_db()
    tweet_id, handle = _resolve_target(target)
    ttext, thandle = _fetch_target_text(tweet_id)
    with get_session() as s:
        d = service.create_quote_command_draft(
            s, Formatter(), tweet_id, text,
            target_handle=handle or thandle, target_text=ttext,
            raw=raw, emulate_handle=emulate,
        )
        return _draft_view(d)


# --- 取得・編集 -------------------------------------------------------------

@mcp.tool()
def list_drafts(status: str | None = None) -> list[dict]:
    """下書きを一覧する。statusで絞り込み可(draft/approved/queued/posted/rejected/canceled)。"""
    init_db()
    with get_session() as s:
        st = DraftStatus(status) if status else None
        return [_draft_view(d) for d in service.list_drafts(s, status=st)]


@mcp.tool()
def get_draft(draft_id: int) -> dict:
    """下書きの詳細(元ポスト本文・状態・対象込み)を返す。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        return _draft_view(d) if d else {"error": "not found"}


@mcp.tool()
def update_segments(draft_id: int, segments: list[str]) -> dict:
    """承認前に本文(セグメント)を手修正する。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        service.update_segments(s, d, segments)
        return _draft_view(d)


# --- 状態遷移 ---------------------------------------------------------------

@mcp.tool()
def approve(draft_id: int) -> dict:
    """下書きを承認する。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        service.approve_draft(s, d)
        return _draft_view(d)


@mcp.tool()
def reject(draft_id: int) -> dict:
    """下書きを却下する。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        service.reject_draft(s, d)
        return _draft_view(d)


@mcp.tool()
def cancel(draft_id: int) -> dict:
    """下書き/承認済み/予約を取消(ゴミ箱へ)。予約済みは自動投稿もキャンセルされる。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        service.cancel_draft(s, d)
        return _draft_view(d)


@mcp.tool()
def restore(draft_id: int) -> dict:
    """取消(ゴミ箱)から下書きへ復元する。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        service.restore_draft(s, d)
        return _draft_view(d)


@mcp.tool()
def queue(draft_id: int, at: str | None = None) -> dict:
    """承認済み下書きをキューへ。at(ISO時刻)未指定なら最適時間に自動割当。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        if at:
            service.queue_draft(
                s, d, scheduled_at=service.to_naive_utc(datetime.fromisoformat(at))
            )
        else:
            scheduler_mod.schedule_optimal(s, d)
        return _draft_view(d)


@mcp.tool()
def post(draft_id: int) -> dict:
    """承認済み下書きを即時投稿する(X資格情報が必要)。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        ids = service.post_draft(s, XClient.from_settings(), d)
        return {**_draft_view(d), "posted_tweet_ids": ids}


# --- 監視・リポスト ---------------------------------------------------------

@mcp.tool()
def monitor_once() -> dict:
    """受信監視を1サイクル実行し、返信案・絡み案を生成する(生成数は上限設定で制御)。"""
    init_db()
    with get_session() as s:
        x = XClient.from_settings()
        me = x.get_me()
        return monitor_mod.run_once(s, x, Formatter(), me["id"])


@mcp.tool()
def monitor_settings(
    mentions: bool | None = None,
    manual: bool | None = None,
    keyword: bool | None = None,
    following: bool | None = None,
    max_drafts_per_run: int | None = None,
) -> dict:
    """監視ソースのトグルと1サイクルの総生成数上限を取得/変更する(引数省略で現在値のみ返す)。"""
    init_db()
    with get_session() as s:
        flags: dict = {}
        if mentions is not None:
            flags["mentions_enabled"] = mentions
        if manual is not None:
            flags["manual_targets_enabled"] = manual
        if keyword is not None:
            flags["keyword_search_enabled"] = keyword
        if following is not None:
            flags["following_enabled"] = following
        if max_drafts_per_run is not None:
            flags["max_drafts_per_run"] = max_drafts_per_run
        if flags:
            monitor_mod.set_monitor_settings(s, **flags)
        cfg = monitor_mod.get_monitor_settings(s)
        return {
            "mentions_enabled": cfg.mentions_enabled,
            "manual_targets_enabled": cfg.manual_targets_enabled,
            "keyword_search_enabled": cfg.keyword_search_enabled,
            "following_enabled": cfg.following_enabled,
            "max_drafts_per_run": cfg.max_drafts_per_run,
        }


@mcp.tool()
def recent_posts(days: int = 7) -> list[dict]:
    """DBにキャッシュ済みの自分の直近投稿(リポスト候補の確認用)。取得更新はWeb API/デーモン側。"""
    init_db()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with get_session() as s:
        rows = s.exec(
            select(PastPost)
            .where(PastPost.is_own == True)  # noqa: E712
            .order_by(PastPost.created_at.desc())
        ).all()
        out = []
        for r in rows:
            if r.created_at is None or r.created_at < cutoff:
                continue
            out.append(
                {
                    "tweet_id": r.tweet_id,
                    "text": r.text,
                    "like_count": r.like_count,
                    "retweet_count": r.retweet_count,
                }
            )
        return out


@mcp.tool()
def repost(tweet_id: str, at: str | None = None) -> dict:
    """自分の過去投稿を通常リポスト(コメント無し)。at未指定は即時、ISO指定で予約。"""
    init_db()
    with get_session() as s:
        d = service.create_repost_draft(s, tweet_id)
        service.approve_draft(s, d)
        if at:
            service.queue_draft(
                s, d, scheduled_at=service.to_naive_utc(datetime.fromisoformat(at))
            )
        else:
            service.post_draft(s, XClient.from_settings(), d)
        return _draft_view(d)


def main() -> None:
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()
