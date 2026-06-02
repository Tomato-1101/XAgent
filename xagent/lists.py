"""Xネイティブ「リスト」の作成サービス。

ハンドルの一覧から公式X APIでリストを作り、解決できたアカウントを一括追加する。
CLI / MCP / API(POST /lists) で共用する(ロジック一元化)。書き込みは XClient 経由＝公式APIのみ。

ハンドルの解決(読み取り)は安価な twitterapi.io 優先(XClient 内部でフォールバック)。
1件の失敗(解決不可・追加失敗)で全体を止めず、skipped に理由付きで集計して継続する
(ユーザーは雑多な一覧を渡す前提＝一部の無効ハンドルで全体が落ちると使い物にならないため)。
"""

from __future__ import annotations

from collections.abc import Iterable


def normalize_handles(handles: Iterable[str]) -> list[str]:
    """@除去・空白除去・空行除去・順序保持の重複除去(大小無視)。"""
    seen: set[str] = set()
    out: list[str] = []
    for h in handles:
        if not h:
            continue
        h = h.strip().lstrip("@").strip()
        if not h or h.lower() in seen:
            continue
        seen.add(h.lower())
        out.append(h)
    return out


def create_list_from_handles(
    x,
    name: str,
    handles: Iterable[str],
    description: str = "",
    private: bool = True,
) -> dict:
    """リストを作成し、解決できたハンドルをメンバー追加して結果を返す。

    戻り値: {list_id, url, name, added: [handle], skipped: [{handle, reason}]}
    """
    cleaned = normalize_handles(handles)
    added: list[str] = []
    skipped: list[dict] = []

    # 先にハンドル→user_id を解決(読み取り＝安価)。解決不可は skip に計上。
    resolved: list[tuple[str, str]] = []
    for h in cleaned:
        try:
            info = x.get_user_by_username(h)
        except Exception as e:  # noqa: BLE001  バッチ堅牢性: 1件の解決失敗で全体を止めない
            skipped.append({"handle": h, "reason": f"解決失敗: {e}"})
            continue
        if not info or not info.get("id"):
            skipped.append({"handle": h, "reason": "解決不可(存在しない/非公開)"})
            continue
        resolved.append((h, str(info["id"])))

    list_id = x.create_list(name, description=description or None, private=private)

    for h, uid in resolved:
        try:
            x.add_list_member(list_id, uid)
            added.append(h)
        except Exception as e:  # noqa: BLE001  追加失敗(凍結/ブロック/重複等)は skip にして継続
            skipped.append({"handle": h, "reason": f"追加失敗: {e}"})

    return {
        "list_id": list_id,
        "url": f"https://x.com/i/lists/{list_id}",
        "name": name,
        "added": added,
        "skipped": skipped,
    }
