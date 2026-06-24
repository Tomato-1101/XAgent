"""常駐デーモン。今はMacローカルで動かす(将来クラウド24h)。

- 監視ティック: メンション/絡み対象をポーリングし、返信案・絡み案を下書き生成。新規が出たら通知。
- キュー ティック: 予約/最適時間の到来分を投稿(承認＋頻度ガードを通過したもののみ)。

各ティックは例外を握って継続し、デーモンを止めない(失敗はstdoutへ)。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from .db import get_session, init_db
from .formatter import Formatter
from .monitor import (
    BUZZ_BACKLOG_MAX,
    engage_backlog_count,
    get_monitor_settings,
    run_buzz_once,
    run_celeb_once,
    run_once,
)
from .notify import notify
from .scheduler import process_due_queue
from .x_client import XClient, XClientError

log = logging.getLogger("xagent.daemon")

# スケジューラ稼働確認用ハートビート(naive UTC)。queue_tick が発火するたびに更新し、
# /status が「予約スケジューラが実際に回っているか」を判定する。スケジューラは API と同一
# プロセスで動くためモジュール変数を共有できる(--reload のワーカ再起動時はNoneに戻り次ティックで復帰)。
_last_queue_tick_at: datetime | None = None


def last_queue_tick_at() -> datetime | None:
    """直近で queue_tick が発火した時刻(naive UTC)。未発火なら None。"""
    return _last_queue_tick_at


def monitor_tick() -> None:
    try:
        with get_session() as s:
            # UIの自動運用スイッチ(毎ティック参照)。OFFならプロセスは動かしたまま処理だけ止める。
            if not get_monitor_settings(s).auto_monitor_enabled:
                return
            x = XClient.from_settings()
            me = x.get_me()
            res = run_once(s, x, Formatter(), me["id"])
            total = res.get("reply_suggestions", 0) + res.get("quote_suggestions", 0)
            if total:
                notify("XAgent: 承認待ち", f"返信案{res['reply_suggestions']}件 / 絡み案{res['quote_suggestions']}件")
            log.info("monitor_tick: %s", res)
    except XClientError as e:
        log.warning("monitor_tick: X資格情報未設定 (%s)", e)
    except Exception:
        log.exception("monitor_tick failed")


def celeb_tick() -> None:
    """有名人ウォッチ: リストの有名人がAIについて投稿したら即絡み案を生成する。

    celeb_watch_enabled(既定OFF)で制御。検出は検索1〜2クエリ/回(タイムライン巡回なし)で、
    ヒットが無ければLLMを呼ばない(API節約)。下書きのみ生成・自動投稿しない。
    """
    try:
        with get_session() as s:
            cfg = get_monitor_settings(s)
            if not cfg.celeb_watch_enabled or not cfg.celeb_list_id:
                return
            x = XClient.from_settings()
            res = run_celeb_once(s, x, Formatter())
            total = res.get("reply_suggestions", 0) + res.get("quote_suggestions", 0)
            if total:
                notify("XAgent: 有名人のAI投稿に絡み案", f"{total}件の下書きを生成しました")
                log.info("celeb_tick: %s", res)
    except XClientError as e:
        log.warning("celeb_tick: X資格情報未設定 (%s)", e)
    except Exception:
        log.exception("celeb_tick failed")


def buzz_tick() -> None:
    """バズウォッチ: いいね数の実績(min_faves検索)で「既にバズった投稿」を網羅的に拾い、
    絡み案を生成する。buzz_watch_enabled(既定OFF)で制御。

    自動tickは承認待ちの絡み案が BUZZ_BACKLOG_MAX 件以上溜まっていたら生成しない
    (承認が追いつかないまま乱造しない)。手動の「今すぐチェック」はこのガードを通らない。
    """
    try:
        with get_session() as s:
            cfg = get_monitor_settings(s)
            if not cfg.buzz_watch_enabled:
                return
            backlog = engage_backlog_count(s)
            if backlog >= BUZZ_BACKLOG_MAX:
                log.info("buzz_tick: 承認待ち%s件のためスキップ(乱造ガード)", backlog)
                return
            x = XClient.from_settings()
            res = run_buzz_once(s, x, Formatter())
            total = res.get("reply_suggestions", 0) + res.get("quote_suggestions", 0)
            if total:
                notify("XAgent: バズ投稿に絡み案", f"{total}件の下書きを生成しました")
                log.info("buzz_tick: %s", res)
    except XClientError as e:
        log.warning("buzz_tick: X資格情報未設定 (%s)", e)
    except Exception:
        log.exception("buzz_tick failed")


def news_tick() -> None:
    """ニュース速報の自動下書き生成(ダイジェスト連動)。auto_news_enabled(既定OFF)で制御。

    ONでも新着ダイジェストが無ければ何もしない(XNewsBotのDBを読み取り専用で覗くだけ)。
    生成は下書きまで(投稿は人間承認必須)。
    """
    from .news import NewsSourceUnavailable, get_news_settings, run_news_once

    try:
        with get_session() as s:
            if not get_news_settings(s).auto_news_enabled:
                return
            res = run_news_once(s, Formatter())
            if res["created"]:
                notify("XAgent: ニュース速報案", f"{res['created']}件の下書きを生成しました")
            log.info("news_tick: %s", res)
    except NewsSourceUnavailable as e:
        log.warning("news_tick: %s", e)
    except Exception:
        log.exception("news_tick failed")


def post_gen_tick() -> None:
    """オリジナル投稿の叩き台の自動生成。auto_post_gen_enabled(既定OFF)で制御。

    生成系のため既定OFF・乱造ガード(承認待ちPOSTが post_backlog_max 件以上ならスキップ)。
    下書きを作るだけで投稿しない(人間承認必須)。中身の核はユーザーが埋める前提。
    """
    from .post_gen import get_post_settings, post_backlog_count, run_post_gen_once

    try:
        with get_session() as s:
            cfg = get_post_settings(s)
            if not cfg.auto_post_gen_enabled:
                return
            backlog = post_backlog_count(s)
            if backlog >= cfg.post_backlog_max:
                log.info("post_gen_tick: 承認待ちPOST%s件のためスキップ(乱造ガード)", backlog)
                return
            res = run_post_gen_once(s, Formatter())
            if res["created"]:
                notify("XAgent: オリジナル投稿の叩き台", f"{res['created']}件の下書きを生成しました")
            log.info("post_gen_tick: %s", res)
    except Exception:
        log.exception("post_gen_tick failed")


def metrics_tick() -> None:
    """自分の投稿メトリクスの自動取得。metrics_enabled(既定ON)で制御。

    公式APIで自分の直近投稿を non_public_metrics 付きで取得し PostMetric に upsert する
    読み取りのみの処理(投稿しない)。1日数リクエストと軽量・無害なため既定ONで回す。
    """
    from .metrics import collect_metrics_once, get_metrics_settings

    try:
        with get_session() as s:
            if not get_metrics_settings(s).metrics_enabled:
                return
            x = XClient.from_settings()
            res = collect_metrics_once(s, x)
            log.info("metrics_tick: %s", res)
    except XClientError as e:
        log.warning("metrics_tick: X資格情報未設定 (%s)", e)
    except Exception:
        log.exception("metrics_tick failed")


def queue_tick() -> None:
    # 予約投稿の発火は常時動かす(ユーザー方針: 予約投稿は止めない)。
    # 全投稿の緊急停止は config.posting_enabled が、個別の制限帯/頻度ガードは process_due_queue が担う。
    global _last_queue_tick_at
    # ハートビートはX資格情報の有無に関わらず先に更新する(スケジューラの生存=ティック発火の事実を示す)。
    _last_queue_tick_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with get_session() as s:
            x = XClient.from_settings()
            res = process_due_queue(s, x)
            if res["posted"]:
                log.info("queue_tick posted: %s", res["posted"])
            if res["skipped"]:
                log.info("queue_tick skipped(ガード): %s", res["skipped"])
            if res.get("missed"):
                log.info("queue_tick missed(時刻超過→承認済みへ失効): %s", res["missed"])
    except XClientError as e:
        log.warning("queue_tick: X資格情報未設定 (%s)", e)
    except Exception:
        log.exception("queue_tick failed")


def run(poll_seconds: int = 180, queue_seconds: int = 60) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(monitor_tick, "interval", seconds=poll_seconds, id="monitor")
    sched.add_job(queue_tick, "interval", seconds=queue_seconds, id="queue")
    log.info("XAgent daemon 起動 (monitor=%ss, queue=%ss)。Ctrl-Cで停止。", poll_seconds, queue_seconds)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("停止しました。")
