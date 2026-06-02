"""型API(/templates)のテスト。db_session を in-memory に差し替える。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from xagent.api.deps import db_session
from xagent.api.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[db_session] = _session
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def test_templates_crud_flow(client):
    # 作成(post)
    r = client.post("/templates", json={"name": "型1", "kind": "post", "body": "本文1"})
    assert r.status_code == 200
    t1 = r.json()
    assert t1["name"] == "型1" and t1["kind"] == "post" and t1["active"] is False

    # 一覧 / kind フィルタ
    client.post("/templates", json={"name": "リプ型", "kind": "reply", "body": "R"})
    assert len(client.get("/templates").json()) == 2
    assert len(client.get("/templates?kind=post").json()) == 1

    # 更新
    r = client.patch(f"/templates/{t1['id']}", json={"body": "本文1改"})
    assert r.json()["body"] == "本文1改"

    # 既定化(activate)
    r = client.post(f"/templates/{t1['id']}/activate")
    assert r.json()["active"] is True

    # 削除
    assert client.delete(f"/templates/{t1['id']}").status_code == 204
    assert len(client.get("/templates?kind=post").json()) == 0


def test_update_and_delete_missing_404(client):
    assert client.patch("/templates/999", json={"name": "x"}).status_code == 404
    assert client.post("/templates/999/activate").status_code == 404
    assert client.delete("/templates/999").status_code == 404


def test_create_active_sets_default(client):
    r = client.post("/templates", json={"name": "既定型", "kind": "post", "body": "B", "active": True})
    assert r.status_code == 200
    assert r.json()["active"] is True
