"""アップロードされたメディア(画像/動画)の保存と検証。

ブラウザから受け取ったファイルをローカルの media_dir に保存し、保持したパスを下書きに記録する。
実際の X へのアップロードは投稿直前に x_client.upload_media が行う(media_id の失効を避けるため)。

X 仕様の制約(本ツールで強制):
- 画像は1投稿あたり最大4枚。
- 動画は1個まで。GIF も1個まで。
- 画像と動画(GIF)の混在は不可。
"""

from __future__ import annotations

import os
import uuid

from .config import get_settings
from .guards import PolicyViolation

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}

MAX_IMAGES = 4
MAX_VIDEOS = 1
MAX_BYTES = 25 * 1024 * 1024  # 25MB(暫定上限)


def media_dir() -> str:
    d = get_settings().media_dir
    os.makedirs(d, exist_ok=True)
    return d


def classify(path: str) -> str:
    """拡張子から種別を返す: image / video / other。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


def is_allowed_filename(filename: str) -> bool:
    ext = os.path.splitext(filename or "")[1].lower()
    return ext in IMAGE_EXTS or ext in VIDEO_EXTS


def save_bytes(filename: str, data: bytes) -> str:
    """アップロード内容を保存し、保持パス(例: "media/<uuid>.jpg")を返す。"""
    ext = os.path.splitext(filename)[1].lower()
    name = f"{uuid.uuid4().hex}{ext}"
    full = os.path.join(media_dir(), name)
    with open(full, "wb") as f:
        f.write(data)
    # DBには相対パスを保持(投稿時に open、StaticFiles で配信)
    return os.path.join(get_settings().media_dir, name)


def validate_media_set(paths: list[str] | None) -> None:
    """1投稿に添付するメディアの組み合わせを X 仕様に照らして検証する。"""
    if not paths:
        return
    images = [p for p in paths if classify(p) == "image"]
    videos = [p for p in paths if classify(p) == "video"]
    others = [p for p in paths if classify(p) == "other"]
    if others:
        raise PolicyViolation("対応していないメディア形式です(画像/動画のみ)。")
    if videos and images:
        raise PolicyViolation("画像と動画は同時に添付できません(Xの仕様)。")
    if len(images) > MAX_IMAGES:
        raise PolicyViolation(f"画像は最大{MAX_IMAGES}枚までです。")
    if len(videos) > MAX_VIDEOS:
        raise PolicyViolation(f"動画は最大{MAX_VIDEOS}個までです。")
