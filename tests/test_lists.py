"""create_list_from_handles のロジック(解決→作成→追加・失敗集計・ハンドル正規化)のテスト。"""

from xagent import lists as lists_mod


class _FakeX:
    def __init__(self, users, resolve_errors=None, add_errors=None):
        self._users = users                       # handle(@なし) -> {id,...} or None
        self._resolve_errors = resolve_errors or set()
        self._add_errors = add_errors or set()    # 追加で失敗させる user_id 集合
        self.created = None
        self.added = []

    def get_user_by_username(self, handle):
        h = handle.lstrip("@")
        if h in self._resolve_errors:
            raise RuntimeError("解決API失敗")
        return self._users.get(h)

    def create_list(self, name, description=None, private=True):
        self.created = {"name": name, "description": description, "private": private}
        return "5555"

    def add_list_member(self, list_id, user_id):
        if user_id in self._add_errors:
            raise RuntimeError("追加API失敗")
        self.added.append((list_id, user_id))


def test_all_resolved_added():
    x = _FakeX({"jack": {"id": "1"}, "alice": {"id": "2"}})
    out = lists_mod.create_list_from_handles(
        x, "L", ["@jack", "alice"], description="d", private=False
    )
    assert out["list_id"] == "5555"
    assert out["url"] == "https://x.com/i/lists/5555"
    assert out["added"] == ["jack", "alice"]
    assert out["skipped"] == []
    assert x.created == {"name": "L", "description": "d", "private": False}
    assert x.added == [("5555", "1"), ("5555", "2")]


def test_unresolvable_handle_skipped():
    x = _FakeX({"jack": {"id": "1"}, "ghost": None})
    out = lists_mod.create_list_from_handles(x, "L", ["jack", "ghost"])
    assert out["added"] == ["jack"]
    assert [s["handle"] for s in out["skipped"]] == ["ghost"]
    assert "解決不可" in out["skipped"][0]["reason"]


def test_resolve_exception_skipped_and_continues():
    x = _FakeX({"jack": {"id": "1"}}, resolve_errors={"boom"})
    out = lists_mod.create_list_from_handles(x, "L", ["boom", "jack"])
    assert out["added"] == ["jack"]                       # 例外でも残りは続行
    assert [s["handle"] for s in out["skipped"]] == ["boom"]
    assert "解決失敗" in out["skipped"][0]["reason"]


def test_add_failure_skipped_and_continues():
    x = _FakeX({"jack": {"id": "1"}, "alice": {"id": "2"}}, add_errors={"2"})
    out = lists_mod.create_list_from_handles(x, "L", ["jack", "alice"])
    assert out["added"] == ["jack"]                       # alice の追加だけ失敗
    assert [s["handle"] for s in out["skipped"]] == ["alice"]
    assert "追加失敗" in out["skipped"][0]["reason"]


def test_empty_description_passed_as_none():
    x = _FakeX({"jack": {"id": "1"}})
    lists_mod.create_list_from_handles(x, "L", ["jack"])  # description 省略
    assert x.created["description"] is None               # "" は None に正規化


def test_normalize_dedup_and_at_strip():
    assert lists_mod.normalize_handles(
        ["@Jack", "jack", " alice ", "", "  ", "JACK"]
    ) == ["Jack", "alice"]


def test_read_handle_lines_skips_comments_and_blanks():
    text = "# === カテゴリ ===\n@jack\n\n  \n@alice\n# 末尾コメント\nbob\n"
    assert lists_mod.read_handle_lines(text) == ["@jack", "@alice", "bob"]
