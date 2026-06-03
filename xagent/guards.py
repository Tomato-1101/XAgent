"""ポリシーガード。X規約・凍結リスクに対する安全弁。

設計の背骨(調査で確定):
- 完全自動のいいね/フォロー/RT/一斉リプライは禁止 → 本システムは自動エンゲージしない。
- 投稿は「承認済み」のものだけ送信する(承認ゲート)。
- 投稿頻度は自然な範囲に抑える(既定 1日10件まで・連投間隔300秒・ハード上限100件)。

これらは外部API無しで決定論的に判定できるため、単体テストの対象とする。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .models import DraftStatus


class PolicyViolation(Exception):
    """承認なし送信など、規約・安全方針に反する操作を弾く。"""


class BlackoutViolation(PolicyViolation):
    """制限時間帯(平日の指定帯)に、override なしで書き込もうとしたときに弾く。

    PolicyViolation のサブクラスなので、スケジューラの据え置きやルートの409処理に乗る。
    UI 側は事前に blackout/status を確認して二段階確認を出すため、これは最後の砦。
    """


class PostTrigger(str, Enum):
    """投稿の発火経路。投稿を許すのはこの2つだけ(認証 or 予約)。"""

    MANUAL = "manual"        # 人の明示操作(CLI/UIの投稿ボタン)= 認証
    SCHEDULED = "scheduled"  # 予約(scheduled_at)の到来でスケジューラが発火 = 予約


# --- 承認ゲート(状態遷移) ---------------------------------------------------

# CANCELED(取消/ゴミ箱)は未投稿のどの状態からでも入れる。投稿済みからは不可。
# CANCELED からは DRAFT へ戻せる(ゴミ箱からの復元)。
ALLOWED_TRANSITIONS: dict[DraftStatus, set[DraftStatus]] = {
    DraftStatus.DRAFT: {DraftStatus.APPROVED, DraftStatus.REJECTED, DraftStatus.CANCELED},
    DraftStatus.APPROVED: {DraftStatus.QUEUED, DraftStatus.REJECTED, DraftStatus.CANCELED},
    DraftStatus.QUEUED: {
        DraftStatus.POSTED,
        DraftStatus.QUEUED,    # 予約時刻の変更(再予約)を許可する
        DraftStatus.APPROVED,
        DraftStatus.REJECTED,
        DraftStatus.CANCELED,
    },
    DraftStatus.POSTED: set(),
    DraftStatus.REJECTED: {DraftStatus.CANCELED},
    DraftStatus.CANCELED: {DraftStatus.DRAFT},
}

_POSTABLE = {DraftStatus.APPROVED, DraftStatus.QUEUED}


def can_transition(old: DraftStatus, new: DraftStatus) -> bool:
    return new in ALLOWED_TRANSITIONS.get(old, set())


def ensure_postable(status: DraftStatus) -> None:
    """承認(またはキュー投入済み)でなければ送信を拒否する。"""
    if status not in _POSTABLE:
        raise PolicyViolation(
            f"未承認の下書きは送信できません (status={status.value})。"
            " 先に人間の承認が必要です。"
        )


def ensure_post_authorized(
    status: DraftStatus,
    scheduled_at: datetime | None,
    trigger: PostTrigger,
    now: datetime,
) -> None:
    """投稿を許すのは「認証(人の明示操作)」か「予約(到来済みscheduled_at)」だけ。

    - MANUAL: 人がCLI/UIで明示投稿した = 認証。承認済み/キュー済みのみ可。
    - SCHEDULED: スケジューラ発火。**予約時刻のあるキュー済み**で、かつ**到来済み**のみ可。
      予約時刻が無い/未到来のものは絶対に自動投稿しない(誤爆防止の要)。
    """
    if trigger == PostTrigger.MANUAL:
        ensure_postable(status)
        return

    # SCHEDULED
    if status != DraftStatus.QUEUED:
        raise PolicyViolation(
            f"自動投稿はキュー済みのみ可 (status={status.value})。"
        )
    if scheduled_at is None:
        raise PolicyViolation(
            "予約時刻のないキューは自動投稿しません。"
            "人が明示的に投稿するか、予約時刻を設定してください。"
        )
    if scheduled_at > now:
        raise PolicyViolation(
            f"予約時刻が未到来のため自動投稿しません (scheduled_at={scheduled_at})。"
        )


def ensure_not_blackout(in_blackout: bool, override: bool, reason: str = "") -> None:
    """制限時間帯なら、override(二段階確認済み)が無い限り書き込みを拒否する。

    override=True は「警告を無視→最終確認」を通したことを表す。
    """
    if in_blackout and not override:
        raise BlackoutViolation(
            (reason or "制限時間帯です。") + " 投稿するには警告を無視して最終確認してください。"
        )


# --- 投稿頻度リミッタ --------------------------------------------------------

@dataclass(frozen=True)
class RateLimitConfig:
    max_per_day: int = 10           # 自然な上限(超過は既定で抑止)
    hard_cap_per_day: int = 100     # 安全側のハード上限
    min_interval_seconds: int = 300  # 連投の最小間隔


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    reason: str = ""
    retry_after_seconds: int | None = None


class RateLimiter:
    """直近の投稿時刻(UTC)のリストを元に、いま投稿してよいかを判定する。

    DBに依存させず純粋関数的に判定できるようにし、テスト容易性を確保する。
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()

    def check(
        self, recent_post_times: list[datetime], now: datetime
    ) -> RateDecision:
        cfg = self.config
        day_ago = now - timedelta(days=1)
        in_last_day = [t for t in recent_post_times if t > day_ago]

        if len(in_last_day) >= cfg.hard_cap_per_day:
            return RateDecision(False, f"1日の投稿ハード上限({cfg.hard_cap_per_day})に達しました。")

        if len(in_last_day) >= cfg.max_per_day:
            return RateDecision(
                False,
                f"1日の推奨上限({cfg.max_per_day})に達しました。スパム判定回避のため抑止します。",
            )

        if recent_post_times:
            last = max(recent_post_times)
            elapsed = (now - last).total_seconds()
            if elapsed < cfg.min_interval_seconds:
                return RateDecision(
                    False,
                    f"連投間隔が短すぎます(最小{cfg.min_interval_seconds}秒)。",
                    retry_after_seconds=int(cfg.min_interval_seconds - elapsed),
                )

        return RateDecision(True)
