"""通知(任意)。承認待ちが出たことをmacOS通知で知らせる。

macOS以外・通知不可環境では黙ってno-opにする(運用を止めない)。
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def notify(title: str, message: str) -> bool:
    """通知を出せたら True。出せなくても例外は投げない。"""
    if sys.platform != "darwin":
        return False
    osascript = shutil.which("osascript")
    if not osascript:
        return False
    # AppleScript文字列に渡すためエスケープ。バックスラッシュ→二重引用符の順(逆にすると
    # 直前に付けた \ をさらにエスケープしてしまい、末尾が \ の本文で構文エラーになる)。
    safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    try:
        subprocess.run([osascript, "-e", script], check=False, timeout=5)
        return True
    except Exception:
        return False
