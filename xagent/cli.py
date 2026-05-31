"""Typer CLI。Claude Code/ターミナルからの入力面。

整形→承認→投稿の半自動フローと、監視・スタイル・対象管理・サーバ/デーモン起動を提供。
DBを使うコマンドは init_db を通してから実行する。LLM/X資格情報が要るコマンドは
未設定なら明示エラーを出す。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime

import typer

from . import __version__, monitor as monitor_mod, scheduler as scheduler_mod, service, style as style_mod
from .db import get_session, init_db
from .formatter import Formatter
from .models import DraftStatus
from .text import exceeds_fold, split_into_thread, weighted_length
from .x_client import XClient, XClientError

app = typer.Typer(help="Xエージェント CLI", no_args_is_help=True)


@contextmanager
def _db():
    init_db()
    with get_session() as s:
        yield s


def _x() -> XClient:
    try:
        return XClient.from_settings()
    except XClientError as e:
        raise typer.BadParameter(str(e))


def _print_draft(d) -> None:
    segs = json.loads(d.segments_json or "[]")
    typer.echo(f"#{d.id} [{d.kind.value}/{d.status.value}] {('@'+d.target_handle) if d.target_handle else ''}")
    for i, s in enumerate(segs, 1):
        typer.echo(f"   ({i}/{len(segs)}) {s}")


@app.command()
def version() -> None:
    """バージョン表示。"""
    typer.echo(f"xagent {__version__}")


@app.command("init-db")
def init_db_cmd() -> None:
    """SQLiteのテーブルを作成する。"""
    init_db()
    typer.echo("DB初期化完了。")


@app.command()
def preview(text: str, allow_long: bool = typer.Option(False, "--long")) -> None:
    """LLM不使用: 加重文字数・折りたたみ・スレッド分割を表示。"""
    typer.echo(f"加重文字数: {weighted_length(text)}  折りたたみ: {exceeds_fold(text)}")
    segs = [text.strip()] if allow_long else split_into_thread(text)
    for i, s in enumerate(segs, 1):
        typer.echo(f"  ({i}/{len(segs)}) ({weighted_length(s)}) {s}")


@app.command()
def compose(
    text: str,
    allow_long: bool = typer.Option(False, "--long"),
    emulate: str = typer.Option(None, "--emulate", help="真似るアカウント(@handle)。学習済みのみ有効"),
    variations: int = typer.Option(1, "--variations", help="言い回し違いをN案生成(最大5)"),
) -> None:
    """テキストをClaudeで整形し、未承認の下書きを作成する。"""
    with _db() as s:
        f = Formatter()
        if variations > 1:
            ds = service.create_post_variations(
                s, f, text, n=variations, allow_long=allow_long, emulate_handle=emulate
            )
            for d in ds:
                _print_draft(d)
        else:
            d = service.create_post_draft(
                s, f, text, allow_long=allow_long, emulate_handle=emulate
            )
            _print_draft(d)
            typer.echo("承認するには: xagent approve {}".format(d.id))


@app.command("learn-account")
def learn_account_cmd(
    handle: str,
    max_posts: int = typer.Option(200, "--max", help="取得上限"),
    is_self: bool = typer.Option(False, "--self", help="自分のアカウントとして学習"),
) -> None:
    """指定ユーザーの投稿を取得し、特徴をAIで抽出してプロファイル保存する。"""
    from . import profiles

    with _db() as s:
        prof = profiles.learn_account(
            s, _x(), Formatter().complete, handle, max_total=max_posts, is_self=is_self
        )
        typer.echo(
            f"@{prof.handle} を学習: {prof.posts_fetched}件 / 口調: {prof.profile_text[:60]}"
        )


@app.command("profiles-list")
def profiles_list_cmd() -> None:
    """学習済みアカウント・プロファイルの一覧。"""
    from . import profiles

    with _db() as s:
        for p in profiles.list_profiles(s):
            tag = "自分" if p.is_self else "他人"
            typer.echo(
                f"#{p.id} @{p.handle} [{tag}] posts={p.posts_fetched} likes_avg={p.avg_likes:.1f}"
            )


@app.command("monitor-config")
def monitor_config(
    mentions: bool = typer.Option(None, "--mentions/--no-mentions"),
    manual: bool = typer.Option(None, "--manual/--no-manual"),
    keyword: bool = typer.Option(None, "--keyword/--no-keyword"),
    following: bool = typer.Option(None, "--following/--no-following"),
) -> None:
    """絡み監視ソースのオン/オフを表示・変更する。"""
    with _db() as s:
        flags = {}
        if mentions is not None:
            flags["mentions_enabled"] = mentions
        if manual is not None:
            flags["manual_targets_enabled"] = manual
        if keyword is not None:
            flags["keyword_search_enabled"] = keyword
        if following is not None:
            flags["following_enabled"] = following
        if flags:
            monitor_mod.set_monitor_settings(s, **flags)
        cfg = monitor_mod.get_monitor_settings(s)
        typer.echo(
            f"mentions={cfg.mentions_enabled} manual={cfg.manual_targets_enabled} "
            f"keyword={cfg.keyword_search_enabled} following={cfg.following_enabled}"
        )


@app.command("list")
def list_cmd(status: str = typer.Option(None, help="draft/approved/queued/posted/rejected")) -> None:
    """下書き一覧。"""
    with _db() as s:
        st = DraftStatus(status) if status else None
        for d in service.list_drafts(s, status=st):
            _print_draft(d)


@app.command()
def show(draft_id: int) -> None:
    """下書きの詳細表示。"""
    with _db() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            raise typer.BadParameter("見つかりません。")
        _print_draft(d)


@app.command()
def approve(draft_id: int) -> None:
    """下書きを承認する。"""
    with _db() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            raise typer.BadParameter("見つかりません。")
        service.approve_draft(s, d)
        typer.echo(f"#{draft_id} を承認しました。投稿: xagent post {draft_id} / 予約: xagent queue {draft_id}")


@app.command()
def reject(draft_id: int) -> None:
    """下書きを却下する。"""
    with _db() as s:
        d = service.get_draft(s, draft_id)
        if d:
            service.reject_draft(s, d)
            typer.echo(f"#{draft_id} を却下しました。")


@app.command()
def queue(draft_id: int, at: str = typer.Option(None, help="ISO時刻(UTC naive)。未指定は最適時間")) -> None:
    """承認済み下書きをキューへ(最適時間 or 指定時刻)。"""
    with _db() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            raise typer.BadParameter("見つかりません。")
        if at:
            service.queue_draft(s, d, scheduled_at=service.to_naive_utc(datetime.fromisoformat(at)))
        else:
            scheduler_mod.schedule_optimal(s, d)
        typer.echo(f"#{draft_id} をキューに入れました。予約時刻: {d.scheduled_at}")


@app.command()
def post(draft_id: int) -> None:
    """承認済み下書きを即時投稿する(X資格情報が必要)。"""
    with _db() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            raise typer.BadParameter("見つかりません。")
        ids = service.post_draft(s, _x(), d)
        typer.echo(f"投稿しました: {ids}")


@app.command("targets-add")
def targets_add(handle: str) -> None:
    """絡み対象を追加(user_idはX APIで解決を試みる)。"""
    from .models import EngageTarget, TargetKind

    handle = handle.lstrip("@")
    user_id = None
    try:
        info = _x().get_user_by_username(handle)
        user_id = info["id"] if info else None
    except Exception:
        pass
    with _db() as s:
        t = EngageTarget(kind=TargetKind.MANUAL, handle=handle, user_id=user_id)
        s.add(t)
        s.commit()
        s.refresh(t)
        typer.echo(f"追加: @{handle} (user_id={user_id})")


@app.command("targets-list")
def targets_list() -> None:
    """絡み対象の一覧。"""
    from sqlmodel import select

    from .models import EngageTarget

    with _db() as s:
        for t in s.exec(select(EngageTarget)).all():
            typer.echo(f"#{t.id} @{t.handle} user_id={t.user_id} active={t.active}")


@app.command("style-set")
def style_set(text: str) -> None:
    """スタイルガイドを設定する。"""
    with _db() as s:
        style_mod.set_style_guide(s, text)
        typer.echo("スタイルガイドを更新しました。")


@app.command("style-show")
def style_show() -> None:
    """スタイルガイドを表示する。"""
    with _db() as s:
        typer.echo(style_mod.active_style_guide(s) or "(未設定)")


@app.command()
def learn() -> None:
    """自分の過去投稿をX APIで取得して学習データに保存する。"""
    with _db() as s:
        x = _x()
        me = x.get_me()
        n = style_mod.learn_past_posts(s, x, me["id"])
        typer.echo(f"{n}件の過去投稿を保存しました。")


@app.command("monitor-once")
def monitor_once() -> None:
    """受信監視を1サイクル実行(返信案・絡み案を生成)。"""
    with _db() as s:
        x = _x()
        me = x.get_me()
        res = monitor_mod.run_once(s, x, Formatter(), me["id"])
        typer.echo(f"生成: {res}")


@app.command()
def daemon(
    poll_seconds: int = typer.Option(180, help="監視ポーリング間隔(秒)"),
    queue_seconds: int = typer.Option(60, help="投稿キュー処理間隔(秒)"),
) -> None:
    """監視＋投稿キュー処理の常駐デーモンを起動する。"""
    from .daemon import run

    run(poll_seconds=poll_seconds, queue_seconds=queue_seconds)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """FastAPI(Web UI API)を起動する。"""
    import uvicorn

    uvicorn.run("xagent.api.main:app", host=host, port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
