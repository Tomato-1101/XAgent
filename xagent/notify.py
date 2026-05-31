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
    # AppleScript文字列に渡すため二重引用符をエスケープ
    safe_msg = message.replace('"', '\\"')
    safe_title = title.replace('"', '\\"')
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    try:
        subprocess.run([osascript, "-e", script], check=False, timeout=5)
        return True
    except Exception:
        return False
