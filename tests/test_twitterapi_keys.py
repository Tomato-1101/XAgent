"""twitterapi.io 読み取りキーAPI(/twitterapi-keys)のテスト。db_session を in-memory に差し替える。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from xagent import twitterapi_keys as keys_mod
from xagent.api.deps import db_session
from xagent.api.main import app
from xagent.twitterapi_client import TwitterApiIoError


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(engine):
    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[db_session] = _session
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def test_create_returns_masked_not_plaintext(client):
    r = client.post("/twitterapi-keys", json={"api_key": "secret-abcdefgh1234", "label": "メイン"})
    assert r.status_code == 200
    row = r.json()
    assert row["label"] == "メイン"
    assert row["enabled"] is True
    assert row["key_masked"].endswith("1234")  # 末尾4文字
    assert "secret" not in row["key_masked"]    # 平文は漏らさない
    assert "api_key" not in row                 # 平文フィールド自体を返さない


def test_empty_key_rejected(client):
    assert client.post("/twitterapi-keys", json={"api_key": "  "}).status_code == 400


def test_list_ordered_by_priority_then_reorder(client):
    client.post("/twitterapi-keys", json={"api_key": "key-aaaa", "label": "A"})
    client.post("/twitterapi-keys", json={"api_key": "key-bbbb", "label": "B"})
    client.post("/twitterapi-keys", json={"api_key": "key-cccc", "label": "C"})
    rows = client.get("/twitterapi-keys").json()
    assert [x["label"] for x in rows] == ["A", "B", "C"]  # 追加順=末尾追加で priority 昇順
    ids = [x["id"] for x in rows]

    # C,A,B の順に並べ替え → priority 0,1,2
    reordered = client.post("/twitterapi-keys/reorder", json={"ids": [ids[2], ids[0], ids[1]]}).json()
    assert [x["label"] for x in reordered] == ["C", "A", "B"]
    assert [x["priority"] for x in reordered] == [0, 1, 2]


def test_update_label_and_toggle_enabled(client):
    kid = client.post("/twitterapi-keys", json={"api_key": "key-zzzz", "label": "旧"}).json()["id"]
    r = client.patch(f"/twitterapi-keys/{kid}", json={"label": "新", "enabled": False})
    assert r.status_code == 200
    assert r.json()["label"] == "新" and r.json()["enabled"] is False


def test_replacing_key_clears_test_status(client, engine):
    kid = client.post("/twitterapi-keys", json={"api_key": "key-old0", "label": "X"}).json()["id"]
    # 疎通成功を記録しておく
    from xagent.models import TwitterApiKey, _utcnow

    with Session(engine) as s:
        row = s.get(TwitterApiKey, kid)
        row.last_ok_at = _utcnow()
        s.add(row)
        s.commit()
    assert client.get("/twitterapi-keys").json()[0]["last_ok_at"] is not None
    # キーを差し替えると過去の疎通結果は無効化される(古い成功表示を残さない)
    r = client.patch(f"/twitterapi-keys/{kid}", json={"api_key": "key-new9"})
    assert r.json()["last_ok_at"] is None
    assert r.json()["key_masked"].endswith("new9")


def test_delete(client):
    kid = client.post("/twitterapi-keys", json={"api_key": "key-dele"}).json()["id"]
    assert client.delete(f"/twitterapi-keys/{kid}").status_code == 204
    assert client.get("/twitterapi-keys").json() == []
    assert client.delete(f"/twitterapi-keys/{kid}").status_code == 404


def test_test_endpoint_records_ok_and_error(client, monkeypatch):
    kid = client.post("/twitterapi-keys", json={"api_key": "key-prob"}).json()["id"]

    # 成功: get_user_by_username が例外を投げない → last_ok_at が入り last_error は None
    class _OK:
        def __init__(self, key):
            pass

        def get_user_by_username(self, handle):
            return {"id": "1", "username": handle}

    monkeypatch.setattr(keys_mod, "TwitterApiIoClient", _OK)
    ok = client.post(f"/twitterapi-keys/{kid}/test").json()
    assert ok["last_ok_at"] is not None and ok["last_error"] is None

    # 失敗: TwitterApiIoError → last_error が入る
    class _NG:
        def __init__(self, key):
            pass

        def get_user_by_username(self, handle):
            raise TwitterApiIoError("残高切れ(402)")

    monkeypatch.setattr(keys_mod, "TwitterApiIoClient", _NG)
    ng = client.post(f"/twitterapi-keys/{kid}/test").json()
    assert ng["last_error"] and "402" in ng["last_error"]


def test_test_endpoint_404(client):
    assert client.post("/twitterapi-keys/999/test").status_code == 404
