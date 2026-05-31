"""Claude Code連携(副): X操作を公開するMCPサーバー(stdio)。

導入:
  pip install -e ".[mcp]"
起動:
  xagent-mcp        (または python -m xagent.mcp_server)
これをClaude CodeのMCP設定に登録すると、ターミナルでClaudeに話しかけるだけで
整形→承認→投稿ができる。投稿系は必ず承認ゲートを通るので完全自動にはならない。
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from . import monitor as monitor_mod
from . import service
from .db import get_session, init_db
from .formatter import Formatter
from .models import DraftStatus
from .text import exceeds_fold, split_into_thread, weighted_length
from .x_client import XClient

mcp = FastMCP("xagent")


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
def compose(text: str, allow_long: bool = False) -> dict:
    """テキストを整形して未承認の下書きを作成する。"""
    init_db()
    with get_session() as s:
        d = service.create_post_draft(s, Formatter(), text, allow_long=allow_long)
        return {"id": d.id, "status": d.status.value, "segments": json.loads(d.segments_json)}


@mcp.tool()
def list_drafts(status: str | None = None) -> list[dict]:
    """下書きを一覧する。statusで絞り込み可。"""
    init_db()
    with get_session() as s:
        st = DraftStatus(status) if status else None
        return [
            {"id": d.id, "kind": d.kind.value, "status": d.status.value,
             "segments": json.loads(d.segments_json)}
            for d in service.list_drafts(s, status=st)
        ]


@mcp.tool()
def approve(draft_id: int) -> dict:
    """下書きを承認する。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        service.approve_draft(s, d)
        return {"id": d.id, "status": d.status.value}


@mcp.tool()
def post(draft_id: int) -> dict:
    """承認済み下書きを即時投稿する(X資格情報が必要)。"""
    init_db()
    with get_session() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            return {"error": "not found"}
        ids = service.post_draft(s, XClient.from_settings(), d)
        return {"id": d.id, "posted_tweet_ids": ids}


@mcp.tool()
def monitor_once() -> dict:
    """受信監視を1サイクル実行し、返信案・絡み案を生成する。"""
    init_db()
    with get_session() as s:
        x = XClient.from_settings()
        me = x.get_me()
        return monitor_mod.run_once(s, x, Formatter(), me["id"])


def main() -> None:
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()
