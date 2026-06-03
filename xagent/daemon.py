"""常駐デーモン。今はMacローカルで動かす(将来クラウド24h)。

- 監視ティック: メンション/絡み対象をポーリングし、返信案・絡み案を下書き生成。新規が出たら通知。
- キュー ティック: 予約/最適時間の到来分を投稿(承認＋頻度ガードを通過したもののみ)。

各ティックは例外を握って継続し、デーモンを止めない(失敗はstdoutへ)。
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from .db import get_session, init_db
from .formatter import Formatter
from .monitor import get_monitor_settings, run_once
from .notify import notify
from .scheduler import process_due_queue
from .x_client import XClient, XClientError

log = logging.getLogger("xagent.daemon")


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


def queue_tick() -> None:
    # 予約投稿の発火は常時動かす(ユーザー方針: 予約投稿は止めない)。
    # 全投稿の緊急停止は config.posting_enabled が、個別の制限帯/頻度ガードは process_due_queue が担う。
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
