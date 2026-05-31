"""Xクライアント。tweepy(v2 + v1.1メディア)をラップする。

出力は tweepy のオブジェクトではなく dict に正規化して返す
(サービス層・監視・テストが扱いやすいように)。
ライブ検証にはX Developer Portalで発行した資格情報(OAuth1.0a + Bearer)が必要。
資格情報が無い場合は from_settings() が明示エラーを出す。
"""

from __future__ import annotations

from typing import Any, Protocol

from .config import Settings, get_settings


class XClientError(Exception):
    pass


class TweepyClientLike(Protocol):
    def create_tweet(self, **kwargs: Any) -> Any: ...
    def get_users_mentions(self, id: str, **kwargs: Any) -> Any: ...
    def get_users_tweets(self, id: str, **kwargs: Any) -> Any: ...
    def get_user(self, **kwargs: Any) -> Any: ...
    def get_me(self, **kwargs: Any) -> Any: ...
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

    def __init__(self, client: TweepyClientLike, api_v1: Any | None = None) -> None:
        self._client = client
        self._api_v1 = api_v1

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
        return cls(client, api_v1)

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

    def upload_media(self, path: str) -> str:
        if self._api_v1 is None:
            raise XClientError("メディアアップロードにはOAuth1.0a資格情報が必要です。")
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
        resp = self._client.get_user(username=username)
        data = getattr(resp, "data", None)
        if not data:
            return None
        return {"id": str(data.id), "username": getattr(data, "username", username)}

    def get_mentions(self, user_id: str, since_id: str | None = None) -> list[dict]:
        resp = self._client.get_users_mentions(
            id=user_id, since_id=since_id, tweet_fields=["author_id"]
        )
        return _normalize_tweets(resp)

    def get_user_timeline(self, user_id: str, since_id: str | None = None) -> list[dict]:
        resp = self._client.get_users_tweets(
            id=user_id, since_id=since_id, tweet_fields=["author_id"]
        )
        return _normalize_tweets(resp)

    def search_recent(self, query: str, since_id: str | None = None) -> list[dict]:
        resp = self._client.search_recent_tweets(
            query=query, since_id=since_id, tweet_fields=["author_id"]
        )
        return _normalize_tweets(resp)

    def get_user_tweets_full(self, user_id: str, max_total: int = 200) -> list[dict]:
        """ユーザーの投稿を可能な限り取得(ページネーション)。メトリクス/作成時刻付き。

        プロファイル学習用。max_total で上限を切る(APIコスト/上限対策)。
        """
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
        """ユーザーがフォロー中のアカウント一覧({id, username})。"""
        import tweepy

        out: list[dict] = []
        paginator = tweepy.Paginator(
            self._client.get_users_following, id=user_id, max_results=1000
        )
        for u in paginator.flatten(limit=max_total):
            out.append({"id": str(getattr(u, "id", "")), "username": getattr(u, "username", None)})
        return out
