"""Xクライアント。tweepy(v2 + v1.1メディア)をラップする。

出力は tweepy のオブジェクトではなく dict に正規化して返す
(サービス層・監視・テストが扱いやすいように)。
ライブ検証にはX Developer Portalで発行した資格情報(OAuth1.0a + Bearer)が必要。
資格情報が無い場合は from_settings() が明示エラーを出す。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .config import Settings, get_settings
from .twitterapi_client import TwitterApiIoClient, TwitterApiIoError

logger = logging.getLogger(__name__)


class XClientError(Exception):
    pass


class TweepyClientLike(Protocol):
    def create_tweet(self, **kwargs: Any) -> Any: ...
    def retweet(self, tweet_id: str, **kwargs: Any) -> Any: ...
    def get_users_mentions(self, id: str, **kwargs: Any) -> Any: ...
    def get_users_tweets(self, id: str, **kwargs: Any) -> Any: ...
    def get_user(self, **kwargs: Any) -> Any: ...
    def get_me(self, **kwargs: Any) -> Any: ...
    def get_tweet(self, id: str, **kwargs: Any) -> Any: ...
    def search_recent_tweets(self, query: str, **kwargs: Any) -> Any: ...


def _normalize_tweets(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None) or []
    out = []
    for t in data:
        out.append(
            {
                "id": str(getattr(t, "id", "")),
                "text": getattr(t, "text", ""),
                "author_id": (
                    str(t.author_id) if getattr(t, "author_id", None) else None
                ),
            }
        )
    return out


class XClient:
    """v2クライアント(投稿/読取)とv1.1 API(メディアアップロード)をまとめる。"""

    def __init__(
        self,
        client: TweepyClientLike,
        api_v1: Any | None = None,
        read_backend: TwitterApiIoClient | None = None,
    ) -> None:
        self._client = client
        self._api_v1 = api_v1
        # 読み取りはここがあれば優先(安価な twitterapi.io)。失敗時のみ公式へフォールバック。
        self._read_backend = read_backend
        self._me: dict | None = None  # 自分の識別をキャッシュ(mentions のハンドル解決用)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "XClient":
        settings = settings or get_settings()
        if not (settings.x_api_key and settings.x_api_secret):
            raise XClientError(
                "X APIの資格情報が未設定です。env.example を参照し .env を設定してください。"
            )
        import tweepy

        client = tweepy.Client(
            bearer_token=settings.x_bearer_token,
            consumer_key=settings.x_api_key,
            consumer_secret=settings.x_api_secret,
            access_token=settings.x_access_token,
            access_token_secret=settings.x_access_token_secret,
        )
        api_v1 = None
        if settings.x_access_token and settings.x_access_token_secret:
            auth = tweepy.OAuth1UserHandler(
                settings.x_api_key,
                settings.x_api_secret,
                settings.x_access_token,
                settings.x_access_token_secret,
            )
            api_v1 = tweepy.API(auth)
        # 読み取り用の安価なバックエンド(設定時のみ)。投稿系は常に公式 client を使う。
        read_backend = (
            TwitterApiIoClient(settings.twitterapi_io_key)
            if settings.twitterapi_io_key
            else None
        )
        return cls(client, api_v1, read_backend=read_backend)

    # --- 読み取りルーティング(バックエンド優先 / 失敗時のみ公式へ) ---
    def _read(self, backend_call, official_call, what: str):
        """読み取りは twitterapi.io 優先。TwitterApiIoError のときだけ公式APIで読み直す。"""
        if self._read_backend is None:
            return official_call()
        try:
            return backend_call(self._read_backend)
        except TwitterApiIoError as e:
            logger.warning("twitterapi.io %s 失敗→公式APIにフォールバック: %s", what, e)
            return official_call()

    def _self_handle(self) -> str | None:
        """自分のハンドル。mentions を twitterapi.io で引くのに必要(公式 get_me で一度だけ解決)。"""
        if self._me is None:
            self._me = self.get_me()
        return self._me.get("username")

    # --- 書き込み ---
    def post(
        self,
        text: str,
        in_reply_to_tweet_id: str | None = None,
        quote_tweet_id: str | None = None,
        media_ids: list[str] | None = None,
    ) -> str:
        resp = self._client.create_tweet(
            text=text,
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            quote_tweet_id=quote_tweet_id,
            media_ids=media_ids,
        )
        data = getattr(resp, "data", None) or {}
        tweet_id = data.get("id") if isinstance(data, dict) else getattr(data, "id", None)
        if not tweet_id:
            raise XClientError(f"投稿IDを取得できませんでした: {resp!r}")
        return str(tweet_id)

    def post_thread(
        self,
        segments: list[str],
        in_reply_to_tweet_id: str | None = None,
        media_ids_first: list[str] | None = None,
    ) -> list[str]:
        """スレッド投稿。各セグメントを前の投稿へのリプライとして連結する。"""
        ids: list[str] = []
        prev = in_reply_to_tweet_id
        for i, seg in enumerate(segments):
            media = media_ids_first if i == 0 else None
            tid = self.post(seg, in_reply_to_tweet_id=prev, media_ids=media)
            ids.append(tid)
            prev = tid
        return ids

    def retweet(self, tweet_id: str) -> str:
        """認証ユーザー(自分)で対象ツイートを通常リポスト(RT)する。

        tweepy の Client.retweet は認証ユーザーの user_id を自動で使う。
        返り値は対象 tweet_id(RT自体には新規IDが無いため、リンク用に対象IDを返す)。
        """
        self._client.retweet(tweet_id)
        return str(tweet_id)

    def upload_media(self, path: str) -> str:
        """画像/動画/GIF を X にアップロードして media_id を返す。

        動画・GIF はチャンクアップロード + 非同期処理待ち(tweepy が完了までブロック)。
        """
        import os

        if self._api_v1 is None:
            raise XClientError("メディアアップロードにはOAuth1.0a資格情報が必要です。")
        ext = os.path.splitext(path)[1].lower()
        if ext in {".mp4", ".mov", ".m4v"}:
            media = self._api_v1.media_upload(
                path, chunked=True, media_category="tweet_video"
            )
        elif ext == ".gif":
            media = self._api_v1.media_upload(
                path, chunked=True, media_category="tweet_gif"
            )
        else:
            media = self._api_v1.media_upload(path)
        return str(media.media_id)

    # --- 読み取り ---
    def get_me(self) -> dict:
        resp = self._client.get_me()
        data = getattr(resp, "data", None)
        if not data:
            raise XClientError("get_me に失敗しました。")
        return {"id": str(data.id), "username": getattr(data, "username", None)}

    def get_user_by_username(self, username: str) -> dict | None:
        return self._read(
            lambda b: b.get_user_by_username(username),
            lambda: self._official_get_user_by_username(username),
            "get_user_by_username",
        )

    def _official_get_user_by_username(self, username: str) -> dict | None:
        resp = self._client.get_user(username=username)
        data = getattr(resp, "data", None)
        if not data:
            return None
        return {"id": str(data.id), "username": getattr(data, "username", username)}

    def get_tweet(self, tweet_id: str) -> dict | None:
        """単体ツイートを {id, text, author_id, author_handle} に正規化して返す。

        URL指定の絡み(リプライ/引用)で、元ポスト本文を表示用に取得するのに使う。
        取得不可(削除/権限/不正ID)なら None を返し、呼び出し側はスキップする。
        読み取りは twitterapi.io 優先・失敗時のみ公式。
        """
        return self._read(
            lambda b: b.get_tweet(tweet_id),
            lambda: self._official_get_tweet(tweet_id),
            "get_tweet",
        )

    def _official_get_tweet(self, tweet_id: str) -> dict | None:
        resp = self._client.get_tweet(
            id=tweet_id,
            tweet_fields=["author_id"],
            expansions=["author_id"],
            user_fields=["username"],
        )
        data = getattr(resp, "data", None)
        if not data:
            return None
        includes = getattr(resp, "includes", None) or {}
        users = includes.get("users") if isinstance(includes, dict) else None
        author_handle = getattr(users[0], "username", None) if users else None
        return {
            "id": str(getattr(data, "id", tweet_id)),
            "text": getattr(data, "text", ""),
            "author_id": str(data.author_id) if getattr(data, "author_id", None) else None,
            "author_handle": author_handle,
        }

    def get_mentions(self, user_id: str, since_id: str | None = None) -> list[dict]:
        return self._read(
            lambda b: self._backend_mentions(b, since_id),
            lambda: self._official_get_mentions(user_id, since_id),
            "get_mentions",
        )

    def _backend_mentions(self, backend: TwitterApiIoClient, since_id: str | None) -> list[dict]:
        handle = self._self_handle()
        if not handle:
            raise TwitterApiIoError("自分のハンドルを解決できず mentions を引けません。")
        return backend.get_mentions(handle, since_id)

    def _official_get_mentions(self, user_id: str, since_id: str | None = None) -> list[dict]:
        resp = self._client.get_users_mentions(
            id=user_id, since_id=since_id, tweet_fields=["author_id"]
        )
        return _normalize_tweets(resp)

    def get_user_timeline(self, user_id: str, since_id: str | None = None) -> list[dict]:
        return self._read(
            lambda b: b.get_user_timeline(user_id, since_id),
            lambda: self._official_get_user_timeline(user_id, since_id),
            "get_user_timeline",
        )

    def _official_get_user_timeline(self, user_id: str, since_id: str | None = None) -> list[dict]:
        resp = self._client.get_users_tweets(
            id=user_id, since_id=since_id, tweet_fields=["author_id"]
        )
        return _normalize_tweets(resp)

    def search_recent(self, query: str, since_id: str | None = None) -> list[dict]:
        return self._read(
            lambda b: b.search_recent(query, since_id),
            lambda: self._official_search_recent(query, since_id),
            "search_recent",
        )

    def _official_search_recent(self, query: str, since_id: str | None = None) -> list[dict]:
        resp = self._client.search_recent_tweets(
            query=query, since_id=since_id, tweet_fields=["author_id"]
        )
        return _normalize_tweets(resp)

    def get_user_tweets_full(self, user_id: str, max_total: int = 200) -> list[dict]:
        """ユーザーの投稿を可能な限り取得(ページネーション)。メトリクス/作成時刻付き。

        プロファイル学習用。max_total で上限を切る(APIコスト/上限対策)。
        読み取りは twitterapi.io 優先・失敗時のみ公式。
        """
        return self._read(
            lambda b: b.get_user_tweets_full(user_id, max_total=max_total),
            lambda: self._official_get_user_tweets_full(user_id, max_total=max_total),
            "get_user_tweets_full",
        )

    def _official_get_user_tweets_full(self, user_id: str, max_total: int = 200) -> list[dict]:
        import tweepy

        out: list[dict] = []
        paginator = tweepy.Paginator(
            self._client.get_users_tweets,
            id=user_id,
            max_results=100,
            tweet_fields=["created_at", "public_metrics", "author_id"],
        )
        for t in paginator.flatten(limit=max_total):
            pm = getattr(t, "public_metrics", None)
            pm = pm if isinstance(pm, dict) else {}
            out.append(
                {
                    "id": str(getattr(t, "id", "")),
                    "text": getattr(t, "text", ""),
                    "author_id": str(t.author_id) if getattr(t, "author_id", None) else None,
                    "created_at": getattr(t, "created_at", None),
                    "like_count": int(pm.get("like_count", 0)),
                    "retweet_count": int(pm.get("retweet_count", 0)),
                }
            )
        return out

    def get_following(self, user_id: str, max_total: int = 100) -> list[dict]:
        """ユーザーがフォロー中のアカウント一覧({id, username})。

        読み取りは twitterapi.io 優先・失敗時のみ公式。
        """
        return self._read(
            lambda b: self._backend_following(b, max_total),
            lambda: self._official_get_following(user_id, max_total=max_total),
            "get_following",
        )

    def _backend_following(self, backend: TwitterApiIoClient, max_total: int) -> list[dict]:
        # twitterapi.io の followings は userName 必須。監視は自分のフォローのみ取得するため、
        # 公式 get_me で解決した自分のハンドルで引く。
        handle = self._self_handle()
        if not handle:
            raise TwitterApiIoError("自分のハンドルを解決できず following を引けません。")
        return backend.get_following(handle, max_total=max_total)

    def _official_get_following(self, user_id: str, max_total: int = 100) -> list[dict]:
        import tweepy

        out: list[dict] = []
        paginator = tweepy.Paginator(
            self._client.get_users_following, id=user_id, max_results=1000
        )
        for u in paginator.flatten(limit=max_total):
            out.append({"id": str(getattr(u, "id", "")), "username": getattr(u, "username", None)})
        return out
