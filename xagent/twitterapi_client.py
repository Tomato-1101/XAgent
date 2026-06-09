"""twitterapi.io（非公式プロキシ）経由の読み取り専用クライアント。

なぜ存在するか: 公式X APIは高い。他人の投稿の読み取りは自分のアカウントと無関係なので、
安価な twitterapi.io に寄せる。自分のアカウントの「操作」(投稿/RT)は公式のまま
(非公式APIで自分を操作するとBANリスクがあるため)。読み取りは資格情報を使わない＝BAN無関係。

戻り値は tweepy ラッパ(XClient)と同じ dict 形に正規化し、XClient が読み取りメソッドの
バックエンドとして差し替えられるようにする。失敗時は TwitterApiIoError を送出し、
XClient 側が公式APIにフォールバックする(本クライアントは握り潰さない)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import httpx

BASE_URL = "https://api.twitterapi.io"
_MAX_PAGES = 10  # ページネーションの安全弁(無限ループ防止)


class TwitterApiIoError(Exception):
    """twitterapi.io 呼び出しの失敗(HTTP/ネットワーク/応答不正/APIエラー)。"""


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_created_at(value: Any) -> datetime | None:
    """twitterapi.io の createdAt 文字列を datetime に。XAgent下流は datetime を前提とする。"""
    if not isinstance(value, str) or not value:
        return None
    # Xの旧来形式 "Wed Oct 10 20:19:24 +0000 2018"
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class TwitterApiIoClient:
    """読み取り専用。XClient の読取メソッドと同じ dict を返す。"""

    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # --- HTTP ---
    def _get(self, path: str, params: dict) -> dict:
        clean = {k: v for k, v in params.items() if v is not None}
        try:
            # per-call client: XClientがリクエスト毎に生成されるためコネクションを持ち越さない。
            with httpx.Client(
                base_url=self._base_url, timeout=self._timeout,
                headers={"X-API-Key": self._api_key},
            ) as client:
                resp = client.get(path, params=clean)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            raise TwitterApiIoError(f"twitterapi.io {path} 失敗: {e}") from e
        except ValueError as e:  # JSONデコード失敗
            raise TwitterApiIoError(f"twitterapi.io {path} 応答が不正: {e}") from e
        if isinstance(data, dict) and str(data.get("status", "")).lower() == "error":
            raise TwitterApiIoError(
                f"twitterapi.io {path} エラー: {data.get('message') or data.get('msg')}"
            )
        return data if isinstance(data, dict) else {}

    # --- 正規化 ---
    @staticmethod
    def _norm(t: dict) -> dict:
        author = t.get("author") if isinstance(t.get("author"), dict) else {}
        # リポスト判定: 単純RTは retweeted_tweet、引用RTは quoted_tweet に元ツイートのdictが入る
        # (実レスポンスで確認済み)。どちらも本人のオリジナルではないので絡み対象から外す。
        is_repost = bool(t.get("retweeted_tweet")) or bool(t.get("quoted_tweet"))
        return {
            "id": str(t.get("id", "")),
            "text": t.get("text", ""),
            "author_id": str(author.get("id")) if author.get("id") else None,
            "author_handle": author.get("userName"),
            "created_at": _parse_created_at(t.get("createdAt")),  # 元投稿の投稿時刻(表示・鮮度判断用)
            "is_repost": is_repost,
        }

    @staticmethod
    def _extract_tweets(data: dict) -> list:
        """tweets配列の位置はエンドポイントで違う: トップ直下(tweets) と data.tweets の両対応。

        last_tweets は data.tweets にネスト、mentions/advanced_search/tweets はトップ直下。
        """
        if isinstance(data.get("tweets"), list):
            return data["tweets"]
        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("tweets"), list):
            return inner["tweets"]
        return []

    @classmethod
    def _norm_full(cls, t: dict) -> dict:
        base = cls._norm(t)
        base.update(
            {
                "created_at": _parse_created_at(t.get("createdAt")),
                "like_count": _as_int(t.get("likeCount")),
                "retweet_count": _as_int(t.get("retweetCount")),
            }
        )
        return base

    def _paginate(
        self, path: str, params: dict, since_id: str | None,
        max_total: int, norm: Callable[[dict], dict] | None = None,
    ) -> list[dict]:
        """tweets を newest-first 前提で集める。since_id を下回ったら打ち切り、cursorで遡る。

        X の tweet id は時系列単調(snowflake)なので、id降順=新しい順。since_id 超のものだけ採る。
        """
        norm = norm or self._norm
        out: list[dict] = []
        cursor = ""
        for _ in range(_MAX_PAGES):
            data = self._get(path, {**params, "cursor": cursor})
            tweets = self._extract_tweets(data)
            reached_old = False
            for t in tweets:
                n = norm(t)
                if since_id is not None and _as_int(n["id"]) <= _as_int(since_id):
                    # 既読の境界に到達。順序が完全な降順でない場合に備え、同ページ内の
                    # 新しい要素は拾い続け(continue)、ページ送りだけを止める。
                    reached_old = True
                    continue
                out.append(n)
                if len(out) >= max_total:
                    return out
            if reached_old or not data.get("has_next_page") or not data.get("next_cursor"):
                break
            cursor = data["next_cursor"]
        return out

    # --- 公開: 読み取り(XClientと同じ戻り値形) ---
    def get_tweet(self, tweet_id: str) -> dict | None:
        data = self._get("/twitter/tweets", {"tweet_ids": str(tweet_id)})
        tweets = self._extract_tweets(data)
        return self._norm(tweets[0]) if tweets else None

    def get_user_by_username(self, username: str) -> dict | None:
        data = self._get("/twitter/user/info", {"userName": username.lstrip("@")})
        u = data.get("data") if isinstance(data.get("data"), dict) else data
        uid = u.get("id") or u.get("userId")
        if not uid:
            return None
        return {
            "id": str(uid),
            "username": u.get("userName") or u.get("screen_name") or username.lstrip("@"),
        }

    def get_user_timeline(self, user_id: str, since_id: str | None = None) -> list[dict]:
        return self._paginate(
            "/twitter/user/last_tweets", {"userId": str(user_id)}, since_id, max_total=100
        )

    def search_recent(self, query: str, since_id: str | None = None) -> list[dict]:
        return self._paginate(
            "/twitter/tweet/advanced_search",
            {"query": query, "queryType": "Latest"}, since_id, max_total=100,
        )

    def get_mentions(self, handle: str, since_id: str | None = None) -> list[dict]:
        return self._paginate(
            "/twitter/user/mentions", {"userName": handle.lstrip("@")}, since_id, max_total=100
        )

    def get_user_tweets_full(self, user_id: str, max_total: int = 200) -> list[dict]:
        return self._paginate(
            "/twitter/user/last_tweets", {"userId": str(user_id)},
            since_id=None, max_total=max_total, norm=self._norm_full,
        )

    def get_following(self, user_name: str, max_total: int = 100) -> list[dict]:
        # twitterapi.io の followings は userName 必須(userId だと空が返る)。
        out: list[dict] = []
        cursor = ""
        for _ in range(_MAX_PAGES):
            data = self._get(
                "/twitter/user/followings",
                {"userName": user_name.lstrip("@"), "cursor": cursor},
            )
            users = data.get("followings") or data.get("users") or []
            for u in users:
                out.append({"id": str(u.get("id", "")), "username": u.get("userName")})
                if len(out) >= max_total:
                    return out
            if not data.get("has_next_page") or not data.get("next_cursor"):
                break
            cursor = data["next_cursor"]
        return out

    @staticmethod
    def _norm_member(u: dict) -> dict:
        """リストメンバー(=ユーザー)を XClient と同じプロフィールdictに正規化。"""
        followers = u.get("followers")
        if followers is None:
            followers = u.get("followersCount") or u.get("followers_count")
        return {
            "id": str(u.get("id", "")),
            "username": u.get("userName") or u.get("screen_name"),
            "name": u.get("name"),
            "description": u.get("description") or "",
            "profile_image_url": u.get("profilePicture") or u.get("profile_image_url"),
            "followers_count": _as_int(followers),
        }

    def get_list_members(self, list_id: str, max_total: int = 100) -> list[dict]:
        # 公式 XClient.get_list_members と同じプロフィールdictを返す。メンバーは 20件/頁・cursor。
        out: list[dict] = []
        cursor = ""
        for _ in range(_MAX_PAGES):
            data = self._get(
                "/twitter/list/members",
                {"list_id": str(list_id), "cursor": cursor},
            )
            members = data.get("members") or data.get("users") or []
            for u in members:
                out.append(self._norm_member(u))
                if len(out) >= max_total:
                    return out
            if not data.get("has_next_page") or not data.get("next_cursor"):
                break
            cursor = data["next_cursor"]
        return out
