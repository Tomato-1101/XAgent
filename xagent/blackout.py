"""制限時間帯(ブラックアウト)。平日の指定帯は自分の公開書き込みを禁止する安全弁。

- 設定は BlackoutSettings(単一行)。曜日(月=0..日=6)と時間帯(JST)をUIで編集できる。
- is_blackout(now_utc, settings) は外部API不要の純関数 → 単体テスト対象。
- 監視(読み取り)はブロックしない。投稿/リポスト/返信/引用など「自分の書き込み」だけが対象。
- 二段階確認を通した操作は override で投稿できる(判定は guards.ensure_not_blackout)。
"""

from __future__ import annotations

import json
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from .models import BlackoutSettings

_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def get_blackout_settings(session: Session) -> BlackoutSettings:
    """制限帯設定(単一行)。無ければ既定(平日9-12・13-19)で作成する。"""
    row = session.exec(select(BlackoutSettings)).first()
    if row is None:
        row = BlackoutSettings()
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def set_blackout_settings(
    session: Session,
    enabled: bool | None = None,
    weekdays: list[int] | None = None,
    windows: list[list[str]] | None = None,
) -> BlackoutSettings:
    """制限帯設定を更新する(指定項目のみ)。"""
    row = get_blackout_settings(session)
    if enabled is not None:
        row.enabled = enabled
    if weekdays is not None:
        # 0..6 のみ・重複排除・昇順で保存
        clean = sorted({int(d) for d in weekdays if 0 <= int(d) <= 6})
        row.weekdays_json = json.dumps(clean)
    if windows is not None:
        # 妥当な "HH:MM" 形式のみ保存(不正値は弾く)
        clean_w = [[a, b] for a, b in windows if _parse_hhmm(a) and _parse_hhmm(b)]
        row.windows_json = json.dumps(clean_w)
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _parse_hhmm(s: str) -> time | None:
    try:
        h, m = s.split(":")
        return time(hour=int(h), minute=int(m))
    except (ValueError, AttributeError):
        return None


def parse_weekdays(row: BlackoutSettings) -> set[int]:
    try:
        return {int(d) for d in json.loads(row.weekdays_json or "[]")}
    except (json.JSONDecodeError, ValueError, TypeError):
        return set()


def parse_windows(row: BlackoutSettings) -> list[tuple[time, time]]:
    out: list[tuple[time, time]] = []
    try:
        raw = json.loads(row.windows_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return out
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        a, b = _parse_hhmm(pair[0]), _parse_hhmm(pair[1])
        if a and b:
            out.append((a, b))
    return out


def is_blackout(
    now_utc: datetime,
    settings_row: BlackoutSettings,
    tz_name: str = "Asia/Tokyo",
) -> tuple[bool, str]:
    """now(naive UTC)が制限帯か判定し、(bool, 理由) を返す。

    無効・対象外曜日(土日など)・時間帯外 なら (False, "")。
    対象曜日かつ時間帯内なら (True, "平日 月 09:00–12:00 は制限時間帯です")。
    """
    if not settings_row.enabled:
        return False, ""
    tz = ZoneInfo(tz_name)
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    if local.weekday() not in parse_weekdays(settings_row):
        return False, ""
    cur = local.time()
    for start, end in parse_windows(settings_row):
        if start <= cur < end:
            wd = _WEEKDAY_JA[local.weekday()]
            return (
                True,
                f"{wd}曜 {start.strftime('%H:%M')}–{end.strftime('%H:%M')} は制限時間帯です。",
            )
    return False, ""
