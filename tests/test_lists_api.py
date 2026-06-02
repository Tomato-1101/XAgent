"""リストAPIルータ(/lists)のテスト。get_x_client をフェイクに差し替える。"""

import pytest
from fastapi.testclient import TestClient

from xagent.api.deps import get_x_client
from xagent.api.main import app
from tests.conftest import FakeXClient


@pytest.fixture
def client():
    fx = FakeXClient(
        users={
            "jack": {"id": "1", "username": "jack"},
            "alice": {"id": "2", "username": "alice"},
        },
        lists=[{"id": "900", "name": "既存", "description": "d",
                "private": True, "member_count": 1}],
        list_members={"900": [{
            "id": "11", "username": "alice", "name": "Alice", "description": "bio",
            "profile_image_url": "http://img", "followers_count": 42,
        }]},
    )
    app.dependency_overrides[get_x_client] = lambda: fx
    c = TestClient(app)
    c.fake_x = fx
    yield c
    app.dependency_overrides.clear()


def test_get_owned_lists(client):
    r = client.get("/lists")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "既存"


def test_get_list_members_with_profile(client):
    r = client.get("/lists/900/members")
    assert r.status_code == 200
    m = r.json()[0]
    assert m["username"] == "alice"
    assert m["name"] == "Alice"
    assert m["followers_count"] == 42


def test_create_list_skips_unresolvable(client):
    r = client.post("/lists", json={"name": "新規", "accounts": ["@jack", "alice", "ghost"]})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "新規"
    assert body["added"] == ["jack", "alice"]               # ghost は解決不可でskip
    assert [s["handle"] for s in body["skipped"]] == ["ghost"]
    kinds = [c[0] for c in client.fake_x.list_calls]
    assert kinds.count("create_list") == 1
    assert kinds.count("add_list_member") == 2


def test_update_list(client):
    r = client.patch("/lists/900", json={"name": "改名"})
    assert r.status_code == 204
    assert ("update_list", "900", "改名", None, None) in client.fake_x.list_calls


def test_delete_list(client):
    r = client.delete("/lists/900")
    assert r.status_code == 204
    assert ("delete_list", "900") in client.fake_x.list_calls


def test_add_member_by_handle_resolves_id(client):
    r = client.post("/lists/900/members", json={"handle": "@jack"})
    assert r.status_code == 204
    assert ("add_list_member", "900", "1") in client.fake_x.list_calls


def test_add_member_by_user_id(client):
    r = client.post("/lists/900/members", json={"user_id": "77"})
    assert r.status_code == 204
    assert ("add_list_member", "900", "77") in client.fake_x.list_calls


def test_add_member_requires_handle_or_id(client):
    assert client.post("/lists/900/members", json={}).status_code == 422


def test_add_member_unresolvable_is_404(client):
    assert client.post("/lists/900/members", json={"handle": "ghost"}).status_code == 404


def test_remove_member(client):
    r = client.delete("/lists/900/members/11")
    assert r.status_code == 204
    assert ("remove_list_member", "900", "11") in client.fake_x.list_calls
