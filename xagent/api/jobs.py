"""長時間のAI生成をHTTP同期から切り離す簡易ジョブランナー。

なぜ: 生成系(引用案・監視1回実行・型切替)は Claude CLI 待ちで数十秒〜15分かかり、
同期リクエストのままだとブラウザ fetch の上限(Chrome 約300秒)・CLIタイムアウト(240秒)・
worker 再起動(--reload / post-commit kickstart)のどれかに当たって
「TypeError: Failed to fetch」で死ぬ。生成はサーバ側スレッドで実行し、
フロントは即返る job_id で /jobs/{id} をポーリングして結果を受け取る。

ジョブはプロセス内メモリ保持(worker 再起動で消える)。結果の実体(下書き)は DB に
入るので永続化はしない。ポーリングが 404 を見たら「サーバ再起動で中断」と表示する。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from .deps import require_api_token

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_token)])

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_TTL_SECONDS = 600  # 完了後にこの時間が過ぎたジョブは次の start_job 時に掃除する


def start_job(fn: Callable[[], dict]) -> str:
    """fn() を別スレッドで実行して job_id を返す。fn は JSON 化可能な dict を返すこと。"""
    job_id = uuid.uuid4().hex
    with _lock:
        now = time.monotonic()
        for jid in [j for j, v in _jobs.items() if v["done_at"] and now - v["done_at"] > _TTL_SECONDS]:
            del _jobs[jid]
        _jobs[job_id] = {"status": "running", "result": None, "error": None, "done_at": None}

    def _run() -> None:
        try:
            result = fn()
        except Exception as e:  # ジョブの失敗は status=error としてフロントに渡す
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
        return {"job_id": job_id, "status": job["status"], "result": job["result"], "error": job["error"]}
