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
from .text import char_length, exceeds_fold, split_into_thread, weighted_length
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
    if d.target_text:
        typer.echo(f"   ↳ 元: {d.target_text[:60]}")
    for i, s in enumerate(segs, 1):
        typer.echo(f"   ({i}/{len(segs)}) {s}")


def _draft_dict(d) -> dict:
    """エージェントがパースしやすい機械可読な下書き表現(元ポスト本文・状態・対象込み)。"""
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
    from .commands import extract_tweet_ref

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
    """LLM不使用: 字数(140基準)・折りたたみ・スレッド分割を表示。"""
    typer.echo(
        f"字数: {char_length(text)}/140  加重: {weighted_length(text)}  折りたたみ: {exceeds_fold(text)}"
    )
    segs = [text.strip()] if allow_long else split_into_thread(text)
    for i, s in enumerate(segs, 1):
        typer.echo(f"  ({i}/{len(segs)}) ({char_length(s)}字) {s}")


@app.command()
def compose(
    text: str,
    allow_long: bool = typer.Option(False, "--long"),
    raw: bool = typer.Option(False, "--raw", help="整形(LLM)せず、自分が書いた文をそのまま下書き化"),
    emulate: str = typer.Option(None, "--emulate", help="真似るアカウント(@handle)。学習済みのみ有効"),
    variations: int = typer.Option(1, "--variations", help="言い回し違いをN案生成(最大5)"),
) -> None:
    """テキストをClaudeで整形(--rawで整形なし)し、未承認の下書きを作成する。

    エージェントが自分で最終文を書いた場合は --raw を使う(再整形しない)。
    """
    with _db() as s:
        f = Formatter()
        if variations > 1 and not raw:
            ds = service.create_post_variations(
                s, f, text, n=variations, allow_long=allow_long, emulate_handle=emulate
            )
            for d in ds:
                _print_draft(d)
        else:
            d = service.create_post_draft(
                s, f, text, allow_long=allow_long, emulate_handle=emulate, raw=raw
            )
            _print_draft(d)
            typer.echo("承認するには: xagent approve {}".format(d.id))


@app.command()
def reply(
    target: str,
    text: str,
    raw: bool = typer.Option(False, "--raw", help="整形せず、書いた文をそのまま返信本文に"),
    emulate: str = typer.Option(None, "--emulate", help="真似るアカウント(@handle)"),
) -> None:
    """指定ツイート(URL/ID)に、自分の文でリプライする下書きを作る。元ポスト本文も保存する。"""
    tweet_id, handle = _resolve_target(target)
    ttext, thandle = _fetch_target_text(tweet_id)
    with _db() as s:
        d = service.create_reply_command_draft(
            s, Formatter(), tweet_id, text,
            target_handle=handle or thandle, target_text=ttext,
            raw=raw, emulate_handle=emulate,
        )
        _print_draft(d)
        typer.echo("承認: xagent approve {} → 送信: xagent post {}".format(d.id, d.id))


@app.command()
def quote(
    target: str,
    text: str,
    raw: bool = typer.Option(False, "--raw", help="整形せず、書いた文をそのまま引用コメントに"),
    emulate: str = typer.Option(None, "--emulate", help="真似るアカウント(@handle)"),
) -> None:
    """指定ツイート(URL/ID)を、自分のコメント付きで引用RTする下書きを作る。引用元本文も保存する。"""
    tweet_id, handle = _resolve_target(target)
    ttext, thandle = _fetch_target_text(tweet_id)
    with _db() as s:
        d = service.create_quote_command_draft(
            s, Formatter(), tweet_id, text,
            target_handle=handle or thandle, target_text=ttext,
            raw=raw, emulate_handle=emulate,
        )
        _print_draft(d)
        typer.echo("承認: xagent approve {} → 送信: xagent post {}".format(d.id, d.id))


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
    max_drafts: int = typer.Option(None, "--max", help="1監視サイクルの総生成数上限(API圧迫防止)"),
) -> None:
    """絡み監視ソースのオン/オフと1サイクルの生成数上限を表示・変更する。"""
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
        if max_drafts is not None:
            flags["max_drafts_per_run"] = max_drafts
        if flags:
            monitor_mod.set_monitor_settings(s, **flags)
        cfg = monitor_mod.get_monitor_settings(s)
        typer.echo(
            f"mentions={cfg.mentions_enabled} manual={cfg.manual_targets_enabled} "
            f"keyword={cfg.keyword_search_enabled} following={cfg.following_enabled} "
            f"max_drafts_per_run={cfg.max_drafts_per_run}"
        )


@app.command("list")
def list_cmd(
    status: str = typer.Option(None, help="draft/approved/queued/posted/rejected"),
    json_out: bool = typer.Option(False, "--json", help="機械可読JSONで出力(エージェント向け)"),
) -> None:
    """下書き一覧(--json で機械可読出力)。"""
    with _db() as s:
        st = DraftStatus(status) if status else None
        drafts = service.list_drafts(s, status=st)
        if json_out:
            typer.echo(json.dumps([_draft_dict(d) for d in drafts], ensure_ascii=False))
        else:
            for d in drafts:
                _print_draft(d)


@app.command()
def show(
    draft_id: int,
    json_out: bool = typer.Option(False, "--json", help="機械可読JSONで出力(エージェント向け)"),
) -> None:
    """下書きの詳細表示(--json で元ポスト本文・状態・対象を含む機械可読出力)。"""
    with _db() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            raise typer.BadParameter("見つかりません。")
        if json_out:
            typer.echo(json.dumps(_draft_dict(d), ensure_ascii=False))
        else:
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
def cancel(draft_id: int) -> None:
    """下書き/承認済み/予約を取消(ゴミ箱へ)。予約済みは自動投稿もキャンセルされる。"""
    with _db() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            raise typer.BadParameter("見つかりません。")
        service.cancel_draft(s, d)
        typer.echo(f"#{draft_id} を取消しました(ゴミ箱)。復元: xagent restore {draft_id}")


@app.command()
def restore(draft_id: int) -> None:
    """取消(ゴミ箱)から下書きへ復元する。"""
    with _db() as s:
        d = service.get_draft(s, draft_id)
        if not d:
            raise typer.BadParameter("見つかりません。")
        service.restore_draft(s, d)
        typer.echo(f"#{draft_id} を下書きに復元しました。")


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


@app.command("x-list-create")
def x_list_create(
    name: str = typer.Argument(..., help="リスト名"),
    accounts: str = typer.Argument("", help="ハンドルのカンマ区切り(@有無どちらでも)"),
    file: str = typer.Option(None, "--file", "-f", help="ハンドルを1行1件で書いたファイル"),
    description: str = typer.Option("", "--description", help="リストの説明"),
    private: bool = typer.Option(True, "--private/--public", help="非公開(既定)か公開か"),
) -> None:
    """ハンドル一覧からXネイティブのリストを作成し、解決できたアカウントを一括追加する。"""
    from . import lists as lists_mod

    handles = accounts.replace("\n", ",").split(",")
    if file:
        with open(file, encoding="utf-8") as fh:
            handles += lists_mod.read_handle_lines(fh.read())  # #コメント/空行を除外
    result = lists_mod.create_list_from_handles(
        _x(), name, handles, description=description, private=private
    )
    typer.echo(f"作成: {result['name']} → {result['url']}")
    added = ", ".join("@" + h for h in result["added"]) or "(なし)"
    typer.echo(f"追加 {len(result['added'])}件: {added}")
    if result["skipped"]:
        typer.echo(f"スキップ {len(result['skipped'])}件:")
        for sk in result["skipped"]:
            typer.echo(f"  @{sk['handle']}: {sk['reason']}")


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
