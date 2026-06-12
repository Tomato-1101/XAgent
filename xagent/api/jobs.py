"""長時間のAI生成をHTTP同期から切り離す簡易ジョブランナー。

なぜ: 生成系(引用案・監視1回実行・型切替)は Claude CLI 待ちで数十秒〜15分かかり、
同期リクエストのままだとブラウザ fetch の上限(Chrome 約300秒)・CLIタイムアウト(240秒)・
worker 再起動(--reload / post-commit kickstart)のどれかに当たって
「TypeError: Failed to fetch」で死ぬ。生成はサーバ側スレッドで実行し、
フロントは即返る job_id で /jobs/{id} をポーリングして結果を受け取る。

ジョブはプロセス内メモリ保持(worker 再起動で消える)。結果の実体(下書き)は DB に
入るので永続化はしない。ポーリングが 404 を見たら「サーバ再起動で中断」と表示する。

進捗: fn は set_progress("メッセージ") を受け取り、節目で「何をしているか」を更新する。
フロントは progress と elapsed_seconds を表示する(無表示の長い待ちはユーザーには
ハング/エラーと区別がつかないため)。失敗はフロントに渡すだけでなくサーバログにも残す
(画面遷移等でポーリングが切れるとエラーがどこにも記録されず原因調査が不可能になる)。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from .deps import require_api_token

logger = logging.getLogger("xagent.jobs")

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_token)])

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_TTL_SECONDS = 600  # 完了後にこの時間が過ぎたジョブは次の start_job 時に掃除する


def start_job(fn: Callable[[Callable[[str], None]], dict]) -> str:
    """fn(set_progress) を別スレッドで実行して job_id を返す。fn は JSON 化可能な dict を返すこと。"""
    job_id = uuid.uuid4().hex
    with _lock:
        now = time.monotonic()
        for jid in [j for j, v in _jobs.items() if v["done_at"] and now - v["done_at"] > _TTL_SECONDS]:
            del _jobs[jid]
        _jobs[job_id] = {
            "status": "running",
            "progress": None,
            "started_at": now,
            "result": None,
            "error": None,
            "done_at": None,
        }

    def _set_progress(message: str) -> None:
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                job["progress"] = message

    def _run() -> None:
        try:
            result = fn(_set_progress)
        except Exception as e:  # ジョブの失敗は status=error としてフロントに渡す
            logger.exception("ジョブ %s が失敗しました", job_id)
            with _lock:
                _jobs[job_id].update(status="error", error=str(e), done_at=time.monotonic())
        else:
            with _lock:
                _jobs[job_id].update(status="done", result=result, done_at=time.monotonic())

    threading.Thread(target=_run, daemon=True).start()
    return job_id


@router.get("/{job_id}")
def get_job(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "ジョブが見つかりません(サーバ再起動で中断された可能性)。")
        end = job["done_at"] or time.monotonic()
        return {
            "job_id": job_id,
            "status": job["status"],
            "progress": job["progress"],
            "elapsed_seconds": int(end - job["started_at"]),
            "result": job["result"],
            "error": job["error"],
        }
