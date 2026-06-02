from xagent.text import (
    FOLD_THRESHOLD_WEIGHTED,
    POST_LIMIT_CHARS,
    POST_LIMIT_WEIGHTED,
    char_length,
    exceeds_fold,
    split_into_thread,
    weighted_length,
)


def test_weighted_length_latin_is_one():
    assert weighted_length("hello") == 5


def test_weighted_length_japanese_is_two():
    assert weighted_length("あいう") == 6  # 各2


def test_weighted_length_mixed():
    # 'a'(1) + ' '(1) + 'あ'(2) = 4
    assert weighted_length("a あ") == 4


def test_fold_threshold_boundary():
    just_fit = "あ" * (FOLD_THRESHOLD_WEIGHTED // 2)      # 加重280
    over = "あ" * (FOLD_THRESHOLD_WEIGHTED // 2 + 1)       # 加重282
    assert exceeds_fold(just_fit) is False
    assert exceeds_fold(over) is True


def test_split_short_text_single_segment():
    assert split_into_thread("短い投稿です。") == ["短い投稿です。"]


def test_split_long_text_into_thread_within_limit():
    text = "".join(f"これは文番号{i}の内容です。" for i in range(40))
    segs = split_into_thread(text)
    assert len(segs) > 1
    for s in segs:
        assert weighted_length(s) <= POST_LIMIT_WEIGHTED


def test_split_hard_splits_single_long_sentence():
    # 句点無しの超長文(加重 > 280)は強制分割される
    text = "あ" * 300  # 加重600
    segs = split_into_thread(text)
    assert len(segs) >= 2
    for s in segs:
        assert weighted_length(s) <= POST_LIMIT_WEIGHTED
    assert "".join(segs) == text


def test_numbering_added_for_multi_segment():
    text = "あ" * 300
    segs = split_into_thread(text, add_numbering=True)
    assert segs[0].endswith(f"(1/{len(segs)})")


# --- 字数(140字)基準の既定動作 ---

def test_char_length_counts_codepoints():
    assert char_length("あいう") == 3
    assert char_length("abc123") == 6


def test_fold_threshold_is_140_chars():
    assert exceeds_fold("あ" * 140) is False
    assert exceeds_fold("あ" * 141) is True
    # Latin も「字数」でカウントする(140字を超えれば折りたたみ扱い)
    assert exceeds_fold("a" * 141) is True


def test_split_default_limit_is_140_chars():
    text = "あ" * 280
    segs = split_into_thread(text)
    assert len(segs) >= 2
    for s in segs:
        assert char_length(s) <= POST_LIMIT_CHARS
    assert "".join(segs) == text


def test_split_latin_over_140_is_chunked():
    # 旧仕様(加重)では1ツイートだったLatin 200字も、字数基準で分割される
    text = "a" * 200
    segs = split_into_thread(text)
    assert len(segs) == 2
    for s in segs:
        assert char_length(s) <= POST_LIMIT_CHARS
