"""ジョブランナー(/jobs)と未捕捉例外のJSON 500変換のテスト。

生成系(quote-from-url / run-once / recast)は job_id を即返し、フロントが
/jobs/{id} をポーリングする。未捕捉例外は CORS ヘッダ付きの JSON 500 になる
(プレーン500はクロスオリジンで「Failed to fetch」に化けるため)。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from xagent.api.jobs import start_job
from xagent.api.main import app
from tests.test_api import client, wait_job  # noqa: F401 (fixture)


def test_job_success_roundtrip(client):
    job_id = start_job(lambda progress: {"answer": 42})
    j = wait_job(client, _FakeResp(job_id))
    assert j["status"] == "done"
    assert j["result"] == {"answer": 42}


def test_job_error_returns_message(client):
    def _boom(progress) -> dict:
        raise RuntimeError("AI生成が240秒でタイムアウトしました。")

    job_id = start_job(_boom)
    j = wait_job(client, _FakeResp(job_id))
    assert j["status"] == "error"
    assert "タイムアウト" in j["error"]


def test_job_progress_and_elapsed_visible_while_running(client):
    """実行中のジョブは progress(何をしているか)と elapsed_seconds(経過秒)を返す。

    無表示の待ちはユーザーにはハング/エラーと区別がつかないため、
    フロントがポーリングで進捗を表示できることを保証する。
    """
    import threading
    import time

    started = threading.Event()
    release = threading.Event()

    def _slow(progress) -> dict:
        progress("候補収集中: リスト「絡み」 3/120人")
        started.set()
        release.wait(5)
        return {"ok": True}

    job_id = start_job(_slow)
    try:
        assert started.wait(5)
        for _ in range(100):  # progress の反映を最大5秒待つ
            r = client.get(f"/jobs/{job_id}").json()
            if r["progress"]:
                break
            time.sleep(0.05)
        assert r["status"] == "running"
        assert r["progress"] == "候補収集中: リスト「絡み」 3/120人"
        assert isinstance(r["elapsed_seconds"], int)
        assert r["elapsed_seconds"] >= 0
    finally:
        release.set()
    j = wait_job(client, _FakeResp(job_id))
    assert j["status"] == "done"


def test_job_unknown_id_404(client):
    r = client.get("/jobs/deadbeef")
    assert r.status_code == 404
    assert "再起動" in r.json()["detail"]


def test_monitor_run_once_returns_job(client):
    """監視1回実行はジョブ化され、結果(生成件数)はジョブ経由で返る。"""
    j = wait_job(client, client.post("/monitor/run-once?limit=1"))
    assert j["status"] == "done"
    assert "reply_suggestions" in j["result"]


def test_uncaught_exception_becomes_json_500_with_cors(client):
    """未捕捉例外はプレーン500ではなく、CORSヘッダ付き JSON 500 で返る。"""
    from xagent.api.deps import get_formatter

    class _BrokenFormatter:
        def format_post(self, *a, **k):
            raise RuntimeError("claude CLI が失敗しました (exit=1): boom")

    app.dependency_overrides[get_formatter] = lambda: _BrokenFormatter()
    try:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/compose",
            json={"text": "テスト"},
            headers={"Origin": "http://localhost:5180"},
        )
        assert r.status_code == 500
        assert "claude CLI" in r.json()["detail"]
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5180"
    finally:
        app.dependency_overrides[get_formatter] = lambda: __import__(
            "tests.conftest", fromlist=["FakeFormatter"]
        ).FakeFormatter()


class _FakeResp:
    """wait_job に直接 job_id を渡すための薄いレスポンス互換。"""

    status_code = 200
    text = ""

    def __init__(self, job_id: str):
        self._job_id = job_id

    def json(self) -> dict:
        return {"job_id": self._job_id}
