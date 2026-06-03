"""常駐スケジューラ(デーモン)のティック挙動。

ユーザー方針:
- 予約投稿(queue_tick)は auto_post_enabled に関係なく常時 process_due_queue を呼ぶ
  (全投稿の緊急停止は config.posting_enabled が担う)。
- 絡み案生成(monitor_tick)は auto_monitor_enabled が False のとき X クライアントすら作らず即return
  (APIを消費しない)。True のときだけ run_once を回す。
"""

from contextlib import contextmanager

from xagent import daemon, monitor


def _patch_session(monkeypatch, session):
    """daemon.get_session() がテスト用セッションを返すようにする(閉じない)。"""

    @contextmanager
    def _fake_get_session():
        yield session

    monkeypatch.setattr(daemon, "get_session", _fake_get_session)


def test_queue_tick_runs_even_when_auto_post_disabled(session, fake_x, monkeypatch):
    monitor.set_monitor_settings(session, auto_post_enabled=False)
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(daemon.XClient, "from_settings", staticmethod(lambda: fake_x))
    called = {}

    def _fake_process(s, x, **kw):
        called["yes"] = True
        return {"posted": [], "skipped": [], "errors": [], "missed": []}

    monkeypatch.setattr(daemon, "process_due_queue", _fake_process)
    daemon.queue_tick()
    assert called.get("yes") is True  # auto_post_enabled=False でも予約発火は動く


def test_monitor_tick_skips_when_auto_monitor_disabled(session, monkeypatch):
    monitor.set_monitor_settings(session, auto_monitor_enabled=False)
    _patch_session(monkeypatch, session)

    def _boom():
        raise AssertionError("auto_monitor_enabled=False の時は X クライアントを作ってはいけない")

    monkeypatch.setattr(daemon.XClient, "from_settings", staticmethod(_boom))
    daemon.monitor_tick()  # 例外なく即returnすれば合格(APIを一切叩かない)


def test_monitor_tick_runs_when_enabled(session, fake_x, monkeypatch):
    monitor.set_monitor_settings(session, auto_monitor_enabled=True)
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(daemon.XClient, "from_settings", staticmethod(lambda: fake_x))
    monkeypatch.setattr(daemon, "Formatter", lambda: object())
    monkeypatch.setattr(daemon, "notify", lambda *a, **k: None)
    seen = {}

    def _fake_run_once(s, x, fmt, me_id):
        seen["me"] = me_id
        return {"reply_suggestions": 1, "quote_suggestions": 0}

    monkeypatch.setattr(daemon, "run_once", _fake_run_once)
    daemon.monitor_tick()
    assert seen.get("me") == "1"  # fake_x.get_me()["id"]
