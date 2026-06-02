import pytest

from xagent.guards import PolicyViolation
from xagent.media import classify, validate_media_set


def test_classify_by_extension():
    assert classify("media/a.JPG") == "image"
    assert classify("media/a.png") == "image"
    assert classify("media/a.gif") == "image"
    assert classify("media/a.mp4") == "video"
    assert classify("media/a.mov") == "video"
    assert classify("media/a.txt") == "other"


def test_validate_none_and_empty_ok():
    validate_media_set(None)
    validate_media_set([])


def test_validate_four_images_ok():
    validate_media_set(["a.jpg", "b.png", "c.webp", "d.gif"])


def test_validate_five_images_rejected():
    with pytest.raises(PolicyViolation):
        validate_media_set(["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"])


def test_validate_one_video_ok():
    validate_media_set(["clip.mp4"])


def test_validate_two_videos_rejected():
    with pytest.raises(PolicyViolation):
        validate_media_set(["a.mp4", "b.mov"])


def test_validate_mixed_image_and_video_rejected():
    with pytest.raises(PolicyViolation):
        validate_media_set(["a.jpg", "b.mp4"])


def test_validate_unknown_type_rejected():
    with pytest.raises(PolicyViolation):
        validate_media_set(["a.txt"])
